import { vi } from "vitest";

/**
 * Stand in for the Python backend: every task succeeds with whatever `reply` returns for its type.
 *
 * Loads python-executor on demand because it reads PODCLI_HOME through paths.ts, so a test has to
 * set its environment first.
 */
export async function stubPythonBackend(reply: (taskType: string) => object = () => ({})): Promise<void> {
  const { setExecutorFactory } = await import("../services/python-executor.js");

  const execute = vi.fn().mockImplementation(async (taskType: string) => ({
    task_id: "stub",
    status: "success",
    data: reply(taskType),
  }));

  setExecutorFactory(() => ({ execute }));
}
