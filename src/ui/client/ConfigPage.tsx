import React, { useEffect, useRef, useState } from "react";
import { api, upload } from "./lib";

export default function ConfigPage() {
  const [status, setStatus] = useState<any>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [hasFile, setHasFile] = useState(false);
  const [activate, setActivate] = useState(false);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [settings, setSettings] = useState<any[]>([]);
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function loadStatus() {
    setStatus(await api("/config/status"));
    setStatusError(null);
  }

  async function loadSettings() {
    try {
      const data = await api<any>("/settings");
      setSettings(data.settings || []);
    } catch { /* settings are optional */ }
  }

  useEffect(() => {
    loadStatus().catch((e) => setStatusError(e.message));
    loadSettings();
  }, []);

  async function saveSetting(key: string) {
    setSavingKey(key);
    setMsg(null);
    try {
      await api("/settings", { method: "POST", body: JSON.stringify({ key, value: secretInputs[key] ?? "" }) });
      setSecretInputs((p) => ({ ...p, [key]: "" }));
      setMsg({ text: `${key} saved.`, ok: true });
      await loadSettings();
    } catch (e: any) {
      setMsg({ text: e.message, ok: false });
    } finally {
      setSavingKey(null);
    }
  }

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
        ["Cache", status.cache || ""],
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

      {settings.length > 0 && (
        <div className="section">
          <div className="section-label">Secrets</div>
          {settings.map((s) => (
            <div key={s.key} style={{ marginBottom: 16 }}>
              <label className="field-label">
                {s.label}{" "}
                <span style={{ color: s.set ? "var(--green)" : "var(--text3)", fontSize: 11 }}>
                  {s.set ? (s.preview || "set") : "not set"}
                </span>
              </label>
              <div className="set-file">
                <input
                  type="password"
                  placeholder={s.set ? "Replace token" : "hf_..."}
                  value={secretInputs[s.key] ?? ""}
                  onChange={(e) => setSecretInputs((p) => ({ ...p, [s.key]: e.target.value }))}
                  style={{ fontSize: 13, flex: 1 }}
                />
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => saveSetting(s.key)}
                  disabled={savingKey === s.key || !(secretInputs[s.key] ?? "").trim()}
                >
                  {savingKey === s.key ? "Saving…" : "Save"}
                </button>
              </div>
              <a href={s.url} target="_blank" rel="noopener" className="set-link">Get token</a>
            </div>
          ))}
        </div>
      )}

      <div className="section">
        <div className="section-label">Actions</div>
        <div className="set-actions">
          <button type="button" className="btn btn-primary btn-sm" onClick={onExport}>Export profile</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onMigrate}>Migrate paths</button>
        </div>

        <div style={{ marginTop: 18 }}>
          <label className="field-label">Import profile</label>
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
            Use imported profile
          </label>
        </div>

        {msg && <div className={`set-note ${msg.ok ? "ok" : "err"}`}>{msg.text}</div>}
      </div>
    </div>
  );
}
