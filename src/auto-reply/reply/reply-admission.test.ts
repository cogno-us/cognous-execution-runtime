import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TextAdmissionRequest } from "./reply-admission.js";
import { createReplyDispatcher } from "./reply-dispatcher.js";

const allow = async (request: TextAdmissionRequest) => ({
  outcome: "allow" as const,
  requestSha256: request.requestSha256,
});

afterEach(() => vi.useRealTimers());

describe("opt-in final text admission", () => {
  it("binds normalized text, kind, destination and unique output identity before delivery", async () => {
    const events: string[] = [];
    const requests: TextAdmissionRequest[] = [];
    const deliver = vi.fn(async (payload) => {
      events.push("deliver");
      expect(Object.isFrozen(payload)).toBe(true);
    });
    const dispatcher = createReplyDispatcher({
      deliver,
      responsePrefix: "Synthetic:",
      textAdmission: {
        destinationId: "synthetic-destination",
        check: async (request) => {
          events.push("check");
          requests.push(request);
          expect(Object.isFrozen(request)).toBe(true);
          const { requestSha256, ...record } = request;
          expect(requestSha256).toBe(
            createHash("sha256").update(JSON.stringify(record)).digest("hex"),
          );
          return allow(request);
        },
      },
    });
    const input = { text: "original" };
    expect(dispatcher.sendFinalReply(input)).toBe(true); // Queue acceptance, not permission.
    input.text = "changed after enqueue";
    dispatcher.sendFinalReply({ text: "original" });
    await dispatcher.waitForIdle();
    expect(events).toEqual(["check", "deliver", "check", "deliver"]);
    expect(deliver).toHaveBeenNthCalledWith(1, { text: "Synthetic: original" }, { kind: "final" });
    expect(requests[0]).toMatchObject({ kind: "final", destinationId: "synthetic-destination" });
    expect(requests[0].outputId).not.toBe(requests[1].outputId);
    expect(requests[0].requestSha256).not.toBe(requests[1].requestSha256);
  });

  it.each(["refuse", "wrong-digest", "missing", "exception"])(
    "does not deliver on %s and stops later replies",
    async (mode) => {
      const deliver = vi.fn();
      const onError = vi.fn();
      const check = vi.fn(async (request: TextAdmissionRequest) => {
        if (mode === "exception") throw new Error("synthetic unavailable");
        if (mode === "missing") return undefined as never;
        return {
          outcome: mode === "refuse" ? ("refuse" as const) : ("allow" as const),
          requestSha256: mode === "wrong-digest" ? "0".repeat(64) : request.requestSha256,
        };
      });
      const dispatcher = createReplyDispatcher({
        deliver,
        onError,
        textAdmission: { destinationId: "synthetic", check },
      });
      dispatcher.sendFinalReply({ text: "one" });
      dispatcher.sendFinalReply({ text: "two" });
      await dispatcher.waitForIdle();
      expect(check).toHaveBeenCalledTimes(1);
      expect(deliver).not.toHaveBeenCalled();
      expect(onError).toHaveBeenCalledTimes(2);
      expect(dispatcher.sendFinalReply({ text: "three" })).toBe(false);
    },
  );

  it("ignores a late allow after timeout", async () => {
    vi.useFakeTimers();
    let finish!: (result: Awaited<ReturnType<typeof allow>>) => void;
    let request!: TextAdmissionRequest;
    let checkerSignal: AbortSignal | undefined;
    const deliver = vi.fn();
    const onError = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: {
        destinationId: "synthetic",
        timeoutMs: 10,
        check: (value, signal) => {
          checkerSignal = signal;
          request = value;
          return new Promise((resolve) => {
            finish = resolve;
          });
        },
      },
    });
    dispatcher.sendFinalReply({ text: "one" });
    await vi.advanceTimersByTimeAsync(11);
    await dispatcher.waitForIdle();
    expect(checkerSignal?.aborted).toBe(true);
    finish(await allow(request));
    await Promise.resolve();
    expect(deliver).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(dispatcher.sendFinalReply({ text: "two" })).toBe(false);
  });

  it.each([9, 10, 11])(
    "checks elapsed time before accepting an allowance at %s ms",
    async (elapsed) => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      const clock = vi.spyOn(performance, "now").mockReturnValue(100);
      try {
        const deliver = vi.fn();
        const onError = vi.fn();
        let checkerSignal: AbortSignal | undefined;
        const check = vi.fn(async (request: TextAdmissionRequest, signal: AbortSignal) => {
          checkerSignal = signal;
          // Model elapsed work without running the scheduled timeout callback.
          clock.mockReturnValue(100 + elapsed);
          return allow(request);
        });
        const dispatcher = createReplyDispatcher({
          deliver,
          onError,
          textAdmission: { destinationId: "synthetic", timeoutMs: 10, check },
        });
        dispatcher.sendFinalReply({ text: "synthetic output" });
        await dispatcher.waitForIdle();
        expect(check).toHaveBeenCalledTimes(1);
        expect(deliver).toHaveBeenCalledTimes(elapsed < 10 ? 1 : 0);
        expect(onError).toHaveBeenCalledTimes(elapsed < 10 ? 0 : 1);
        expect(checkerSignal?.aborted).toBe(elapsed >= 10);
        expect(vi.getTimerCount()).toBe(0);
        if (elapsed >= 10) {
          expect(dispatcher.sendFinalReply({ text: "later output" })).toBe(false);
        }
      } finally {
        clock.mockRestore();
      }
    },
  );

  it.each(["media", "channel-data", "block", "tool", "oversize"])(
    "rejects unsupported %s",
    async (mode) => {
      const deliver = vi.fn();
      const check = vi.fn(allow);
      const dispatcher = createReplyDispatcher({
        deliver,
        textAdmission: { destinationId: "synthetic", check },
      });
      const payload =
        mode === "media"
          ? { text: "x", mediaUrl: "synthetic://media" }
          : mode === "channel-data"
            ? { text: "x", channelData: { synthetic: true } }
            : { text: mode === "oversize" ? "x".repeat(16_385) : "x" };
      const accepted =
        mode === "block"
          ? dispatcher.sendBlockReply(payload)
          : mode === "tool"
            ? dispatcher.sendToolResult(payload)
            : dispatcher.sendFinalReply(payload);
      expect(accepted).toBe(false);
      await dispatcher.waitForIdle();
      expect(check).not.toHaveBeenCalled();
      expect(deliver).not.toHaveBeenCalled();
    },
  );

  it("checks queued replies at delivery time against current host policy", async () => {
    let permitted = true;
    let release!: () => void;
    const firstDelivery = new Promise<void>((resolve) => {
      release = resolve;
    });
    const deliver = vi.fn(async () => firstDelivery);
    const check = vi.fn(async (request: TextAdmissionRequest) => ({
      outcome: permitted ? ("allow" as const) : ("refuse" as const),
      requestSha256: request.requestSha256,
    }));
    const dispatcher = createReplyDispatcher({
      deliver,
      textAdmission: { destinationId: "synthetic", check },
    });
    dispatcher.sendFinalReply({ text: "first" });
    dispatcher.sendFinalReply({ text: "second" });
    await vi.waitFor(() => expect(deliver).toHaveBeenCalledTimes(1));
    permitted = false;
    release();
    await dispatcher.waitForIdle();
    expect(check).toHaveBeenCalledTimes(2);
    expect(deliver).toHaveBeenCalledTimes(1);
  });

  it("stops an outstanding allow when an unsupported enqueue closes the profile", async () => {
    let finish!: (result: Awaited<ReturnType<typeof allow>>) => void;
    let request!: TextAdmissionRequest;
    const deliver = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      textAdmission: {
        destinationId: "synthetic",
        check: (value) => {
          request = value;
          return new Promise((resolve) => {
            finish = resolve;
          });
        },
      },
    });
    dispatcher.sendFinalReply({ text: "first" });
    await vi.waitFor(() => expect(request).toBeDefined());
    expect(dispatcher.sendBlockReply({ text: "unsupported" })).toBe(false);
    finish(await allow(request));
    await dispatcher.waitForIdle();
    expect(deliver).not.toHaveBeenCalled();
  });

  it.each([0, 30_001, NaN])("rejects invalid timeout %s at construction", (timeoutMs) => {
    expect(() =>
      createReplyDispatcher({
        deliver: vi.fn(),
        textAdmission: {
          destinationId: "synthetic",
          timeoutMs,
          check: allow,
        },
      }),
    ).toThrow("invalid reply admission configuration");
  });
});

