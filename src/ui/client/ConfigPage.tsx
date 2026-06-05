import React, { useEffect, useRef, useState } from "react";
import { api, upload } from "./lib";

export default function ConfigPage() {
  const [status, setStatus] = useState<any>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [hasFile, setHasFile] = useState(false);
  const [activate, setActivate] = useState(false);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function loadStatus() {
    setStatus(await api("/config/status"));
    setStatusError(null);
  }

  useEffect(() => {
    loadStatus().catch((e) => setStatusError(e.message));
  }, []);

  async function onMigrate() {
    setMsg({ text: "Migrating…", ok: true });
    try {
      const data = await api<any>("/config/migrate", { method: "POST" });
      setMsg({ text: `Moved ${data.moved_json || 0} cache file(s).`, ok: true });
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
      const data = await upload<any>("/config/import", fd);
      setMsg({ text: data.backup ? `Imported into ${data.home} · backup ${data.backup}` : `Imported into ${data.home}`, ok: true });
      await loadStatus();
    } catch (e: any) {
      setMsg({ text: e.message, ok: false });
    } finally {
      setImporting(false);
    }
  }

  const rows: Array<[string, string]> = status
    ? [
        ["Config home", status.home || ""],
        ["Data / cache", status.cache || ""],
        ["Profile marker", status.profile_marker || ""],
        ["Migration", status.migration?.already_migrated ? "Up to date" : "Ran on load"],
      ]
    : [];

  return (
    <div className="app" style={{ maxWidth: 760 }}>
      <div className="header"><h1>Config</h1></div>

      <div className="section">
        {statusError ? (
          <div className="set-note err">{statusError}</div>
        ) : !status ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text2)" }}>
            <div className="spinner sm" /> Loading…
          </div>
        ) : (
          rows.map(([k, v]) => (
            <div className="set-kv" key={k}>
              <span className="k">{k}</span>
              <span className="v">{v}</span>
            </div>
          ))
        )}
      </div>

      <div className="section">
        <div className="section-label">Actions</div>
        <div className="set-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onMigrate}>Run path migration</button>
          <button type="button" className="btn btn-primary btn-sm" onClick={onExport}>Download profile (.zip)</button>
        </div>

        <div style={{ marginTop: 18 }}>
          <label className="field-label">Import profile bundle</label>
          <div className="set-file">
            <input
              type="file"
              ref={fileRef}
              accept=".zip,application/zip"
              style={{ fontSize: 13 }}
              onChange={(e) => setHasFile(!!e.target.files?.length)}
            />
            <button type="button" className="btn btn-ghost btn-sm" onClick={onImport} disabled={!hasFile || importing}>
              {importing ? "Importing…" : "Import"}
            </button>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: 12, color: "var(--text2)" }}>
            <input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} />
            Set imported folder as active config home
          </label>
        </div>

        {msg && <div className={`set-note ${msg.ok ? "ok" : "err"}`}>{msg.text}</div>}
      </div>
    </div>
  );
}
