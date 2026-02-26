#!/usr/bin/env node
/**
 * podcli — MCP Server
 *
 * Entry point — starts the MCP server over stdio transport.
 * Connect this to Claude Desktop, Claude Code, or any MCP client.
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createServer } from "./server.js";
import { FileManager } from "./services/file-manager.js";

async function main() {
  // Ensure working directories exist
  const fileManager = new FileManager();
  await fileManager.ensureDirectories();

  // Create MCP server with all tools
  const server = createServer();

  // Start stdio transport (for Claude Desktop / Claude Code integration)
  const transport = new StdioServerTransport();
  await server.connect(transport);

  console.error("podcli MCP server running on stdio");
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
