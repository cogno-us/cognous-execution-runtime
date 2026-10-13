import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TextAdmissionRequest, TextReplyAdmission } from "./reply-admission.js";
import { createReplyDispatcher } from "./reply-dispatcher.js";

const probe = process.env.COGNOUS_OPENAPPA_REPLY_PROBE;

// Ordinary suites may omit Rust; the dedicated hosted gate rejects any skipped case.
describe.skipIf(!probe)("actual pinned OpenAPPA final-text admission", () => {
  let directory: string;
  let store: string;
  const run = (mode: string, raw?: string) =>
    execFileSync(probe!, [mode, store], {
      input: raw,
      encoding: "utf8",
      timeout: 10_000,
      maxBuffer: 262_144,
      stdio: ["pipe", "pipe", "pipe"],
    });
  const check = async (request: TextAdmissionRequest) => {
    const { requestSha256: _digest, ...record } = request;
    return JSON.parse(run("check", JSON.stringify(record))) as Awaited<
      ReturnType<TextReplyAdmission["check"]>
    >;
  };
  beforeEach(() => {
    directory = fs.mkdtempSync(path.join(os.tmpdir(), "synthetic-reply-admission-"));
    store = path.join(directory, "trajectory.sqlite");
    run("bootstrap");
  });
  afterEach(() => fs.rmSync(directory, { recursive: true, force: true }));

  it("delivers normalized text only after actual persisted-runtime allowance", async () => {
    const deliver = vi.fn();
    const responses: string[] = [];
    const dispatcher = createReplyDispatcher({
      deliver,
      responsePrefix: "Synthetic:",
      textAdmission: {
        destinationId: "synthetic-public",
        check: async (request) => {
          const response = await check(request);
          responses.push(response.outcome);
          expect(response.requestSha256).toBe(request.requestSha256);
          expect(request.text).toBe("Synthetic: public output");
          return response;
        },
      },
    });
    dispatcher.sendFinalReply({ text: "public output" });
    await dispatcher.waitForIdle();
    expect(responses).toEqual(["allow"]);
    expect(deliver).toHaveBeenCalledExactlyOnceWith(
      { text: "Synthetic: public output" },
      { kind: "final" },
    );
  });

  it("refuses public delivery after actual narrowing and process reopen", async () => {
    run("restrict");
    for (let attempt = 0; attempt < 2; attempt++) {
      const deliver = vi.fn();
      const decisions: string[] = [];
      const dispatcher = createReplyDispatcher({
        deliver,
        textAdmission: {
          destinationId: "synthetic-public",
          check: async (request) => {
            const response = await check(request);
            decisions.push(response.outcome);
            return response;
          },
        },
      });
      dispatcher.sendFinalReply({ text: "synthetic restricted output" });
      await dispatcher.waitForIdle();
      expect(decisions).toEqual(["refuse"]);
      expect(deliver).not.toHaveBeenCalled();
    }
  });

  it.each(["missing", "corrupt"])(
    "does not deliver or recreate a %s trajectory",
    async (mutation) => {
      if (mutation === "missing") fs.unlinkSync(store);
      else fs.writeFileSync(store, "synthetic-invalid-store");
      const deliver = vi.fn();
      const onError = vi.fn();
      const dispatcher = createReplyDispatcher({
        deliver,
        onError,
        textAdmission: { destinationId: "synthetic-public", check },
      });
      dispatcher.sendFinalReply({ text: "synthetic output" });
      await dispatcher.waitForIdle();
      expect(deliver).not.toHaveBeenCalled();
      expect(onError).toHaveBeenCalledTimes(1);
      if (mutation === "missing") expect(fs.existsSync(store)).toBe(false);
      else expect(fs.readFileSync(store, "utf8")).toBe("synthetic-invalid-store");
    },
  );

  it("rejects actual allowance for different text", async () => {
    const deliver = vi.fn();
    const onError = vi.fn();
    let actualOutcome: string | undefined;
    let actualDigest: string | undefined;
    let expectedDigest: string | undefined;
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: {
        destinationId: "synthetic-public",
        check: async (request) => {
          const response = await check({ ...request, text: "different synthetic text" });
          actualOutcome = response.outcome;
          actualDigest = response.requestSha256;
          expectedDigest = request.requestSha256;
          return response;
        },
      },
    });
    dispatcher.sendFinalReply({ text: "original synthetic text" });
    await dispatcher.waitForIdle();
    expect(actualOutcome).toBe("allow");
    expect(actualDigest).toBeDefined();
    expect(actualDigest).not.toBe(expectedDigest);
    expect(deliver).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("refuses a destination outside the fixed fixture mapping", async () => {
    const deliver = vi.fn();
    const onError = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: { destinationId: "synthetic-other", check },
    });
    dispatcher.sendFinalReply({ text: "synthetic output" });
    await dispatcher.waitForIdle();
    expect(deliver).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });
  it("does not deliver an actual allowance cancelled before release", async () => {
    const controller = new AbortController();
    const deliver = vi.fn();
    const onError = vi.fn();
    let actualOutcome: string | undefined;
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: {
        destinationId: "synthetic-public",
        signal: controller.signal,
        check: async (request) => {
          const response = await check(request);
          actualOutcome = response.outcome;
          controller.abort();
          return response;
        },
      },
    });
    dispatcher.sendFinalReply({ text: "synthetic cancelled output" });
    await dispatcher.waitForIdle();
    expect(actualOutcome).toBe("allow");
    expect(deliver).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });
  it("stops after ambiguous delivery despite actual policy allowance", async () => {
    const syntheticDestination: string[] = [];
    const deliver = vi.fn(async (payload) => {
      syntheticDestination.push(payload.text);
      throw new Error("synthetic receipt loss after send");
    });
    const onError = vi.fn();
    const decisions: string[] = [];
    const policyCheck = vi.fn(async (request: TextAdmissionRequest) => {
      const response = await check(request);
      decisions.push(response.outcome);
      return response;
    });
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: {
        destinationId: "synthetic-public",
        check: policyCheck,
      },
    });
    dispatcher.sendFinalReply({ text: "first synthetic output" });
    dispatcher.sendFinalReply({ text: "later synthetic output" });
    await dispatcher.waitForIdle();
    expect(decisions).toEqual(["allow"]);
    expect(policyCheck).toHaveBeenCalledTimes(1);
    expect(deliver).toHaveBeenCalledTimes(1);
    expect(syntheticDestination).toEqual(["first synthetic output"]);
    expect(onError).toHaveBeenCalledTimes(2);
    expect(dispatcher.sendFinalReply({ text: "new output" })).toBe(false);
  });
  it.each(["success", "feedback-unavailable", "feedback-receipt-lost", "send-receipt-lost"])(
    "keeps synthetic delivery separate from actual feedback: %s",
    async (mode) => {
      const destination: string[] = [];
      let raw: string | undefined;
      let digest: string | undefined;
      let receipt: { requestSha256: string; outcome: string } | undefined;
      const audit = () => JSON.parse(run("audit")) as { event: unknown }[];
      const closed = (entries: { event: unknown }[]) =>
        entries.filter(
          (entry) =>
            JSON.stringify(entry.event) ===
            JSON.stringify({ Closed: { outcome: { Ran: { effects: ["egress"] } } } }),
        ).length;
      const before = audit();
      const onError = vi.fn();
      const deliver = vi.fn(async (payload) => {
        destination.push(payload.text);
        if (mode === "send-receipt-lost") throw new Error("synthetic send receipt lost");
        if (mode === "feedback-unavailable") throw new Error("synthetic feedback unavailable");
        const observed = JSON.parse(run("feedback", raw));
        if (mode === "feedback-receipt-lost") throw new Error("synthetic feedback receipt lost");
        receipt = observed;
      });
      const dispatcher = createReplyDispatcher({
        deliver,
        onError,
        textAdmission: {
          destinationId: "synthetic-public",
          check: async (request) => {
            const { requestSha256, ...record } = request;
            raw = JSON.stringify(record);
            digest = requestSha256;
            return check(request);
          },
        },
      });
      dispatcher.sendFinalReply({ text: "synthetic observed output" });
      await dispatcher.waitForIdle();
      expect(raw).toBeDefined();
      expect(digest).toBeDefined();
      expect(destination).toEqual(["synthetic observed output"]);
      expect(deliver).toHaveBeenCalledTimes(1);
      expect(onError).toHaveBeenCalledTimes(mode === "success" ? 0 : 1);
      expect(receipt).toEqual(
        mode === "success" ? { requestSha256: digest, outcome: "ack" } : undefined,
      );
      const after = audit();
      const committed = mode === "success" || mode === "feedback-receipt-lost";
      expect(closed(after) - closed(before)).toBe(Number(committed));
      if (committed) {
        // Explicit fixture-only duplicate feedback; never re-send the output.
        expect(JSON.parse(run("feedback", raw))).toEqual({
          requestSha256: digest,
          outcome: "refuse",
        });
        expect(audit()).toEqual(after);
      }
      expect(destination).toEqual(["synthetic observed output"]);
    },
  );

  it("refuses feedback for an output that was never admitted", () => {
    const before = JSON.parse(run("audit"));
    const raw = JSON.stringify({
      outputId: "synthetic-unreleased-output",
      kind: "final",
      destinationId: "synthetic-public",
      text: "synthetic output",
    });
    expect(JSON.parse(run("feedback", raw)).outcome).toBe("refuse");
    expect(JSON.parse(run("audit"))).toEqual(before);
  });
});
