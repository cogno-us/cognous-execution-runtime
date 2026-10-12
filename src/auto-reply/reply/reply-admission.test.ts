import { createHash } from "node:crypto";
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
    const deliver = vi.fn();
    const onError = vi.fn();
    const dispatcher = createReplyDispatcher({
      deliver,
      onError,
      textAdmission: {
        destinationId: "synthetic",
        timeoutMs: 10,
        check: (value) => {
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
    finish(await allow(request));
    await Promise.resolve();
    expect(deliver).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(dispatcher.sendFinalReply({ text: "two" })).toBe(false);
  });

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