describe("reply admission cancellation", () => {
  it("rejects an already-cancelled profile without a policy check", async () => {
    const controller = new AbortController();
    controller.abort();
    const check = vi.fn(allow);
    const deliver = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      textAdmission: {
        destinationId: "synthetic",
        signal: controller.signal,
        check,
      },
    });
    expect(dispatcher.sendFinalReply({ text: "cancelled" })).toBe(false);
    await dispatcher.waitForIdle();
    expect(check).not.toHaveBeenCalled();
    expect(deliver).not.toHaveBeenCalled();
  });

  it("does not start a check cancelled between enqueue and dispatch", async () => {
    const controller = new AbortController();
    const check = vi.fn(allow);
    const deliver = vi.fn();
    const onError = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: {
        destinationId: "synthetic",
        signal: controller.signal,
        check,
      },
    });
    expect(dispatcher.sendFinalReply({ text: "queued" })).toBe(true);
    controller.abort();
    await dispatcher.waitForIdle();
    expect(check).not.toHaveBeenCalled();
    expect(deliver).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it("notifies an outstanding checker and rejects an allow returned during abort", async () => {
    const controller = new AbortController();
    const deliver = vi.fn();
    const onError = vi.fn();
    let checkerSignal: AbortSignal | undefined;
    const check = vi.fn((request: TextAdmissionRequest, signal: AbortSignal) => {
      checkerSignal = signal;
      return new Promise<Awaited<ReturnType<typeof allow>>>((resolve) => {
        signal.addEventListener(
          "abort",
          () => {
            void allow(request).then(resolve);
          },
          { once: true },
        );
      });
    });
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: {
        destinationId: "synthetic",
        signal: controller.signal,
        check,
      },
    });
    dispatcher.sendFinalReply({ text: "pending" });
    dispatcher.sendFinalReply({ text: "later" });
    await vi.waitFor(() => expect(checkerSignal).toBeDefined());
    controller.abort();
    await dispatcher.waitForIdle();
    expect(checkerSignal?.aborted).toBe(true);
    expect(check).toHaveBeenCalledTimes(1);
    expect(deliver).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(2);
  });

  it("prevents subsequent queued delivery without claiming to undo a started send", async () => {
    const controller = new AbortController();
    let release!: () => void;
    const firstDelivery = new Promise<void>((resolve) => {
      release = resolve;
    });
    const deliver = vi.fn(async () => firstDelivery);
    const check = vi.fn(allow);
    const dispatcher = createReplyDispatcher({
      deliver,
      textAdmission: {
        destinationId: "synthetic",
        signal: controller.signal,
        check,
      },
    });
    dispatcher.sendFinalReply({ text: "first" });
    dispatcher.sendFinalReply({ text: "second" });
    await vi.waitFor(() => expect(deliver).toHaveBeenCalledTimes(1));
    controller.abort();
    release();
    await dispatcher.waitForIdle();
    expect(check).toHaveBeenCalledTimes(1);
    expect(deliver).toHaveBeenCalledTimes(1);
  });
});

