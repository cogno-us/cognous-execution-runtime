import { createHash, randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import type { ReplyPayload } from "../types.js";

export type TextAdmissionRequest = Readonly<{
  outputId: string;
  kind: "final";
  destinationId: string;
  text: string;
  requestSha256: string;
}>;

export type TextReplyAdmission = {
  /** Trusted host routing identity; the host must bind deliver() to this destination. */
  destinationId: string;
  timeoutMs?: number;
  /** Cancels queued admission, not an already-started delivery. */
  signal?: AbortSignal;
  /** Must check current policy and authenticated host context; it must not deliver. */
  check: (
    request: TextAdmissionRequest,
    signal: AbortSignal,
  ) => Promise<{
    outcome: "allow" | "refuse";
    requestSha256: string;
  }>;
};

/** Optional final-text boundary. No authentication, remote receipt or atomicity claim. */
export function createTextReplyAdmission(options: TextReplyAdmission) {
  const { destinationId, check, signal } = options;
  const timeoutMs = options.timeoutMs ?? 5_000;
  if (
    typeof destinationId !== "string" ||
    !destinationId.trim() ||
    destinationId !== destinationId.trim() ||
    typeof check !== "function" ||
    !Number.isInteger(timeoutMs) ||
    timeoutMs < 1 ||
    timeoutMs > 30_000
  ) {
    throw new Error("invalid reply admission configuration");
  }
  let stopped = false;
  let activeController: AbortController | undefined;
  let interrupt: ((error: Error) => void) | undefined;
  const stop = (message: string) => {
    // Latch first: a checker may return an allowance from its abort handler.
    stopped = true;
    activeController?.abort();
    interrupt?.(new Error(message));
  };
  const assertOpen = () => {
    if (signal?.aborted) stopped = true;
    if (stopped) throw new Error("reply admission stopped");
  };
  return {
    assertOpen,
    stop: () => stop("reply admission stopped after dispatch failure"),
    snapshot(payload: ReplyPayload, kind: string, pending: number): ReplyPayload {
      assertOpen();
      if (pending >= 32) {
        stop("reply admission queue limit exceeded");
        throw new Error("reply admission queue limit exceeded");
      }
      if (
        kind !== "final" ||
        typeof payload.text !== "string" ||
        Buffer.byteLength(payload.text, "utf8") > 16_384 ||
        Reflect.ownKeys(payload).some(
          (key) => key !== "text" && Reflect.get(payload, key) !== undefined,
        )
      ) {
        stop("reply admission supports bounded final text only");
        throw new Error("reply admission supports bounded final text only");
      }
      return Object.freeze({ text: payload.text });
    },
    async admit(payload: ReplyPayload): Promise<void> {
      assertOpen();
      let timer: ReturnType<typeof setTimeout> | undefined;
      let onAbort: (() => void) | undefined;
      const controller = new AbortController();
      activeController = controller;
      try {
        const record = {
          outputId: randomUUID(),
          kind: "final" as const,
          destinationId,
          text: payload.text!,
        };
        const requestSha256 = createHash("sha256").update(JSON.stringify(record)).digest("hex");
        const request = Object.freeze({ ...record, requestSha256 });
        const startedAt = performance.now();
        const assertTimely = () => {
          // A busy event loop can delay the timer past a checker's resolution.
          if (performance.now() - startedAt >= timeoutMs) {
            stop("reply admission timed out");
            throw new Error("reply admission timed out");
          }
        };
        const result = await Promise.race([
          Promise.resolve().then(() => {
            assertOpen();
            assertTimely();
            return check(request, controller.signal);
          }),
          new Promise<never>((_, reject) => {
            interrupt = reject;
            onAbort = () => stop("reply admission cancelled");
            signal?.addEventListener("abort", onAbort, { once: true });
            if (signal?.aborted) onAbort();
            timer = setTimeout(() => stop("reply admission timed out"), timeoutMs);
          }),
        ]);
        assertTimely();
        if (result?.outcome !== "allow" || result.requestSha256 !== requestSha256) {
          throw new Error("reply admission refused or uncorrelated");
        }
        // Another enqueue can stop the profile while its check is outstanding.
        assertOpen();
      } catch (error) {
        stopped = true;
        throw error;
      } finally {
        activeController = undefined;
        interrupt = undefined;
        if (timer !== undefined) clearTimeout(timer);
        if (onAbort) signal?.removeEventListener("abort", onAbort);
      }
    },
  };
}
