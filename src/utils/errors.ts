/** Extract a human-readable message from an unknown thrown value. */
export function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

type McpTextResult = {
  content: Array<{ type: "text"; text: string }>;
  isError?: boolean;
};

/** Standard MCP error result for a caught exception. */
export function mcpError(err: unknown): McpTextResult {
  return {
    content: [{ type: "text", text: `Error: ${errMsg(err)}` }],
    isError: true,
  };
}

/** Standard MCP success result wrapping a single text payload. */
export function mcpText(text: string): McpTextResult {
  return { content: [{ type: "text", text }] };
}
