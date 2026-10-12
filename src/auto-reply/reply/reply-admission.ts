import { createHash, randomUUID } from "node:crypto";
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
  const assertOpen = () => {
    if (signal?.aborted) stopped = true;
    if (stopped) throw new Error("reply admission stopped");
  };
  return {
    assertOpen,
    snapshot(payload: ReplyPayload, kind: string): ReplyPayload {
      assertOpen();
      if (
        kind !== "final" ||
        typeof payload.text !== "string" ||
        Buffer.byteLength(payload.text, "utf8") > 16_384 ||
        Reflect.ownKeys(payload).some(
          (key) => key !== "text" && Reflect.get(payload, key) !== undefined,
        )
      ) {
        stopped = true;
        throw new Error("reply admission supports bounded final text only");
      }
      return Object.freeze({ text: payload.text });
    },
    async admit(payload: ReplyPayload): Promise<void> {
      assertOpen();
      let timer: ReturnType<typeof setTimeout> | undefined;
      let onAbort: (() => void) | undefined;
      const controller = new AbortController();
      try {
        const record = {
          outputId: randomUUID(),
          kind: "final" as const,
          destinationId,
          text: payload.text!,
        };
        const requestSha256 = createHash("sha256").update(JSON.stringify(record)).digest("hex");
        const request = Object.freeze({ ...record, requestSha256 });
        const result = await Promise.race([
          Promise.resolve().then(() => {
            assertOpen();
            return check(request, controller.signal);
          }),
          new Promise<never>((_, reject) => {
            const stop = (message: string) => {
              // Stop before notifying the checker: it may resolve during abort.
              stopped = true;
              controller.abort();
              reject(new Error(message));
            };
            onAbort = () => stop("reply admission cancelled");
            signal?.addEventListener("abort", onAbort, { once: true });
            if (signal?.aborted) onAbort();
            timer = setTimeout(() => stop("reply admission timed out"), timeoutMs);
          }),
        ]);
        if (result?.outcome !== "allow" || result.requestSha256 !== requestSha256) {
          throw new Error("reply admission refused or uncorrelated");
        }
        // Another enqueue can stop the profile while its check is outstanding.
        assertOpen();
      } catch (error) {
        stopped = true;
        throw error;
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        if (onAbort) signal?.removeEventListener("abort", onAbort);
      }
    },
  };
}
