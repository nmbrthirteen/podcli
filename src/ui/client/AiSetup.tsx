import React, { useEffect, useState } from "react";
import { Cloud, Terminal, Key } from "lucide-react";
import { labelStyle } from "./lib";

/**
 * What podcli will use for AI, and what to do when the answer is "nothing".
 *
 * Two real options are offered side by side and neither is dressed up as the
 * only one: install a CLI you already pay for, or let us run it. A user who
 * picks the free path has solved their problem, which is the point.
 */

type Provider = { kind: string; engine: string; label: string };

type Status = {
  available: boolean;
  providers: Provider[];
  mode: string;
  api_key_set: boolean;
  candidates: Array<{ engine: string; path: string }>;
};

const INSTALL_COMMAND = "npm install -g @anthropic-ai/claude-code";

function Option({
  icon, title, body, action,
}: {
  icon: React.ReactNode; title: string; body: string; action: React.ReactNode;
}) {
  return (
    <div className="card" style={{ flex: 1, minWidth: 240, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        {icon}
        <strong style={{ fontSize: 13 }}>{title}</strong>
      </div>
      <div className="hint" style={{ marginBottom: 12 }}>{body}</div>
      {action}
    </div>
  );
}

export default function AiSetup() {
  const [status, setStatus] = useState<Status | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetch("/api/ai-provider-status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (!status) return null;

  if (status.available) {
    return (
      <div className="section card">
        <div style={labelStyle}>AI</div>
        <div className="set-kv">
          <span className="k">Using</span>
          <span className="v" style={{ color: "var(--green)" }}>
            {status.providers.map((p) => p.label).join(" → ")}
          </span>
        </div>
        {status.providers.length > 1 && (
          <div className="hint" style={{ marginTop: 8 }}>
            podcli tries these in order, so a failure falls through to the next one
            rather than stopping.
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="section card">
      <div style={labelStyle}>AI is not set up</div>
      <div className="hint" style={{ marginBottom: 16 }}>
        podcli transcribes, cuts, and renders without any of this. Picking moments,
        titles, and descriptions needs a model. Two ways to get one:
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <Option
          icon={<Terminal className="ico" strokeWidth={1.8} size={15} />}
          title="Use Claude Code"
          body="Free with a Claude subscription you may already have. Runs on this machine; nothing leaves it."
          action={
            <button
              className="btn btn-ghost btn-sm"
              style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}
              onClick={() => {
                navigator.clipboard.writeText(INSTALL_COMMAND);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? "Copied" : INSTALL_COMMAND}
            </button>
          }
        />

        <Option
          icon={<Cloud className="ico" strokeWidth={1.8} size={15} />}
          title="Use podcli Pro"
          body="Nothing to install. Faster, and picks moments using what has actually performed on your channel."
          action={
            <a className="btn btn-primary btn-sm" href="https://podcli.com/pro"
               target="_blank" rel="noreferrer">
              See podcli Pro
            </a>
          }
        />

        <Option
          icon={<Key className="ico" strokeWidth={1.8} size={15} />}
          title="Use your own API key"
          body="Set ANTHROPIC_API_KEY and podcli calls the API directly. You pay per token."
          action={
            <a className="btn btn-ghost btn-sm" href="/config">Open config</a>
          }
        />
      </div>

      {status.candidates.length > 0 && (
        // Found but unusable is a different problem from missing, and saying
        // "not detected" here would send someone to reinstall what they have.
        <div className="hint" style={{ marginTop: 14 }}>
          A CLI was found at <code>{status.candidates[0].path}</code> but did not respond.
          Run <code>{status.candidates[0].engine}</code> once in a terminal to sign in, then reload.
        </div>
      )}
    </div>
  );
}
