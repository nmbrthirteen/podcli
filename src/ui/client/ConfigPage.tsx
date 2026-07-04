import React, { useEffect, useRef, useState } from "react";
import { api, upload } from "./lib";

type SettingRow = {
  key: string;
  label: string;
  help?: string;
  url?: string;
  placeholder?: string;
  secret: boolean;
  set: boolean;
  preview?: string;
};

type AiCliStatus = {
  available?: boolean;
  configured?: Record<string, string | null>;
  candidates?: Array<{ engine: string; path: string }>;
};

export default function ConfigPage() {
  const [status, setStatus] = useState<any>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [hasFile, setHasFile] = useState(false);
  const [activate, setActivate] = useState(false);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [settings, setSettings] = useState<SettingRow[]>([]);
  const [aiCli, setAiCli] = useState<AiCliStatus | null>(null);
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({});
  const [pathInputs, setPathInputs] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function loadStatus() {
    setStatus(await api("/config/status"));
    setStatusError(null);
  }

  async function loadSettings() {
    try {
      const data = await api<{ settings?: SettingRow[]; ai_cli?: AiCliStatus }>("/settings");
      setSettings(data.settings || []);
      if (data.ai_cli) {
        setAiCli(data.ai_cli);
      } else {
        try {
          setAiCli(await api<AiCliStatus>("/ai-cli-status"));
        } catch {
          setAiCli(null);
        }
      }
    } catch { /* settings are optional */ }
  }

  async function refreshAiCli() {
    try {
      setAiCli(await api<AiCliStatus>("/ai-cli-status"));
    } catch {
      setAiCli(null);
    }
  }

  useEffect(() => {
    loadStatus().catch((e) => setStatusError(e.message));
    loadSettings();
  }, []);

  async function saveSetting(key: string, secret: boolean) {
    setSavingKey(key);
    setMsg(null);
    const value = secret ? (secretInputs[key] ?? "") : (pathInputs[key] ?? "");
    try {
      await api("/settings", { method: "POST", body: JSON.stringify({ key, value }) });
      if (secret) {
        setSecretInputs((p) => ({ ...p, [key]: "" }));
      } else {
        setPathInputs((p) => ({ ...p, [key]: "" }));
      }
      setMsg({ text: `${key} saved.`, ok: true });
      await loadSettings();
      await refreshAiCli();
    } catch (e: any) {
      setMsg({ text: e.message, ok: false });
    } finally {
      setSavingKey(null);
    }
  }

  async function clearSetting(key: string) {
    setSavingKey(key);
    setMsg(null);
    try {
      await api("/settings", { method: "POST", body: JSON.stringify({ key, value: "" }) });
      setPathInputs((p) => ({ ...p, [key]: "" }));
      setMsg({ text: `${key} cleared.`, ok: true });
      await loadSettings();
      await refreshAiCli();
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

  const pathSettings = settings.filter((s) => !s.secret);
  const secretSettings = settings.filter((s) => s.secret);

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
        <div className="section-label">AI CLI</div>
        {aiCli ? (
          <>
            <div className="set-kv">
              <span className="k">Status</span>
              <span className="v" style={{ color: aiCli.available ? "var(--green)" : "var(--yellow)" }}>
                {aiCli.available
                  ? `Found ${aiCli.candidates?.map((c) => c.engine).join(", ") || "CLI"}`
                  : "Not detected — set a path below or install Claude Code / Codex"}
              </span>
            </div>
            {(aiCli.candidates || []).map((c) => (
              <div className="set-kv" key={`${c.engine}-${c.path}`}>
                <span className="k">{c.engine}</span>
                <span className="v" style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{c.path}</span>
              </div>
            ))}
          </>
        ) : (
          <div style={{ color: "var(--text2)", fontSize: 13 }}>Checking for Claude Code / Codex…</div>
        )}
        {pathSettings.map((s) => (
          <div key={s.key} style={{ marginTop: 16 }}>
            <label className="field-label">
              {s.label}{" "}
              <span style={{ color: s.set ? "var(--green)" : "var(--text3)", fontSize: 11 }}>
                {s.set ? (s.preview || "set") : "auto"}
              </span>
            </label>
            {s.help && (
              <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 8 }}>{s.help}</div>
            )}
            <div className="set-file">
              <input
                type="text"
                placeholder={s.set ? "Replace path" : (s.placeholder || "/path/to/claude")}
                value={pathInputs[s.key] ?? ""}
                onChange={(e) => setPathInputs((p) => ({ ...p, [s.key]: e.target.value }))}
                style={{ fontSize: 13, flex: 1, fontFamily: "var(--font-mono)" }}
              />
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => saveSetting(s.key, false)}
                disabled={savingKey === s.key || !(pathInputs[s.key] ?? "").trim()}
              >
                {savingKey === s.key ? "Saving…" : "Save"}
              </button>
              {s.set && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => clearSetting(s.key)}
                  disabled={savingKey === s.key}
                >
                  Clear
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {secretSettings.length > 0 && (
        <div className="section">
          <div className="section-label">Secrets</div>
          {secretSettings.map((s) => (
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
                  onClick={() => saveSetting(s.key, true)}
                  disabled={savingKey === s.key || !(secretInputs[s.key] ?? "").trim()}
                >
                  {savingKey === s.key ? "Saving…" : "Save"}
                </button>
              </div>
              {s.url && (
                <a href={s.url} target="_blank" rel="noopener" className="set-link">Get token</a>
              )}
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
