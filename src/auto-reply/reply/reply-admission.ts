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
  /** Must check current policy and authenticated host context; it must not deliver. */
  check: (request: TextAdmissionRequest) => Promise<{
    outcome: "allow" | "refuse";
    requestSha256: string;
  }>;
};

/** Optional final-text boundary. No authentication, remote receipt or atomicity claim. */
export function createTextReplyAdmission(options: TextReplyAdmission) {
  const { destinationId, check } = options;
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
  return {
    assertOpen() {
      if (stopped) throw new Error("reply admission stopped");
    },
    snapshot(payload: ReplyPayload, kind: string): ReplyPayload {
      if (stopped) throw new Error("reply admission stopped");
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
      if (stopped) throw new Error("reply admission stopped");
      let timer: ReturnType<typeof setTimeout> | undefined;
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
          Promise.resolve().then(() => check(request)),
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => reject(new Error("reply admission timed out")), timeoutMs);
          }),
        ]);
        if (result?.outcome !== "allow" || result.requestSha256 !== requestSha256) {
          throw new Error("reply admission refused or uncorrelated");
        }
        // Another enqueue can stop the profile while its check is outstanding.
        if (stopped) throw new Error("reply admission stopped");
      } catch (error) {
        stopped = true;
        throw error;
      } finally {
        if (timer !== undefined) clearTimeout(timer);
      }
    },
  };
}
