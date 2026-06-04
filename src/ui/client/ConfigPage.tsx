import React, { useEffect, useRef, useState } from "react";

const panelStyle: React.CSSProperties = {
  border: "1px solid rgba(255, 255, 255, 0.1)",
  borderRadius: 12,
  padding: "20px 22px",
  background: "rgba(255, 255, 255, 0.02)",
  marginBottom: 16,
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  marginBottom: 14,
};

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  opacity: 0.65,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
};

const monoStyle: React.CSSProperties = {
  fontFamily: '"JetBrains Mono", monospace',
  fontSize: 13,
  wordBreak: "break-all",
};

export default function ConfigPage() {
  const [status, setStatus] = useState<any>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [hasFile, setHasFile] = useState(false);
  const [activate, setActivate] = useState(false);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function loadStatus() {
    const resp = await fetch("/api/config/status");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    setStatus(data);
    setStatusError(null);
  }

  useEffect(() => {
    loadStatus().catch((e) => setStatusError(e.message));
  }, []);

  async function onMigrate() {
    setMsg({ text: "Migrating…", ok: true });
    try {
      const resp = await fetch("/api/config/migrate", { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      setMsg({ text: `Done. Moved ${data.moved_json || 0} cache file(s).`, ok: true });
      await loadStatus();
    } catch (e: any) {
      setMsg({ text: e.message, ok: false });
    }
  }

  function onExport() {
    window.location.href = "/api/config/export";
  }

  async function onImport() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("bundle", file);
    fd.append("activate", activate ? "1" : "0");
    setMsg({ text: "Importing…", ok: true });
    setImporting(true);
    try {
      const resp = await fetch("/api/config/import", { method: "POST", body: fd });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      let text = `Imported into ${data.home}`;
      if (data.backup) text += `. Backup: ${data.backup}`;
      setMsg({ text, ok: true });
      await loadStatus();
    } catch (e: any) {
      setMsg({ text: e.message, ok: false });
    } finally {
      setImporting(false);
    }
  }

  const m = status?.migration || {};

  return (
    <div className="app" style={{ maxWidth: 720 }}>
      <div className="header" style={{ marginBottom: 28 }}>
        <h1>Config profiles</h1>
        <p>
          Export or import your show settings (knowledge, presets, assets, corrections). Cache and
          rendered clips stay on this machine.
        </p>
      </div>

      <div className="panel" style={panelStyle}>
        {statusError ? (
          <div className="msg err" style={{ marginTop: 12, fontSize: 13, padding: "10px 12px", borderRadius: 8, background: "rgba(255, 100, 100, 0.12)" }}>
            {statusError}
          </div>
        ) : !status ? (
          <div className="status" style={{ fontSize: 14, opacity: 0.8, lineHeight: 1.5 }}>Loading…</div>
        ) : (
          <>
            <div className="row" style={rowStyle}>
              <label style={labelStyle}>Config home</label>
              <code style={monoStyle}>{status.home || ""}</code>
            </div>
            <div className="row" style={rowStyle}>
              <label style={labelStyle}>Data / cache</label>
              <code style={monoStyle}>{status.cache || ""}</code>
            </div>
            <div className="row" style={rowStyle}>
              <label style={labelStyle}>Profile marker</label>
              <code style={monoStyle}>{status.profile_marker || ""}</code>
            </div>
            <div className="row" style={rowStyle}>
              <label style={labelStyle}>Migration</label>
              <span className="mono" style={monoStyle}>{m.already_migrated ? "Up to date" : "Ran on load"}</span>
            </div>
          </>
        )}
      </div>

      <div className="panel" style={panelStyle}>
        <h2 style={{ margin: "0 0 12px 0", fontSize: 16 }}>Actions</h2>
        <div className="actions" style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
          <button type="button" className="btn" onClick={onMigrate}>Run path migration</button>
          <button type="button" className="btn btn-primary" onClick={onExport}>Download profile (.zip)</button>
        </div>
        <div className="row" style={{ ...rowStyle, marginTop: 16 }}>
          <label style={labelStyle}>Import profile bundle</label>
          <input
            type="file"
            ref={fileRef}
            accept=".zip,application/zip"
            style={{ fontSize: 13 }}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHasFile(!!e.target.files?.length)}
          />
          <label style={{ textTransform: "none", letterSpacing: 0, display: "flex", alignItems: "center", gap: 8, marginTop: 6, fontSize: 12, opacity: 0.65 }}>
            <input
              type="checkbox"
              checked={activate}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setActivate(e.target.checked)}
            />
            Set imported folder as active config home
          </label>
          <button type="button" className="btn" onClick={onImport} disabled={!hasFile || importing}>
            Import bundle
          </button>
        </div>
        {msg && (
          <div
            className={"msg " + (msg.ok ? "ok" : "err")}
            style={{
              marginTop: 12,
              fontSize: 13,
              padding: "10px 12px",
              borderRadius: 8,
              background: msg.ok ? "rgba(74, 222, 128, 0.12)" : "rgba(255, 100, 100, 0.12)",
            }}
          >
            {msg.text}
          </div>
        )}
      </div>

      <p className="footer-note" style={{ marginTop: 20, fontSize: 13, opacity: 0.55, lineHeight: 1.5 }}>
        CLI: <code>podcli config export ~/backup.zip</code> ·{" "}
        <code>podcli config import ~/backup.zip --activate</code> · MCP: <code>manage_config</code>
      </p>
    </div>
  );
}
