import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { callTool, type ToolArgs } from "./test-support/mcp-client.js";

// manage_thumbnail_config talks to the studio's /api/thumbnail-config. These
// stub that endpoint and check what set_layout sends it.

const tmp = mkdtempSync(join(tmpdir(), "podcli-thumbnail-config-"));
const savedHome = process.env.PODCLI_HOME;
const savedData = process.env.PODCLI_DATA;

let createServer: typeof import("./server.js").createServer;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  ({ createServer } = await import("./server.js"));
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;

  if (savedData === undefined) delete process.env.PODCLI_DATA;
  else process.env.PODCLI_DATA = savedData;
  rmSync(tmp, { recursive: true, force: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const manageThumbnailConfig = (args: ToolArgs) => callTool(createServer(), "manage_thumbnail_config", args);

describe("manage_thumbnail_config set_layout", () => {
  it("writes the layout into the template and keeps every other field", async () => {
    const calls: { method: string; body?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      calls.push({ method: init?.method ?? "GET", body: init?.body === undefined ? undefined : String(init.body) });

      return new Response(JSON.stringify({ accent_color: "#FF3366", layout: "single" }), { status: 200 });
    }));

    const result = await manageThumbnailConfig({ action: "set_layout", layout: "pair" });

    expect(result.text).toBe("Thumbnail layout set to pair.");
    const put = calls.find((c) => c.method === "PUT");
    expect(JSON.parse(put!.body!)).toEqual({ accent_color: "#FF3366", layout: "pair" });
  });

  it("asks for the layout when it is missing", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const result = await manageThumbnailConfig({ action: "set_layout" });
    expect(result.text).toContain("'layout' is required");
    expect(fetch).not.toHaveBeenCalled();
  });
});
