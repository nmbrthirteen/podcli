import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

export interface ToolReply {
  text: string;
  isError: boolean;
}

/** Connect a client to the server over a linked in-memory pair, so tests call tools through the public protocol. */
export async function connectClient(server: McpServer): Promise<Client> {
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "podcli-test", version: "0.0.0" });
  await Promise.all([server.connect(serverSide), client.connect(clientSide)]);

  return client;
}

export interface ToolArgs {
  [field: string]: string | number | boolean | string[];
}

export async function callTool(server: McpServer, name: string, args: ToolArgs): Promise<ToolReply> {
  const client = await connectClient(server);

  try {
    const reply = await client.callTool({ name, arguments: args });
    const content = Array.isArray(reply.content) ? reply.content : [];

    return {
      text: content.flatMap((block) => (block.type === "text" ? [block.text] : [])).join("\n"),
      isError: reply.isError === true,
    };
  } finally {
    await client.close();
  }
}

export async function listToolNames(server: McpServer): Promise<Set<string>> {
  const client = await connectClient(server);

  try {
    const { tools } = await client.listTools();

    return new Set(tools.map((tool) => tool.name));
  } finally {
    await client.close();
  }
}
