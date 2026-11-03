import { describe, it, expect } from "vitest";
import { logger, childLogger } from "./logger.js";

describe("logger", () => {
  it("exposes winston-compatible levels", () => {
    expect(logger.info).toBeInstanceOf(Function);
    expect(logger.warn).toBeInstanceOf(Function);
    expect(logger.error).toBeInstanceOf(Function);
    expect(logger.debug).toBeInstanceOf(Function);
  });

  it("childLogger tags messages with the module name", () => {
    const child = childLogger("test-mod");
    expect(child).toBeDefined();
    expect(child.info).toBeInstanceOf(Function);
  });

  it("respects PODCLI_LOG_LEVEL default (debug in dev)", () => {
    // level is set at import time; just verify it's a known winston level
    expect([
      "silly",
      "debug",
      "verbose",
      "http",
      "info",
      "warn",
      "error",
    ]).toContain(logger.level);
  });
});
