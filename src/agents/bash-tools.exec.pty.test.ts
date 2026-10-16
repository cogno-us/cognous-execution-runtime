import { afterEach, expect, test } from "vitest";

import { createExecTool as rawCreateExecTool } from "./bash-tools.exec";
import { resetProcessRegistryForTests } from "./bash-process-registry";

afterEach(() => {
  resetProcessRegistryForTests();
});

test("exec supports pty output", async () => {
  const tool = createExecTool({ allowBackground: false });
  const result = await tool.execute("toolcall", {
    command: 'node -e "process.stdout.write(String.fromCharCode(111,107))"',
    pty: true,
  });

  expect(result.details.status).toBe("completed");
  const text = result.content?.[0]?.text ?? "";
  expect(text).toContain("ok");
});

// Process-lifecycle tests explicitly authorize their synthetic host commands.
const createExecTool = (defaults?: Parameters<typeof rawCreateExecTool>[0]) =>
  rawCreateExecTool({ host: "gateway", security: "full", ask: "off", ...defaults });
