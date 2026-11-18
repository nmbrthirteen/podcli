import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdirSync, mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { z } from "zod";
import { connectClient } from "./test-support/mcp-client.js";
import { FORMATS } from "./models/index.js";
import { createClipToolDef } from "./handlers/create-clip.handler.js";
import { batchClipsToolDef } from "./handlers/batch-clips.handler.js";

const tmp = mkdtempSync(join(tmpdir(), "podcli-topic-format-"));
const savedHome = process.env.PODCLI_HOME;
const savedData = process.env.PODCLI_DATA;
const savedPort = process.env.PODCLI_PORT;

let createServer: typeof import("./server.js").createServer;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  process.env.PODCLI_PORT = "18427";
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });
  ({ createServer } = await import("./server.js"));
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;

  if (savedData === undefined) delete process.env.PODCLI_DATA;
  else process.env.PODCLI_DATA = savedData;

  if (savedPort === undefined) delete process.env.PODCLI_PORT;
  else process.env.PODCLI_PORT = savedPort;
  rmSync(tmp, { recursive: true, force: true });
});

const formatOptions = z.looseObject({ enum: z.array(z.string()) });

const topLevelFormat = z.looseObject({
  properties: z.looseObject({ format: formatOptions.optional() }).optional(),
});

const batchClipFormat = z.looseObject({
  properties: z.looseObject({
    clips: z.looseObject({
      items: z.looseObject({
        properties: z.looseObject({ format: formatOptions }),
      }),
    }),
  }),
});

describe("topic format wiring", () => {
  it("lists topic after the existing formats", () => {
    expect([...FORMATS]).toEqual(["vertical", "horizontal", "square", "topic"]);
  });

  it("offers topic in the create and batch tool definitions", () => {
    expect(createClipToolDef.inputSchema.properties.format.enum).toEqual([...FORMATS]);
    expect(batchClipsToolDef.inputSchema.properties.clips.items.properties.format.enum).toEqual([...FORMATS]);
  });

  it("offers topic on every registered tool that takes a clip format, except reels", async () => {
    const client = await connectClient(createServer());

    try {
      const { tools } = await client.listTools();
      const clipFormats = new Map<string, string[]>();

      for (const tool of tools) {
        const options = topLevelFormat.parse(tool.inputSchema).properties?.format?.enum;

        if (options?.includes("vertical")) clipFormats.set(tool.name, options);
      }

      expect([...clipFormats.keys()].sort()).toEqual(["clip_history", "create_clip", "manage_reel", "process_episode"]);

      for (const [name, options] of clipFormats) {
        if (name !== "manage_reel") expect(options, name).toContain("topic");
      }

      const batch = tools.find((tool) => tool.name === "batch_create_clips");

      expect(batchClipFormat.parse(batch?.inputSchema).properties.clips.items.properties.format.enum).toContain("topic");
    } finally {
      await client.close();
    }
  });

  it("accepts topic and rejects unknown formats at the create_clip schema", async () => {
    const client = await connectClient(createServer());

    try {
      const accepted = await client.callTool({ name: "create_clip", arguments: { clip_number: 1, format: "topic" } });
      const rejected = await client.callTool({ name: "create_clip", arguments: { clip_number: 1, format: "tall" } });
      const replyText = z.looseObject({ content: z.array(z.looseObject({ text: z.string().optional() })) });
      const textOf = (reply: z.infer<typeof replyText>) => reply.content.map((block) => block.text ?? "").join("\n");

      expect(textOf(replyText.parse(accepted))).not.toMatch(/Invalid arguments/);
      expect(textOf(replyText.parse(rejected))).toMatch(/Invalid arguments/);
    } finally {
      await client.close();
    }
  });
});
