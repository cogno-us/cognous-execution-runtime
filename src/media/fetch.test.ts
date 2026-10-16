import { describe, expect, it, vi } from "vitest";

vi.hoisted(() => vi.resetModules());

import { fetchRemoteMedia } from "./fetch.js";

vi.mock("../infra/net/ssrf.js", async () => {
  const actual =
    await vi.importActual<typeof import("../infra/net/ssrf.js")>("../infra/net/ssrf.js");
  return {
    ...actual,
    resolvePinnedHostname: (host: string) =>
      actual.resolvePinnedHostname(host, async () => [{ address: "93.184.216.34", family: 4 }]),
  };
});

function makeStream(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

describe("fetchRemoteMedia", () => {
  it("rejects when content-length exceeds maxBytes", async () => {
    const fetchImpl = async () =>
      new Response(makeStream([new Uint8Array([1, 2, 3, 4, 5])]), {
        status: 200,
        headers: { "content-length": "5" },
      });

    await expect(
      fetchRemoteMedia({
        url: "https://example.com/file.bin",
        fetchImpl,
        maxBytes: 4,
      }),
    ).rejects.toThrow("exceeds maxBytes");
  });

  it("rejects when streamed payload exceeds maxBytes", async () => {
    const fetchImpl = async () =>
      new Response(makeStream([new Uint8Array([1, 2, 3]), new Uint8Array([4, 5, 6])]), {
        status: 200,
      });

    await expect(
      fetchRemoteMedia({
        url: "https://example.com/file.bin",
        fetchImpl,
        maxBytes: 4,
      }),
    ).rejects.toThrow("exceeds maxBytes");
  });
});

it("blocks private redirect before the second fetch", async () => {
  const fetchImpl = vi.fn(
    async () =>
      new Response(null, {
        status: 302,
        headers: { location: "http://127.0.0.1/private" },
      }),
  );
  await expect(fetchRemoteMedia({ url: "https://example.com/a", fetchImpl })).rejects.toThrow(
    "Blocked",
  );
  expect(fetchImpl).toHaveBeenCalledTimes(1);
});

it("enforces the default download budget before reading a large declared body", async () => {
  const fetchImpl = async () => new Response(null, { headers: { "content-length": "20971521" } });
  await expect(
    fetchRemoteMedia({ url: "https://example.com/oversized", fetchImpl }),
  ).rejects.toThrow("exceeds maxBytes");
});
