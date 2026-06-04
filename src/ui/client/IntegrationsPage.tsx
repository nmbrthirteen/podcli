import React, { useEffect, useState } from "react";

const cardStyle: React.CSSProperties = {
  border: "1px solid rgba(255, 255, 255, 0.1)",
  borderRadius: 12,
  padding: "20px 22px",
  background: "rgba(255, 255, 255, 0.02)",
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

function categoryLabel(cat: string): string {
  return (
    {
      editor_export: "Editor export",
      platform_upload: "Platform upload",
      productivity: "Productivity",
      ai_helper: "AI helper",
    } as Record<string, string>
  )[cat] || cat;
}

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  async function load() {
    try {
      const resp = await fetch("/api/integrations");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setIntegrations(data.integrations || []);
      setError(null);
    } catch (err: any) {
      setError(err.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function onToggle(int: any) {
    const name = int.name;
    const next = !int.enabled;
    setBusy((b) => ({ ...b, [name]: true }));
    try {
      const resp = await fetch(`/api/integrations/${encodeURIComponent(name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${resp.status}`);
      }
      setIntegrations((list) =>
        (list || []).map((i) => (i.name === name ? { ...i, enabled: next } : i))
      );
    } catch (err: any) {
      alert(`Failed to ${next ? "enable" : "disable"} ${name}: ${err.message}`);
    } finally {
      setBusy((b) => ({ ...b, [name]: false }));
    }
  }

  return (
    <div className="app" style={{ maxWidth: 860 }}>
      <div className="header" style={{ marginBottom: 32 }}>
        <h1 style={{ margin: "0 0 8px 0" }}>Output integrations</h1>
        <p style={{ margin: 0, opacity: 0.7 }}>
          Enable the destinations you want podcli to push to. Disabled integrations error out with a
          hint when called from MCP.
        </p>
      </div>

      <div className="int-grid" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {error ? (
          <div className="error-state" style={{ padding: 32, textAlign: "center", border: "1px dashed rgba(255, 0, 0, 0.3)", borderRadius: 12, color: "#ff8b8b" }}>
            Failed to load: {error}
          </div>
        ) : integrations === null ? (
          <div className="empty-state" style={{ padding: 32, textAlign: "center", border: "1px dashed rgba(255, 255, 255, 0.15)", borderRadius: 12, opacity: 0.6 }}>
            Loading…
          </div>
        ) : integrations.length === 0 ? (
          <div className="empty-state" style={{ padding: 32, textAlign: "center", border: "1px dashed rgba(255, 255, 255, 0.15)", borderRadius: 12, opacity: 0.6 }}>
            No integrations installed.
          </div>
        ) : (
          integrations.map((int) => (
            <div key={int.name} className="int-card" style={cardStyle} data-name={int.name}>
              <div className="int-card-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
                <div>
                  <h2 className="int-name" style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>
                    {int.name}
                    <span
                      className="int-cat"
                      style={{
                        display: "inline-block",
                        fontSize: 11,
                        letterSpacing: "0.04em",
                        textTransform: "uppercase",
                        padding: "3px 8px",
                        borderRadius: 999,
                        background: "rgba(120, 180, 255, 0.15)",
                        color: "#6aa3ff",
                        marginLeft: 8,
                        verticalAlign: "middle",
                      }}
                    >
                      {categoryLabel(int.category)}
                    </span>
                  </h2>
                  <p className="int-desc" style={{ opacity: 0.75, fontSize: 14, lineHeight: 1.45, margin: 0 }}>
                    {int.description || ""}
                  </p>
                </div>
                <button
                  type="button"
                  className={"toggle" + (int.enabled ? " on" : "") + (busy[int.name] ? " busy" : "")}
                  role="switch"
                  aria-checked={!!int.enabled}
                  aria-label={`Toggle ${int.name} integration`}
                  onClick={() => onToggle(int)}
                />
              </div>
              <div className="int-tools" style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 12, opacity: 0.55 }}>
                {(int.tools || []).map((t: any) => (
                  <span
                    key={t.name}
                    className="int-tool-pill"
                    style={{ display: "inline-block", padding: "2px 8px", borderRadius: 4, background: "rgba(255, 255, 255, 0.05)", marginRight: 6 }}
                  >
                    {t.name}
                  </span>
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      <p className="footer-note" style={{ marginTop: 24, fontSize: 13, opacity: 0.55, lineHeight: 1.5 }}>
        State persists at the active config home (<code style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 12 }}>integrations.json</code>, gitignored). Adding a
        new integration: drop a subpackage in{" "}
        <code style={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 12 }}>backend/services/integrations/</code> and it appears here automatically.
      </p>
    </div>
  );
}
