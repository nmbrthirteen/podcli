import { describe, expect, it } from "vitest";
import { shellQuote } from "./McpSetupPage";

describe("shellQuote", () => {
  it("quotes a macOS Application Support path so the space doesn't split it into two args", () => {
    const path = "/Users/nika/Library/Application Support/podcli/mcp-server.mjs";
    const quoted = shellQuote(path);
    expect(quoted).toBe("'/Users/nika/Library/Application Support/podcli/mcp-server.mjs'");
    // A naive split on whitespace (what an unquoted shell command would do)
    // must not break the path in two.
    expect(quoted.split(" ")).toHaveLength(2);
  });

  it("escapes an embedded single quote so the quoting itself can't be broken out of", () => {
    expect(shellQuote("it's/a/path")).toBe(`'it'\\''s/a/path'`);
  });

  it("leaves a path with no special characters readable", () => {
    expect(shellQuote("/opt/podcli/mcp-server.mjs")).toBe("'/opt/podcli/mcp-server.mjs'");
  });
});
