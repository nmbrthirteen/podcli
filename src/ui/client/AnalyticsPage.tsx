import React, { useEffect, useRef, useState } from "react";
import { PageHeader } from "./Page";
import { Link } from "react-router-dom";
import { TrendingUp, Eye, Percent, MousePointerClick } from "lucide-react";
import { api, upload, fmt } from "./lib";

interface Row { key: string; count: number; avgViews: number; avgRetention: number; avgCtr: number }
interface Data {
  published: number; total: number;
  byContentType: Row[]; byCaptionStyle: Row[]; byLength: Row[];
  top: Array<{ id: string; title: string; content_type?: string; caption_style: string; duration: number; metrics: any }>;
}

const fmtViews = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n)));

function weighted(rows: Row[], metric: "avgRetention" | "avgCtr") {
  const total = rows.reduce((s, r) => s + r.count, 0);
  if (!total) return 0;
  return Math.round(rows.reduce((s, r) => s + r[metric] * r.count, 0) / total);
}
function totalViews(rows: Row[]) {
  return rows.reduce((s, r) => s + r.avgViews * r.count, 0);
}
function best(rows: Row[], metric: "avgRetention" | "avgCtr" | "avgViews") {
  return rows.length ? rows.reduce((a, b) => (b[metric] > a[metric] ? b : a)) : null;
}

