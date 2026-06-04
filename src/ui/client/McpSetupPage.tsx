import React, { useEffect, useRef, useState } from "react";

type StatusKind = "warn" | "ok" | "err";

export default function McpSetupPage() {
  const [distPath, setDistPath] = useState<string | null>(null);
  const [statusKind, setStatusKind] = useState<StatusKind>("warn");
  const [statusText, setStatusText] = useState("Checking connection...");

  const desktopRef = useRef<HTMLPreElement>(null);
  const codeRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState<Record<string, boolean>>({});

  useEffect(() => {
    async function init() {
      try {
        const resp = await fetch("/api/integration-info");
        const data = await resp.json();
        setDistPath(data.dist_path);
        if (data.server_ok) {
          setStatusKind("ok");
          setStatusText(`Server running - ${data.tools_count} tools available`);
        } else {
          setStatusKind("err");
          setStatusText("Server not built. Run: npm run build");
        }
      } catch {
        setStatusKind("err");
        setStatusText("Could not reach server");
      }
    }
    init();
  }, []);

  function copyBlock(id: string, ref: React.RefObject<HTMLPreElement>) {
    const text = ref.current?.innerText ?? "";
    navigator.clipboard.writeText(text).then(() => {
      setCopied((c) => ({ ...c, [id]: true }));
      setTimeout(() => setCopied((c) => ({ ...c, [id]: false })), 1500);
    });
  }

  const distDisplay = distPath ?? "loading...";

  return (
    <div className="app" style={{ maxWidth: 780 }}>
      <div className="header" style={{ marginBottom: 40 }}>
        <h1>MCP Integration</h1>
        <p className="subtitle">
          Connect podcli to Claude Desktop, Claude Code, or any MCP-compatible client to generate
          clips through conversation.
        </p>
      </div>

      <div className={"status-bar " + statusKind}>
        <div className="status-dot"></div>
        <span>{statusText}</span>
      </div>

      <div className="card">
        <div className="card-label">Setup</div>
        <div className="card-title">Claude Desktop</div>
        <div className="card-desc">
          Add podcli as an MCP server in your Claude Desktop configuration file. Claude will gain
          access to transcription, clip suggestion, and rendering tools.
        </div>

        <div className="steps">
          <div className="step">
            <div className="step-title">Open your config file</div>
            <div className="step-desc">
              macOS: <code>~/Library/Application Support/Claude/claude_desktop_config.json</code>
              <br />
              Windows: <code>%APPDATA%\Claude\claude_desktop_config.json</code>
            </div>
          </div>
          <div className="step">
            <div className="step-title">Add the podcli server entry</div>
            <div className="step-desc">
              Paste this into the <code>mcpServers</code> object:
            </div>
          </div>
        </div>

        <div className="code-block" style={{ marginBottom: 16 }}>
          <div className="code-header">
            <span className="code-filename">claude_desktop_config.json</span>
            <button
              className={"copy-btn" + (copied.desktop ? " copied" : "")}
              onClick={() => copyBlock("desktop", desktopRef)}
            >
              {copied.desktop ? "Copied" : "Copy"}
            </button>
          </div>
          <pre ref={desktopRef}>
            <span className="punct">{"{"}</span>
            {"\n  "}
            <span className="key">"mcpServers"</span>
            <span className="punct">:</span> <span className="punct">{"{"}</span>
            {"\n    "}
            <span className="key">"podcli"</span>
            <span className="punct">:</span> <span className="punct">{"{"}</span>
            {"\n      "}
            <span className="key">"command"</span>
            <span className="punct">:</span> <span className="str">"node"</span>
            <span className="punct">,</span>
            {"\n      "}
            <span className="key">"args"</span>
            <span className="punct">:</span> <span className="punct">[</span>
            <span className="str">"{distDisplay}"</span>
            <span className="punct">]</span>
            {"\n    "}
            <span className="punct">{"}"}</span>
            {"\n  "}
            <span className="punct">{"}"}</span>
            {"\n"}
            <span className="punct">{"}"}</span>
          </pre>
        </div>

        <div className="step" style={{ paddingLeft: 40, counterIncrement: "step" }}>
          <div className="step-title">Restart Claude Desktop</div>
          <div className="step-desc">
            After saving, restart the app. You should see podcli listed in the MCP tools panel.
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Claude Code (CLI)</div>
        <div className="card-desc">
          Add podcli as an MCP server in your Claude Code project or global settings.
        </div>

        <div className="steps">
          <div className="step">
            <div className="step-title">Run this command in your terminal</div>
            <div className="step-desc">
              This registers podcli as an MCP server for the current project scope:
            </div>
          </div>
        </div>

        <div className="code-block">
          <div className="code-header">
            <span className="code-filename">terminal</span>
            <button
              className={"copy-btn" + (copied.code ? " copied" : "")}
              onClick={() => copyBlock("code", codeRef)}
            >
              {copied.code ? "Copied" : "Copy"}
            </button>
          </div>
          <pre ref={codeRef}>
            {distPath === null ? (
              <span className="comment"># loading path...</span>
            ) : (
              `claude mcp add podcli node ${distPath}`
            )}
          </pre>
        </div>
      </div>

      <div className="divider"></div>

      <div className="card">
        <div className="card-label">Reference</div>
        <div className="card-title">Available MCP Tools</div>
        <div className="card-desc">These tools become available to Claude after connecting.</div>

        <div className="tool-grid">
          <div className="tool-item">
            <span className="tool-name">transcribe_podcast</span>
            <span className="tool-desc">
              Transcribe audio/video with Whisper. Supports model size selection, language detection,
              and speaker diarization.
            </span>
          </div>
          <div className="tool-item">
            <span className="tool-name">suggest_clips</span>
            <span className="tool-desc">
              Submit an array of viral moment suggestions with title, time range, and reasoning. Used
              by Claude to pass clip ideas.
            </span>
          </div>
          <div className="tool-item">
            <span className="tool-name">create_clip</span>
            <span className="tool-desc">
              Render a single short-form clip with caption style, crop strategy, and optional logo
              overlay.
            </span>
          </div>
          <div className="tool-item">
            <span className="tool-name">batch_create_clips</span>
            <span className="tool-desc">
              Render multiple clips in one call. Each clip can have its own caption style and crop
              settings.
            </span>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Example Prompts</div>
        <div className="card-desc">Try these with Claude after connecting:</div>

        <div className="tool-grid">
          <div className="tool-item" style={{ cursor: "default" }}>
            <span className="tool-desc" style={{ color: "var(--text)" }}>
              <em>
                "Transcribe /path/to/episode.mp4 and find the 5 most viral moments, then export them
                with hormozi captions"
              </em>
            </span>
          </div>
          <div className="tool-item" style={{ cursor: "default" }}>
            <span className="tool-desc" style={{ color: "var(--text)" }}>
              <em>
                "Create a 30-second clip from 12:45 to 13:15 with branded captions and face-tracking
                crop"
              </em>
            </span>
          </div>
          <div className="tool-item" style={{ cursor: "default" }}>
            <span className="tool-desc" style={{ color: "var(--text)" }}>
              <em>"Batch export all suggested clips with karaoke-style captions"</em>
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
