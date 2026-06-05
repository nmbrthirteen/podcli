import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmt } from "./lib";

interface Row { key: string; count: number; avgViews: number; avgRetention: number; avgCtr: number }
interface Data {
  published: number; total: number;
  byContentType: Row[]; byCaptionStyle: Row[]; byLength: Row[];
  top: Array<{ id: string; title: string; content_type?: string; caption_style: string; duration: number; metrics: any }>;
}

const labelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: "0.5px", textTransform: "uppercase", color: "var(--text2)", marginBottom: 6, display: "block" };
const fmtViews = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

function Group({ title, rows, metric }: { title: string; rows: Row[]; metric: "avgRetention" | "avgCtr" | "avgViews" }) {
  const max = Math.max(1, ...rows.map((r) => r[metric]));
  const unit = metric === "avgViews" ? "" : "%";
  return (
    <div className="section">
      <div className="section-label">{title}</div>
      {rows.length === 0 ? (
        <div style={{ color: "var(--text3)", fontSize: 12 }}>No data yet</div>
      ) : rows.map((r) => (
        <div key={r.key} style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
            <span style={{ color: "var(--text)" }}>{r.key} <span style={{ color: "var(--text3)" }}>· {r.count}</span></span>
            <span style={{ color: "var(--text2)", fontVariantNumeric: "tabular-nums" }}>{metric === "avgViews" ? fmtViews(r[metric]) : r[metric] + unit}</span>
          </div>
          <div style={{ height: 6, borderRadius: 3, background: "var(--surface2)" }}>
            <div style={{ width: `${(r[metric] / max) * 100}%`, height: "100%", borderRadius: 3, background: "var(--accent)" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function AnalyticsPage() {
  const [data, setData] = useState<Data | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [showConnect, setShowConnect] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [hasSecret, setHasSecret] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => {
    api("/analytics").then(setData).catch(() => setData(null));
    api("/youtube/status").then(setStatus).catch(() => {});
    api("/youtube/config").then((c) => { setClientId(c.client_id || ""); setHasSecret(!!c.has_secret); }).catch(() => {});
  };
  useEffect(load, []);

  const saveConfig = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api("/youtube/config", { method: "PUT", body: JSON.stringify({ client_id: clientId, ...(clientSecret ? { client_secret: clientSecret } : {}) }) });
      if (r.error) throw new Error(r.error);
      setClientSecret(""); setHasSecret(true);
      setMsg("Saved — now run  podcli youtube auth  in your terminal to authorize");
    } catch (e: any) { setMsg(`Save failed: ${e.message}`); } finally { setBusy(false); }
  };

  const sync = async (csvPath?: string) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api("/youtube/sync", { method: "POST", body: JSON.stringify(csvPath ? { csv_path: csvPath } : {}) });
      if (r.error) throw new Error(r.error);
      setMsg("Synced"); load();
    } catch (e: any) { setMsg(`Sync failed: ${e.message}`); } finally { setBusy(false); }
  };

  const importCsv = async (f: File) => {
    setBusy(true); setMsg(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const up = await (await fetch("/api/upload", { method: "POST", body: fd })).json();
      if (!up.file_path) throw new Error(up.error || "upload failed");
      await sync(up.file_path);
    } catch (e: any) { setMsg(`Import failed: ${e.message}`); setBusy(false); }
  };

  return (
    <div className="app">
      <div className="header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h1 style={{ margin: 0 }}>Analytics</h1>
          <div className="set-actions">
            <input ref={fileRef} type="file" accept=".csv" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && importCsv(e.target.files[0])} />
            <button className="btn btn-ghost btn-sm" onClick={() => setShowConnect((v) => !v)} disabled={busy}>Connect</button>
            <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={busy}>Import CSV</button>
            <button className="btn btn-primary btn-sm" onClick={() => sync()} disabled={busy}>{busy ? <div className="spinner sm" /> : "Sync YouTube"}</button>
          </div>
        </div>
        {status && (
          <div style={{ fontSize: 12, color: "var(--text3)", marginTop: 8 }}>
            {status.authorized ? "YouTube connected" : "Not connected"} · {status.with_metrics}/{status.total} clips with metrics
          </div>
        )}
        {msg && <div className="set-note ok" style={{ marginTop: 10, wordBreak: "break-all" }}>{msg}</div>}
      </div>

      {showConnect && (
        <div className="section">
          <div className="section-label">Connect YouTube (read-only)</div>
          <div className="thumb-fields">
            <div>
              <label style={labelStyle}>OAuth client ID</label>
              <input type="text" value={clientId} onChange={(e) => setClientId(e.target.value)} style={{ width: "100%", fontSize: 13, padding: "8px 11px" }} />
            </div>
            <div>
              <label style={labelStyle}>OAuth client secret {hasSecret ? "(saved)" : ""}</label>
              <input type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} placeholder={hasSecret ? "••••••••" : ""} style={{ width: "100%", fontSize: 13, padding: "8px 11px" }} />
            </div>
          </div>
          <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center" }}>
            <button className="btn btn-primary btn-sm" onClick={saveConfig} disabled={busy || !clientId}>Save credentials</button>
            <span style={{ fontSize: 12, color: "var(--text3)" }}>then run <code>podcli youtube auth</code> to authorize · or use Import CSV (no auth)</span>
          </div>
        </div>
      )}

      {!data || data.published === 0 ? (
        <div className="drop-zone" style={{ textAlign: "center", padding: "48px 20px", color: "var(--text2)" }}>
          <div className="icon" style={{ fontSize: 26 }}>📊</div>
          <div className="label" style={{ marginTop: 8 }}>No performance data yet.</div>
          <div style={{ fontSize: 12, color: "var(--text3)", marginTop: 6 }}>
            Connect YouTube and Sync, or Import a YouTube Studio analytics CSV.
          </div>
        </div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16, marginBottom: 8 }}>
            <Group title="Retention by content type — what holds viewers" rows={data.byContentType} metric="avgRetention" />
            <Group title="CTR by caption style — packaging" rows={data.byCaptionStyle} metric="avgCtr" />
            <Group title="Retention by length" rows={data.byLength} metric="avgRetention" />
            <Group title="Views by content type — reach" rows={data.byContentType} metric="avgViews" />
          </div>

          <div className="section">
            <div className="section-label">Top clips</div>
            {data.top.map((c) => (
              <Link key={c.id} to={`/clip/${c.id}`} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 0", borderBottom: "1px solid var(--border)", textDecoration: "none", color: "inherit" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</div>
                  <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>{fmt(c.duration)} · {c.caption_style}{c.content_type ? ` · ${c.content_type}` : ""}</div>
                </div>
                <div style={{ display: "flex", gap: 16, fontSize: 12, fontVariantNumeric: "tabular-nums" }}>
                  <span title="Views (reach)">{fmtViews(c.metrics?.views || 0)} views</span>
                  {c.metrics?.retention != null && <span style={{ color: "var(--text2)" }} title="Retention (content)">{c.metrics.retention}% ret</span>}
                  {c.metrics?.ctr != null && <span style={{ color: "var(--text2)" }} title="CTR (packaging)">{c.metrics.ctr}% ctr</span>}
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
