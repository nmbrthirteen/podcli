import React, { useEffect, useRef, useState } from "react";
import { api } from "./lib";
import CopyButton from "./CopyButton";

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
      <div className="header"><h1>MCP setup</h1></div>

      <span className="pill" style={{ ...STATUS_STYLE[statusKind], fontSize: 11 }}>{statusText}</span>

      <div className="section" style={{ marginTop: 18 }}>
        <div className="section-label">Claude Desktop</div>
        <div className="code-block">
          <div className="code-block-head">
            <span>claude_desktop_config.json</span>
            <CopyButton className="btn btn-ghost btn-sm" style={{ padding: "3px 10px" }} getText={() => desktopRef.current?.innerText ?? ""} />
          </div>
          <pre ref={desktopRef}>{desktopJson}</pre>
        </div>
      </div>

      <div className="section">
        <div className="section-label">Claude Code</div>
        <div className="code-block">
          <div className="code-block-head">
            <span>terminal</span>
            <CopyButton className="btn btn-ghost btn-sm" style={{ padding: "3px 10px" }} getText={() => codeRef.current?.innerText ?? ""} />
          </div>
          <pre ref={codeRef}>podcli mcp install</pre>
        </div>
      </div>
    </div>
  );
}
