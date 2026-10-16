import { expect, it } from "vitest";
import { scriptJson } from "./control-ui.js";
it("preserves identity text without allowing an HTML script terminator", () => {
  const input = "</script><script>untrusted()</script>\u2028\u2029&";
  const encoded = scriptJson(input);
  expect(encoded).not.toContain("<");
  expect(JSON.parse(encoded)).toBe(input);
});
