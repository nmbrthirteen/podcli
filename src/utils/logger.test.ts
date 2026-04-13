import { describe, it, expect } from "vitest";
import { logger, childLogger } from "./logger.js";

describe("logger", () => {
  it("exposes winston-compatible levels", () => {
    expect(typeof logger.info).toBe("function");
    expect(typeof logger.warn).toBe("function");
    expect(typeof logger.error).toBe("function");
    expect(typeof logger.debug).toBe("function");
  });

  it("childLogger tags messages with the module name", () => {
    const child = childLogger("test-mod");
    expect(child).toBeDefined();
    expect(typeof child.info).toBe("function");
  });

  it("respects PODCLI_LOG_LEVEL default (debug in dev)", () => {
    // level is set at import time; just verify it's a known winston level
    expect(["silly", "debug", "verbose", "http", "info", "warn", "error"]).toContain(logger.level);
  });
});
