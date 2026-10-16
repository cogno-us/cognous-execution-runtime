import { expect, it } from "vitest";
import { createExecTool } from "./bash-tools.exec.js";

it.each(["sandbox", "gateway", "node"] as const)(
  "enforces deny before %s process creation",
  async (host) => {
    const tool = createExecTool({ host, security: "deny", ask: "off" });
    await expect(tool.execute("denied", { command: "must-not-execute" })).rejects.toThrow(
      "security=deny",
    );
  },
);
it("does not silently fall back to a host process when the sandbox is absent", async () => {
  const tool = createExecTool({ host: "sandbox", security: "full", ask: "off" });
  await expect(tool.execute("missing-sandbox", { command: "must-not-execute" })).rejects.toThrow(
    "sandbox is unavailable",
  );
});
