import React, { useState } from "react";
import { api } from "./lib";

interface Moment {
  start: number;
  end: number;
  why: string;
  text: string;
  enabled: boolean;
  dirty: boolean;
}

interface ReelResp {
  session_id: string;
  out_dir: string;
  reel_path: string | null;
  moments: Moment[];
}

const mmss = (s: number) =>
  `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function ReelPage() {
  const [videoPath, setVideoPath] = useState("");
  const [profile, setProfile] = useState<"party" | "action">("party");
  const [session, setSession] = useState<ReelResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function call(body: Record<string, unknown>, label: string) {
    setBusy(true);
    setMsg(label);
    try {
      const r = await api<ReelResp>("/reel", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setSession(r);
      setMsg(null);
    } catch (e: unknown) {
      setMsg("Error: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  }

  const detect = () =>
    call(
      { action: "new", video_path: videoPath, profile },
      "Detecting moments (one-time, ~2 min)…",
    );

  const edit = (index: number, op: string, seconds = 0) =>
    session &&
    call(
      { action: "edit", session_id: session.session_id, index, op, seconds },
      "Re-cutting that moment and rebuilding…",
    );

  return (
    <div className="app">
      <div className="header">
        <h1>Highlights reel</h1>
      </div>
      <p className="hint">
        Detect the good moments once, then tweak each one. An edit re-cuts only
        that moment and rebuilds the reel in seconds.
      </p>

      <div className="section">
        <input
          style={{ minWidth: 360 }}
          placeholder="/path/to/video.mp4"
          value={videoPath}
          onChange={(e) => setVideoPath(e.target.value)}
        />
        <select
          value={profile}
          onChange={(e) => setProfile(e.target.value as "party" | "action")}
        >
          <option value="party">party</option>
          <option value="action">action</option>
        </select>
        <button
          className="btn btn-primary btn-sm"
          disabled={busy || !videoPath}
          onClick={detect}
        >
          Detect moments
        </button>
      </div>

      {msg && <div className="hint">{msg}</div>}

      {session && (
        <div className="section stream-in">
          <div className="meta">
            <strong>{session.moments.length} moments</strong>
            {session.reel_path && (
              <span className="hint"> · reel: {session.reel_path}</span>
            )}
          </div>
          {session.moments.map((m, idx) => (
            <div
              key={idx}
              className="yt-card"
              style={{ opacity: m.enabled ? 1 : 0.5 }}
            >
              <div className="meta">
                <strong>
                  [{idx + 1}] {mmss(m.start)}–{mmss(m.end)}
                </strong>
                <span className="hint">
                  {" "}
                  {Math.round(m.end - m.start)}s · {m.why}
                </span>
              </div>
              {m.text && <p>{m.text}</p>}
              <div className="meta">
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => edit(idx + 1, "longer", 5)}>
                  +5s end
                </button>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => edit(idx + 1, "shorter", 5)}>
                  −5s end
                </button>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => edit(idx + 1, "earlier", 5)}>
                  start −5s
                </button>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => edit(idx + 1, "later", 5)}>
                  start +5s
                </button>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => edit(idx + 1, "toggle")}>
                  {m.enabled ? "disable" : "enable"}
                </button>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => edit(idx + 1, "drop")}>
                  drop
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
