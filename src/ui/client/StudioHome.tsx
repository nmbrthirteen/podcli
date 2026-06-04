import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmt, timeAgo, basename } from "./lib";

interface Clip {
  id: string;
  source_video: string;
  title: string;
  caption_style: string;
  duration: number;
  file_size_mb?: number;
  output_path: string;
  created_at: string;
  content_type?: string;
}

interface Episode {
  source: string;
  clips: Clip[];
  latest: string;
}

function groupEpisodes(clips: Clip[]): Episode[] {
  const map = new Map<string, Clip[]>();
  for (const c of clips) {
    const key = basename(c.source_video) || "unknown";
    (map.get(key) ?? map.set(key, []).get(key)!).push(c);
  }
  return Array.from(map.entries())
    .map(([source, list]) => ({
      source,
      clips: list,
      latest: list.reduce((a, c) => (c.created_at > a ? c.created_at : a), list[0]?.created_at || ""),
    }))
    .sort((a, b) => (a.latest < b.latest ? 1 : -1));
}

export default function StudioHome() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api("/history?limit=500")
      .then((d) => setClips(Array.isArray(d) ? d : []))
      .catch(() => setClips([]))
      .finally(() => setLoading(false));
  }, []);

  const episodes = groupEpisodes(clips);
  const rendered = clips.length;

  return (
    <div className="app">
      <div className="header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h1 style={{ margin: 0 }}>Library</h1>
          <Link to="/episode" className="btn btn-primary btn-sm" style={{ textDecoration: "none" }}>+ New episode</Link>
        </div>
      </div>

      {!loading && rendered > 0 && (
        <div className="fade-in" style={{ margin: "0 0 18px", fontSize: 13, color: "var(--text2)" }}>
          {episodes.length} episode{episodes.length !== 1 ? "s" : ""} · {rendered} clip{rendered !== 1 ? "s" : ""} rendered
        </div>
      )}

      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text2)" }}>
          <div className="spinner sm" /> Loading library…
        </div>
      ) : episodes.length === 0 ? (
        <div className="drop-zone" style={{ textAlign: "center", padding: "48px 20px" }}>
          <div className="icon" style={{ fontSize: 28 }}>🎬</div>
          <div className="label" style={{ marginTop: 8 }}>
            No clips yet. <Link to="/episode" style={{ color: "var(--accent)" }}>Start a new episode</Link>.
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {episodes.map((ep) => (
            <div key={ep.source} className="section fade-in">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{ep.source}</div>
                  <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>
                    {ep.clips.length} clip{ep.clips.length !== 1 ? "s" : ""} · {timeAgo(ep.latest)}
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {ep.clips.map((c) => (
                  <Link
                    key={c.id}
                    to={`/clip/${c.id}`}
                    style={{
                      display: "flex", alignItems: "center", gap: 10, padding: "9px 11px",
                      background: "var(--surface)", borderRadius: "var(--radius-sm)",
                      textDecoration: "none", color: "inherit",
                    }}
                  >
                    <div style={{ width: 6, height: 6, borderRadius: 3, background: "var(--green)", flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {c.title}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>
                        {fmt(c.duration)} · {c.caption_style}
                        {c.content_type ? ` · ${c.content_type}` : ""}
                        {c.file_size_mb ? ` · ${c.file_size_mb.toFixed(1)}MB` : ""}
                      </div>
                    </div>
                    <span style={{ fontSize: 12, color: "var(--text3)" }}>›</span>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