function StatTile({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div className="stat-tile">
      <div className="stat-icon">{icon}</div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function BarGroup({ title, hint, rows, metric }: { title: string; hint: string; rows: Row[]; metric: "avgRetention" | "avgCtr" | "avgViews" }) {
  const max = Math.max(1, ...rows.map((r) => r[metric]));
  const unit = metric === "avgViews" ? "" : "%";
  return (
    <div className="section card">
      <div className="section-label" style={{ marginBottom: 2 }}>{title}</div>
      <div className="hint" style={{ marginBottom: 14 }}>{hint}</div>
      {rows.length === 0 ? (
        <div className="analytics-empty">No data yet</div>
      ) : rows.map((r) => (
        <div key={r.key} className="bar-row">
          <div className="bar-head">
            <span className="bar-key">{r.key} <span className="bar-count">· {r.count}</span></span>
            <span className="bar-val">{metric === "avgViews" ? fmtViews(r[metric]) : r[metric] + unit}</span>
          </div>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(r[metric] / max) * 100}%` }} /></div>
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
  const [proposals, setProposals] = useState<any[] | null>(null);
  const [linkBusy, setLinkBusy] = useState<string | null>(null);
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
      setMsg("Saved. Run  podcli youtube auth  in your terminal to authorize");
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

  const loadProposals = async () => {
    setBusy(true); setMsg(null); setProposals(null);
    try {
      const r = await api<any>("/youtube/links");
      setProposals(r.proposals || []);
    } catch (e: any) { setMsg(`Could not load links: ${e.message}`); } finally { setBusy(false); }
  };

  const linkClip = async (clipId: string, videoId: string) => {
    setLinkBusy(clipId);
    try {
      await api("/youtube/link", { method: "POST", body: JSON.stringify({ clip_id: clipId, video_id: videoId }) });
      setProposals((ps) => (ps || []).filter((p) => p.clip_id !== clipId));
      load();
    } catch (e: any) { setMsg(`Link failed: ${e.message}`); } finally { setLinkBusy(null); }
  };

  const analyze = async () => {
    setBusy(true); setMsg("Analyzing top performers vs underperformers…");
    try {
      const r = await api("/youtube/learn", { method: "POST", body: "{}" });
      if (r.error) throw new Error(r.error);
      setMsg("Analysis written to the knowledge base. Claude will use it when picking shorts");
    } catch (e: any) { setMsg(`Analysis failed: ${e.message}`); } finally { setBusy(false); }
  };

  const importCsv = async (f: File) => {
    setBusy(true); setMsg(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const up = await upload<any>("/upload", fd);
      if (!up.file_path) throw new Error("upload failed");
      await sync(up.file_path);
    } catch (e: any) { setMsg(`Import failed: ${e.message}`); setBusy(false); }
  };

  const hasData = !!data && data.published > 0;
  const bestRetention = data ? best(data.byContentType, "avgRetention") : null;
  const bestCtr = data ? best(data.byCaptionStyle, "avgCtr") : null;
  const bestLength = data ? best(data.byLength, "avgRetention") : null;
  const insights = [
    bestRetention && { label: "Holds viewers best", value: bestRetention.key, metric: `${bestRetention.avgRetention}% retention` },
    bestCtr && { label: "Best packaging", value: `${bestCtr.key} captions`, metric: `${bestCtr.avgCtr}% CTR` },
    bestLength && { label: "Ideal length", value: bestLength.key, metric: `${bestLength.avgRetention}% retention` },
  ].filter(Boolean) as { label: string; value: string; metric: string }[];

  return (
    <div className="app">
      <PageHeader
        title="Analytics"
        actions={<>
          <input ref={fileRef} type="file" accept=".csv" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && importCsv(e.target.files[0])} />
          <button className="btn btn-ghost btn-sm" onClick={() => setShowConnect((v) => !v)} disabled={busy}>Connect</button>
          {status?.authorized && <button className="btn btn-ghost btn-sm" onClick={loadProposals} disabled={busy}>Link clips</button>}
          <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={busy}>Import CSV</button>
          <button className="btn btn-ghost btn-sm" onClick={analyze} disabled={busy || !hasData}>Teach Claude</button>
          <button className="btn btn-primary btn-sm" onClick={() => sync()} disabled={busy}>{busy ? <div className="spinner sm" /> : "Sync YouTube"}</button>
        </>}
      />

      <div className="analytics-status">
        <span className={`status-dot ${status?.authorized ? "on" : ""}`} />
        {status ? (status.authorized ? "YouTube connected" : "Not connected") : "…"}
        {status && <span className="analytics-status-sub">· {status.with_metrics}/{status.total} clips with metrics</span>}
      </div>
      {msg && <div className="set-note ok" style={{ marginTop: 10, wordBreak: "break-all" }}>{msg}</div>}

      {showConnect && (
        <div className="section card">
          <div className="section-label">Connect YouTube (read-only)</div>
          <ol className="analytics-steps">
            <li>In Google Cloud Console, enable the <strong>YouTube Data API v3</strong> + <strong>YouTube Analytics API</strong>.</li>
            <li>Create an <strong>OAuth client ID</strong> (type: Desktop app) and paste its ID + secret below.</li>
            <li>Save, then run <code>podcli youtube auth</code> in your terminal to authorize.</li>
          </ol>
          <div className="thumb-fields">
            <div>
              <label className="section-label">OAuth client ID</label>
              <input type="text" value={clientId} onChange={(e) => setClientId(e.target.value)} style={{ width: "100%" }} />
            </div>
            <div>
              <label className="section-label">OAuth client secret {hasSecret ? "(saved)" : ""}</label>
              <input type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} placeholder={hasSecret ? "••••••••" : ""} style={{ width: "100%" }} />
            </div>
          </div>
          <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn btn-primary btn-sm" onClick={saveConfig} disabled={busy || !clientId}>Save credentials</button>
            <span className="hint">then run <code>podcli youtube auth</code> to authorize · or use Import CSV (no auth)</span>
          </div>
        </div>
      )}

      {proposals !== null && (
        <div className="section card">
          <div className="section-label">Link clips to uploads</div>
          {proposals.length === 0 ? (
            <div className="analytics-empty">No proposals. Every clip is linked, or no upload matched.</div>
          ) : (
            proposals.map((p) => (
              <div key={p.clip_id} className="analytics-row">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="analytics-row-title">{p.clip_title}</div>
                  <div className="analytics-row-sub">
                    → {p.video_title} <span style={{ color: p.score >= 0.8 ? "var(--green)" : "var(--text3)" }}>(score {p.score})</span>
                  </div>
                </div>
                <button className="btn btn-ghost btn-sm" disabled={linkBusy !== null} onClick={() => linkClip(p.clip_id, p.video_id)}>
                  {linkBusy === p.clip_id ? <div className="spinner sm" /> : "Link"}
                </button>
              </div>
            ))
          )}
        </div>
      )}

      {!hasData ? (
        <div className="analytics-hero">
          <TrendingUp size={32} strokeWidth={1.5} />
          <div className="analytics-hero-title">No performance data yet</div>
          <div className="analytics-hero-sub">
            Connect YouTube and sync, or import a YouTube Studio analytics CSV. Once clips have metrics,
            you'll see what holds viewers, what earns clicks, and which clips to make more of.
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 18, flexWrap: "wrap", justifyContent: "center" }}>
            <button className="btn btn-primary btn-sm" onClick={() => setShowConnect(true)}>Connect YouTube</button>
            <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()}>Import CSV</button>
          </div>
        </div>
      ) : (
        <>
          <div className="stat-row">
            <StatTile icon={<Eye size={16} />} label="Total views" value={fmtViews(totalViews(data!.byContentType))} sub="across published clips" />
            <StatTile icon={<Percent size={16} />} label="Avg retention" value={`${weighted(data!.byContentType, "avgRetention")}%`} sub="how much people watch" />
            <StatTile icon={<MousePointerClick size={16} />} label="Avg CTR" value={`${weighted(data!.byCaptionStyle, "avgCtr")}%`} sub="clicks per impression" />
            <StatTile icon={<TrendingUp size={16} />} label="Published" value={String(data!.published)} sub={`of ${data!.total} clips`} />
          </div>

          {insights.length > 0 && (
            <div className="section insights">
              <div className="section-label">What's working</div>
              <div className="insight-grid">
                {insights.map((i) => (
                  <div key={i.label} className="insight">
                    <div className="insight-label">{i.label}</div>
                    <div className="insight-value">{i.value}</div>
                    <div className="insight-metric">{i.metric}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="analytics-charts">
            <BarGroup title="Retention by content type" hint="What holds viewers" rows={data!.byContentType} metric="avgRetention" />
            <BarGroup title="CTR by caption style" hint="How packaging drives clicks" rows={data!.byCaptionStyle} metric="avgCtr" />
            <BarGroup title="Retention by length" hint="The sweet spot for watch time" rows={data!.byLength} metric="avgRetention" />
            <BarGroup title="Views by content type" hint="What reaches the most people" rows={data!.byContentType} metric="avgViews" />
          </div>

          <div className="section card">
            <div className="section-label">Top clips</div>
            {data!.top.map((c, i) => (
              <Link key={c.id} to={`/clip/${c.id}`} className="top-clip">
                <span className="rank-badge">{i + 1}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="top-clip-title">{c.title}</div>
                  <div className="hint" style={{ marginTop: 2 }}>{fmt(c.duration)} · {c.caption_style}{c.content_type ? ` · ${c.content_type}` : ""}</div>
                </div>
                <div className="top-clip-metrics">
                  <span className="metric-strong">{fmtViews(c.metrics?.views || 0)} views</span>
                  {c.metrics?.retention != null && <span>{c.metrics.retention}% ret</span>}
                  {c.metrics?.ctr != null && <span>{c.metrics.ctr}% ctr</span>}
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
