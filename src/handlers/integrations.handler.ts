import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { PythonExecutor } from "../services/python-executor.js";

const executor = new PythonExecutor();

interface IntegrationTool {
  name: string;
  description: string;
  tags?: string[];
}

interface IntegrationDescriptor {
  name: string;
  category: "editor_export" | "platform_upload" | "productivity" | "ai_helper";
  description: string;
  default_enabled: boolean;
  enabled: boolean;
  tools: IntegrationTool[];
}

interface ListResult { integrations: IntegrationDescriptor[] }
interface SetResult { name: string; enabled: boolean }

export const manageIntegrationsToolDef = {
  name: "manage_integrations",
  description:
    "List, enable, or disable podcli integrations (editor exporters, platform uploads, productivity tools, AI helpers).\n\n" +
    "Actions:\n" +
    "  • list    — return all installed integrations with their enabled state (default)\n" +
    "  • enable  — turn an integration on (its tools become callable)\n" +
    "  • disable — turn an integration off (calls return a disabled error)\n\n" +
    "State persists at the active config home (integrations.json, gitignored).",
};

export async function handleManageIntegrations(input: {
  action?: "list" | "enable" | "disable";
  name?: string;
}): Promise<string> {
  const action = input.action ?? "list";
  const result = await executor.execute<ListResult | SetResult>("manage_integrations", {
    action,
    name: input.name ?? "",
  });
  if (!result.data) throw new Error(`manage_integrations returned no data (action=${action})`);
  return JSON.stringify(result.data, null, 2);
}

interface DvShortInput {
  title: string;
  source_path: string;
  captions_path?: string;
  logo_path?: string;
}

interface DvExportInput {
  project_name: string;
  output_path: string;
  fps?: number;
  width?: number;
  height?: number;
  shorts: DvShortInput[];
}

export const exportToDaVinciResolveToolDef = {
  name: "export_to_davinci_resolve",
  description:
    "Export podcli shorts as a DaVinci Resolve FCPXML project.\n\n" +
    "Each short becomes a compound clip with V1 source + V2 ProRes 4444 alpha caption overlay — " +
    "fully editable in free or Studio Resolve 20.x.\n\n" +
    "Requires the davinci_resolve integration to be enabled (manage_integrations action=enable name=davinci_resolve).",
};

export async function handleExportToDaVinciResolve(input: DvExportInput): Promise<string> {
  const result = await executor.execute<Record<string, unknown>>("run_integration_tool", {
    integration: "davinci_resolve",
    tool: "export_to_davinci_resolve",
    params: input,
  });
  if (!result.data) throw new Error("export_to_davinci_resolve returned no data");
  return JSON.stringify(result.data, null, 2);
}

export const manageConfigToolDef = {
  name: "manage_config",
  description:
    "Manage portable config profiles and legacy path migration.\n\n" +
    "Actions:\n" +
    "  • status  — active config home, cache dir, migration state (default)\n" +
    "  • migrate — move legacy project/.podcli/cache into data/cache (idempotent)\n" +
    "  • export  — zip the active config home (knowledge, presets, assets, settings)\n" +
    "  • import  — restore a bundle; backs up existing config before overwrite\n" +
    "  • use     — activate a config home path (writes .podcli-home marker)",
};

export async function handleManageConfig(input: {
  action?: "status" | "migrate" | "export" | "import" | "use";
  bundle_path?: string;
  home?: string;
  activate?: boolean;
  dry_run?: boolean;
}): Promise<string> {
  const action = input.action ?? "status";
  const result = await executor.execute<Record<string, unknown>>("manage_config", {
    action,
    bundle_path: input.bundle_path,
    home: input.home,
    activate: input.activate,
    dry_run: input.dry_run,
  });
  if (!result.data) throw new Error(`manage_config returned no data (action=${action})`);
  return JSON.stringify(result.data, null, 2);
}

interface EnvSettingRow {
  key: string;
  label: string;
  help?: string;
  url?: string;
  placeholder?: string;
  secret: boolean;
  set: boolean;
  preview?: string;
}

interface AiCliStatus {
  available?: boolean;
  configured?: Record<string, string | null>;
  candidates?: Array<{ engine: string; path: string }>;
}

export const manageEnvToolDef = {
  name: "manage_env",
  description:
    "List, set, or unset global podcli settings stored in .env.\n\n" +
    "Keys:\n" +
    "  • HF_TOKEN — HuggingFace token for speaker detection\n" +
    "  • PODCLI_CLAUDE_PATH — manual path to Claude Code CLI when auto-discovery fails\n" +
    "  • PODCLI_CODEX_PATH — manual path to Codex CLI when auto-discovery fails\n\n" +
    "Actions:\n" +
    "  • list  — show all settings, configured values, and AI CLI detection (default)\n" +
    "  • set   — set a key (path must exist for PODCLI_*_PATH keys)\n" +
    "  • unset — remove a key (falls back to auto-discovery)",
};

