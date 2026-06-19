import React, { useEffect, useRef, useState } from "react";
import { api } from "./lib";

type StatusKind = "warn" | "ok" | "err";

const STATUS_STYLE: Record<StatusKind, React.CSSProperties> = {
  ok: { background: "var(--green-subtle)", color: "var(--green)", border: "1px solid var(--green-border)" },
  err: { background: "var(--red-subtle)", color: "var(--red)", border: "1px solid var(--red-border)" },
  warn: { background: "rgba(250,204,21,0.08)", color: "#facc15", border: "1px solid rgba(250,204,21,0.2)" },
};

export default function McpSetupPage() {
  const [mcpPath, setMcpPath] = useState<string | null>(null);
  const [statusKind, setStatusKind] = useState<StatusKind>("warn");
  const [statusText, setStatusText] = useState("Checking…");
  const desktopRef = useRef<HTMLPreElement>(null);
  const codeRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState<Record<string, boolean>>({});

  useEffect(() => {
    api<any>("/integration-info")
      .then((data) => {
        setMcpPath(data.mcp_path || data.dist_path);
        if (data.server_ok) {
          setStatusKind("ok");
          setStatusText("Ready");
        } else {
          setStatusKind("err");
          setStatusText("Not built");
        }
      })
      .catch(() => {
        setStatusKind("err");
        setStatusText("Could not reach server");
      });
  }, []);

  function copyBlock(id: string, ref: React.RefObject<HTMLPreElement>) {
    navigator.clipboard.writeText(ref.current?.innerText ?? "").then(() => {
      setCopied((c) => ({ ...c, [id]: true }));
      setTimeout(() => setCopied((c) => ({ ...c, [id]: false })), 1500);
    });
  }

  const serverPath = mcpPath ?? "<path-to>/mcp-server.mjs";
  const desktopJson = JSON.stringify(
    {
      mcpServers: {
        podcli: {
          command: "node",
          args: [serverPath],
        },
      },
    },
    null,
    2
  );

  return (
    <div className="app" style={{ maxWidth: 780 }}>
      <div className="header"><h1>MCP Setup</h1></div>

      <span className="pill" style={{ ...STATUS_STYLE[statusKind], fontSize: 11 }}>{statusText}</span>

      <div className="section" style={{ marginTop: 18 }}>
        <div className="section-label">Claude Desktop</div>
        <div className="code-block">
          <div className="code-block-head">
            <span>claude_desktop_config.json</span>
            <button className="btn btn-ghost btn-sm" style={{ padding: "3px 10px" }} onClick={() => copyBlock("desktop", desktopRef)}>
              {copied.desktop ? "Copied" : "Copy"}
            </button>
          </div>
          <pre ref={desktopRef}>{desktopJson}</pre>
        </div>
      </div>

      <div className="section">
        <div className="section-label">Claude Code</div>
        <div className="code-block">
          <div className="code-block-head">
            <span>terminal</span>
            <button className="btn btn-ghost btn-sm" style={{ padding: "3px 10px" }} onClick={() => copyBlock("code", codeRef)}>
              {copied.code ? "Copied" : "Copy"}
            </button>
          </div>
          <pre ref={codeRef}>podcli mcp install</pre>
        </div>
      </div>
    </div>
  );
}
