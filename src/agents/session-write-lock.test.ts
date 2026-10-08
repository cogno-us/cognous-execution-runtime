import { spawnSync } from "node:child_process";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import { __testing, acquireSessionWriteLock } from "./session-write-lock.js";

describe("acquireSessionWriteLock", () => {
  it("reuses locks across symlinked session paths", async () => {
    if (process.platform === "win32") {
      expect(true).toBe(true);
      return;
    }

    const root = await fs.mkdtemp(path.join(os.tmpdir(), "moltbot-lock-"));
    try {
      const realDir = path.join(root, "real");
      const linkDir = path.join(root, "link");
      await fs.mkdir(realDir, { recursive: true });
      await fs.symlink(realDir, linkDir);

      const sessionReal = path.join(realDir, "sessions.json");
      const sessionLink = path.join(linkDir, "sessions.json");

      const lockA = await acquireSessionWriteLock({ sessionFile: sessionReal, timeoutMs: 500 });
      const lockB = await acquireSessionWriteLock({ sessionFile: sessionLink, timeoutMs: 500 });

      await lockB.release();
      await lockA.release();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("keeps the lock file until the last release", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "moltbot-lock-"));
    try {
      const sessionFile = path.join(root, "sessions.json");
      const lockPath = `${sessionFile}.lock`;

      const lockA = await acquireSessionWriteLock({ sessionFile, timeoutMs: 500 });
      const lockB = await acquireSessionWriteLock({ sessionFile, timeoutMs: 500 });

      await expect(fs.access(lockPath)).resolves.toBeUndefined();
      await lockA.release();
      await expect(fs.access(lockPath)).resolves.toBeUndefined();
      await lockB.release();
      await expect(fs.access(lockPath)).rejects.toThrow();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("reclaims stale lock files", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "moltbot-lock-"));
    try {
      const sessionFile = path.join(root, "sessions.json");
      const lockPath = `${sessionFile}.lock`;
      await fs.writeFile(
        lockPath,
        JSON.stringify({ pid: 123456, createdAt: new Date(Date.now() - 60_000).toISOString() }),
        "utf8",
      );

      const lock = await acquireSessionWriteLock({ sessionFile, timeoutMs: 500, staleMs: 10 });
      const raw = await fs.readFile(lockPath, "utf8");
      const payload = JSON.parse(raw) as { pid: number };

      expect(payload.pid).toBe(process.pid);
      await lock.release();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("removes held locks on termination signals", async () => {
    const signals = ["SIGINT", "SIGTERM", "SIGQUIT", "SIGABRT"] as const;
    for (const signal of signals) {
      const root = await fs.mkdtemp(path.join(os.tmpdir(), "moltbot-lock-cleanup-"));
      try {
        const sessionFile = path.join(root, "sessions.json");
        const lockPath = `${sessionFile}.lock`;
        await acquireSessionWriteLock({ sessionFile, timeoutMs: 500 });
        // Simulate shutdown without sending a real signal to the Vitest worker.
        // Windows terminates the process immediately for a re-raised SIGTERM.
        const originalListeners = process.listeners(signal);
        const kill = vi.spyOn(process, "kill").mockReturnValue(true);
        const keepAlive = () => {};
        if (signal === "SIGINT") {
          process.on(signal, keepAlive);
        }

        try {
          const shouldReraise = process.listenerCount(signal) === 1;
          __testing.handleTerminationSignal(signal);

          await expect(fs.stat(lockPath)).rejects.toThrow();
          if (shouldReraise) {
            expect(kill).toHaveBeenCalledExactlyOnceWith(process.pid, signal);
          } else {
            expect(kill).not.toHaveBeenCalled();
          }
        } finally {
          process.off(signal, keepAlive);
          // The handler removes itself before re-raising. Restore it for the
          // remaining cases rather than leaking test-induced listener changes.
          for (const listener of originalListeners) {
            if (!process.listeners(signal).includes(listener)) {
              process.on(signal, listener);
            }
          }
          kill.mockRestore();
        }
      } finally {
        await fs.rm(root, { recursive: true, force: true });
      }
    }
  });

  it("does not double-close shutdown descriptors during garbage collection", () => {
    const moduleUrl = new URL("./session-write-lock.ts", import.meta.url).href;
    const script = `
      import { mkdtemp, rm } from "node:fs/promises";
      import os from "node:os";
      import path from "node:path";
      const { acquireSessionWriteLock, __testing } = await import(${JSON.stringify(moduleUrl)});
      const root = await mkdtemp(path.join(os.tmpdir(), "moltbot-lock-gc-"));
      try {
        for (let i = 0; i < 40; i++) {
          await acquireSessionWriteLock({ sessionFile: path.join(root, \`session-\${i}.json\`) });
          __testing.releaseAllLocksSync();
        }
        for (let i = 0; i < 12; i++) {
          global.gc();
          await new Promise(resolve => setTimeout(resolve, 10));
        }
      } finally {
        await rm(root, { recursive: true, force: true });
      }
    `;
    // Use Node explicitly even when the outer suite is launched with bunx.
    const child = spawnSync(
      "node",
      ["--experimental-strip-types", "--expose-gc", "--input-type=module", "-e", script],
      { encoding: "utf8", timeout: 10_000 },
    );
    expect(child.error).toBeUndefined();
    expect(child.status, child.stderr).toBe(0);
    expect(child.stderr).not.toContain("EBADF");
  });

  it("closes a descriptor once when shutdown is followed by release", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "moltbot-lock-close-"));
    const close = vi.spyOn(fsSync, "closeSync");
    try {
      const sessionFile = path.join(root, "sessions.json");
      const lock = await acquireSessionWriteLock({ sessionFile, timeoutMs: 500 });
      __testing.releaseAllLocksSync();
      await lock.release();
      expect(close).toHaveBeenCalledTimes(1);
      await expect(fs.access(`${sessionFile}.lock`)).rejects.toThrow();
    } finally {
      close.mockRestore();
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("registers cleanup for SIGQUIT and SIGABRT", () => {
    expect(__testing.cleanupSignals).toContain("SIGQUIT");
    expect(__testing.cleanupSignals).toContain("SIGABRT");
  });
  it("cleans up locks on SIGINT without removing other handlers", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "moltbot-lock-"));
    const originalKill = process.kill.bind(process) as typeof process.kill;
    const killCalls: Array<NodeJS.Signals | undefined> = [];
    let otherHandlerCalled = false;

    process.kill = ((pid: number, signal?: NodeJS.Signals) => {
      killCalls.push(signal);
      return true;
    }) as typeof process.kill;

    const otherHandler = () => {
      otherHandlerCalled = true;
    };

    process.on("SIGINT", otherHandler);

    try {
      const sessionFile = path.join(root, "sessions.json");
      const lockPath = `${sessionFile}.lock`;
      await acquireSessionWriteLock({ sessionFile, timeoutMs: 500 });

      process.emit("SIGINT");

      await expect(fs.access(lockPath)).rejects.toThrow();
      expect(otherHandlerCalled).toBe(true);
      expect(killCalls).toEqual([]);
    } finally {
      process.off("SIGINT", otherHandler);
      process.kill = originalKill;
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("cleans up locks on exit", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "moltbot-lock-"));
    try {
      const sessionFile = path.join(root, "sessions.json");
      const lockPath = `${sessionFile}.lock`;
      await acquireSessionWriteLock({ sessionFile, timeoutMs: 500 });

      process.emit("exit", 0);

      await expect(fs.access(lockPath)).rejects.toThrow();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
  it("keeps other signal listeners registered", () => {
    const keepAlive = () => {};
    process.on("SIGINT", keepAlive);

    __testing.handleTerminationSignal("SIGINT");

    expect(process.listeners("SIGINT")).toContain(keepAlive);
    process.off("SIGINT", keepAlive);
  });
});