export async function handleManageEnv(input: {
  action?: "list" | "set" | "unset";
  key?: string;
  value?: string;
}): Promise<string> {
  const action = input.action ?? "list";
  const result = await executor.execute<{
    settings?: EnvSettingRow[];
    path?: string;
    ai_cli?: AiCliStatus;
    ok?: boolean;
    key?: string;
  }>("manage_env", {
    action,
    key: input.key ?? "",
    value: input.value ?? "",
  });
  if (!result.data) throw new Error(`manage_env returned no data (action=${action})`);
  return JSON.stringify(result.data, null, 2);
}

export const aiCliStatusToolDef = {
  name: "ai_cli_status",
  description:
    "Show whether Claude Code / Codex CLIs are available for AI-powered clip suggestion and content generation.\n\n" +
    "Returns configured manual paths (PODCLI_CLAUDE_PATH / PODCLI_CODEX_PATH) and auto-discovered binaries. " +
    "Use manage_env(action=set, key=PODCLI_CLAUDE_PATH, value=...) to override when detection fails.",
};

export async function handleAiCliStatus(): Promise<string> {
  const result = await executor.execute<AiCliStatus>("ai_cli_status", {});
  if (!result.data) throw new Error("ai_cli_status returned no data");
  return JSON.stringify(result.data, null, 2);
}

function mcpText(text: string, isError = false) {
  return {
    content: [{ type: "text" as const, text }],
    ...(isError ? { isError: true as const } : {}),
  };
}

export function registerIntegrationMcpTools(server: McpServer): void {
  server.tool(
    manageIntegrationsToolDef.name,
    manageIntegrationsToolDef.description,
    {
      action: z.enum(["list", "enable", "disable"]).optional().default("list").describe("list | enable | disable"),
      name: z.string().optional().describe("Integration name (required for enable/disable)"),
    },
    async ({ action, name }) => {
      try {
        if ((action === "enable" || action === "disable") && !name?.trim()) {
          return mcpText("`name` is required when action is enable or disable.", true);
        }
        const text = await handleManageIntegrations({ action, name });
        return mcpText(text);
      } catch (err) {
        return mcpText(err instanceof Error ? err.message : String(err), true);
      }
    }
  );

  server.tool(
    exportToDaVinciResolveToolDef.name,
    exportToDaVinciResolveToolDef.description,
    {
      project_name: z.string().describe("Name of the Resolve project"),
      output_path: z.string().describe("Destination path for the .fcpxml file"),
      fps: z.number().optional().describe("Project fps (defaults to source clip's fps)"),
      width: z.number().int().optional().describe("Project width (defaults to source's width)"),
      height: z.number().int().optional().describe("Project height (defaults to source's height)"),
      shorts: z
        .array(
          z.object({
            title: z.string(),
            source_path: z.string().describe("Cropped 9:16 video for V1"),
            captions_path: z.string().optional().describe("ProRes 4444 alpha overlay for V2"),
            logo_path: z.string().optional().describe("ProRes 4444 alpha overlay for V3"),
          })
        )
        .describe("Shorts to lay on the master timeline"),
    },
    async (params) => {
      try {
        const text = await handleExportToDaVinciResolve(params);
        return mcpText(text);
      } catch (err) {
        return mcpText(err instanceof Error ? err.message : String(err), true);
      }
    }
  );

  server.tool(
    manageConfigToolDef.name,
    manageConfigToolDef.description,
    {
      action: z
        .enum(["status", "migrate", "export", "import", "use"])
        .optional()
        .default("status"),
      bundle_path: z.string().optional().describe("Zip path for export/import"),
      home: z.string().optional().describe("Config home override or target for import/use"),
      activate: z.boolean().optional().describe("After import, set imported home as active"),
      dry_run: z.boolean().optional().describe("For migrate: preview moves without changing files"),
    },
    async (params) => {
      try {
        const text = await handleManageConfig(params);
        return mcpText(text);
      } catch (err) {
        return mcpText(err instanceof Error ? err.message : String(err), true);
      }
    }
  );

  server.tool(
    manageEnvToolDef.name,
    manageEnvToolDef.description,
    {
      action: z.enum(["list", "set", "unset"]).optional().default("list"),
      key: z.string().optional().describe("HF_TOKEN | PODCLI_CLAUDE_PATH | PODCLI_CODEX_PATH"),
      value: z.string().optional().describe("Value to set (required for set)"),
    },
    async ({ action, key, value }) => {
      try {
        if (action === "set" && !key?.trim()) {
          return mcpText("`key` is required when action is set.", true);
        }
        if (action === "set" && !value?.trim()) {
          return mcpText("`value` is required when action is set.", true);
        }
        if (action === "unset" && !key?.trim()) {
          return mcpText("`key` is required when action is unset.", true);
        }
        const text = await handleManageEnv({ action, key, value });
        return mcpText(text);
      } catch (err) {
        return mcpText(err instanceof Error ? err.message : String(err), true);
      }
    }
  );

  server.tool(
    aiCliStatusToolDef.name,
    aiCliStatusToolDef.description,
    {},
    async () => {
      try {
        const text = await handleAiCliStatus();
        return mcpText(text);
      } catch (err) {
        return mcpText(err instanceof Error ? err.message : String(err), true);
      }
    }
  );
}