describe("bounded admission backlog", () => {
  it("stops the profile before checking when the 33rd pending reply arrives", async () => {
    const check = vi.fn(allow);
    const deliver = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      textAdmission: {
        destinationId: "synthetic",
        check,
      },
    });
    for (let index = 0; index < 32; index++) {
      expect(dispatcher.sendFinalReply({ text: `synthetic ${index}` })).toBe(true);
    }
    expect(dispatcher.sendFinalReply({ text: "overflow" })).toBe(false);
    await dispatcher.waitForIdle();
    expect(check).not.toHaveBeenCalled();
    expect(deliver).not.toHaveBeenCalled();
    expect(dispatcher.sendFinalReply({ text: "after overflow" })).toBe(false);
  });

  it("cancels an outstanding check on overflow without waiting for its timeout", async () => {
    const deliver = vi.fn();
    let checkerSignal: AbortSignal | undefined;
    const check = vi.fn((_request: TextAdmissionRequest, signal: AbortSignal) => {
      checkerSignal = signal;
      return new Promise<Awaited<ReturnType<typeof allow>>>(() => {});
    });
    const dispatcher = createReplyDispatcher({
      deliver,
      textAdmission: {
        destinationId: "synthetic",
        timeoutMs: 30_000,
        check,
      },
    });
    dispatcher.sendFinalReply({ text: "first" });
    await vi.waitFor(() => expect(checkerSignal).toBeDefined());
    for (let index = 1; index < 32; index++)
      dispatcher.sendFinalReply({ text: `synthetic ${index}` });
    expect(dispatcher.sendFinalReply({ text: "overflow" })).toBe(false);
    await dispatcher.waitForIdle();
    expect(checkerSignal?.aborted).toBe(true);
    expect(check).toHaveBeenCalledTimes(1);
    expect(deliver).not.toHaveBeenCalled();
  }, 5_000);

  it("releases capacity after completion and leaves an ungated backlog unchanged", async () => {
    const deliver = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      textAdmission: { destinationId: "synthetic", check: allow },
    });
    for (let batch = 0; batch < 2; batch++) {
      for (let index = 0; index < 32; index++) {
        expect(dispatcher.sendFinalReply({ text: `synthetic ${batch}-${index}` })).toBe(true);
      }
      await dispatcher.waitForIdle();
    }
    expect(deliver).toHaveBeenCalledTimes(64);
    const ungatedDeliver = vi.fn();
    const ungated = createReplyDispatcher({ deliver: ungatedDeliver });
    for (let index = 0; index < 33; index++) {
      expect(ungated.sendFinalReply({ text: `synthetic ${index}` })).toBe(true);
    }
    await ungated.waitForIdle();
    expect(ungatedDeliver).toHaveBeenCalledTimes(33);
  });
});

