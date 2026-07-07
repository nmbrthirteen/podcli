import React, { useEffect, useState } from "react";
import { PageHeader } from "./Page";
import { api } from "./lib";

const CATEGORY: Record<string, string> = {
  editor_export: "Editor export",
  platform_upload: "Platform upload",
  productivity: "Productivity",
  ai_helper: "AI helper",
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  async function load() {
    try {
      const data = await api<any>("/integrations");
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
      await api(`/integrations/${encodeURIComponent(name)}`, {
        method: "POST",
        body: JSON.stringify({ enabled: next }),
      });
      setIntegrations((list) => (list || []).map((i) => (i.name === name ? { ...i, enabled: next } : i)));
    } catch (err: any) {
      alert(`Failed to ${next ? "enable" : "disable"} ${name}: ${err.message}`);
    } finally {
      setBusy((b) => ({ ...b, [name]: false }));
    }
  }

  return (
    <div className="app" style={{ maxWidth: 820 }}>
      <PageHeader title="Integrations" />

      {error ? (
        <div className="set-note err">{error}</div>
      ) : integrations === null ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text2)" }}>
          <div className="spinner sm" /> Loading…
        </div>
      ) : integrations.length === 0 ? (
        <div className="drop-zone" style={{ textAlign: "center", padding: "40px 20px", color: "var(--text2)" }}>
          No integrations installed.
        </div>
      ) : (
        <div className="section">
          {integrations.map((int) => (
            <div key={int.name} className="int-row">
              <div style={{ minWidth: 0 }}>
                <div className="name">
                  {int.name}
                  <span className="pill pill-blue" style={{ marginLeft: 8, fontSize: 10 }}>
                    {CATEGORY[int.category] || int.category}
                  </span>
                </div>
                {int.description && <div className="desc">{int.description}</div>}
                {(int.tools || []).length > 0 && (
                  <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {int.tools.map((t: any) => (
                      <span key={t.name} className="pill" style={{ fontSize: 10, fontFamily: "var(--font-mono, monospace)" }}>{t.name}</span>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                className={`toggle ${int.enabled ? "on" : ""}`}
                role="switch"
                aria-checked={!!int.enabled}
                aria-label={`Toggle ${int.name}`}
                disabled={busy[int.name]}
                onClick={() => onToggle(int)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
