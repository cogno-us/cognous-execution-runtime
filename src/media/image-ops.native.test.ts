import { describe, expect, it } from "vitest";

import { getImageMetadata, optimizeImageToPng, resizeToJpeg } from "./image-ops.js";

// Valid 1x1 RGBA PNG with verified chunk CRCs and zlib checksum.
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==",
  "base64",
);

describe("native image pipeline", () => {
  it("decodes and optimizes a valid PNG without losing its dimensions", async () => {
    expect(await getImageMetadata(png)).toEqual({ width: 1, height: 1 });
    const result = await optimizeImageToPng(png, 4096);
    expect(result.buffer.subarray(0, 8)).toEqual(png.subarray(0, 8));
    expect(result.buffer.length).toBeLessThanOrEqual(4096);
    expect(await getImageMetadata(result.buffer)).toEqual({ width: 1, height: 1 });
  });

  it("converts the same valid input to a decodable JPEG", async () => {
    const jpeg = await resizeToJpeg({ buffer: png, maxSide: 64, quality: 80 });
    expect(jpeg.subarray(0, 2)).toEqual(Buffer.from([0xff, 0xd8]));
    expect(await getImageMetadata(jpeg)).toEqual({ width: 1, height: 1 });
  });
});