describe("delivery failure after admission", () => {
  it.each(["before-send", "after-send"])("stops later gated output on failure %s", async (mode) => {
    const syntheticDestination: string[] = [];
    const failure = new Error("synthetic transport failure");
    const deliver = vi.fn(async (payload) => {
      if (mode === "after-send") syntheticDestination.push(payload.text);
      throw failure;
    });
    const check = vi.fn(allow);
    const onError = vi.fn();
    const onIdle = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      onIdle,
      textAdmission: { destinationId: "synthetic", check },
    });
    dispatcher.sendFinalReply({ text: "first" });
    dispatcher.sendFinalReply({ text: "second" });
    await dispatcher.waitForIdle();
    expect(check).toHaveBeenCalledTimes(1);
    expect(deliver).toHaveBeenCalledTimes(1);
    expect(syntheticDestination).toEqual(mode === "after-send" ? ["first"] : []);
    expect(onError).toHaveBeenNthCalledWith(1, failure, { kind: "final" });
    expect(onError).toHaveBeenCalledTimes(2);
    expect(onIdle).toHaveBeenCalledTimes(1);
    expect(dispatcher.sendFinalReply({ text: "third" })).toBe(false);
  });

  it("preserves ungated continuation after delivery errors", async () => {
    const failure = new Error("synthetic transport failure");
    const deliver = vi.fn().mockRejectedValueOnce(failure).mockResolvedValue(undefined);
    const onError = vi.fn();
    const dispatcher = createReplyDispatcher({ deliver, onError });
    dispatcher.sendFinalReply({ text: "first" });
    dispatcher.sendFinalReply({ text: "second" });
    await dispatcher.waitForIdle();
    expect(deliver).toHaveBeenCalledTimes(2);
    expect(deliver).toHaveBeenLastCalledWith({ text: "second" }, { kind: "final" });
    expect(onError).toHaveBeenCalledExactlyOnceWith(failure, { kind: "final" });
  });
});
