import React from "react";
import { basename } from "./lib";
import { DownloadIcon } from "./icons";
import type { McSession } from "./multicam-types";
import { mcFileUrl, mcStreamUrl } from "./multicam-types";

const linkStyle = { textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 } as const;

export default function MulticamDeliver({
  session,
  locked,
  onRender,
  onExport,
  onMakeClips,
}: {
  session: McSession;
  locked: boolean;
  onRender: () => void;
  onExport: (format: "premiere" | "fcpxml") => void;
  onMakeClips: () => void;
}) {
  const { video, stems = [], premiere, fcpxml, duration } = session.outputs;
  const downloads = [
    ...(video ? [{ path: video, label: basename(video) }] : []),
    ...stems.map((path) => ({ path, label: basename(path) })),
    ...(premiere ? [{ path: premiere, label: "Premiere XML" }] : []),
    ...(fcpxml ? [{ path: fcpxml, label: "Final Cut XML" }] : []),
  ];

  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 4 }}>Deliver</div>
      <p className="card-desc">Render the finished MP4, or hand the cut to Premiere or Final Cut as a timeline of your original files.</p>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button className="btn btn-primary" disabled={locked} onClick={onRender}>Render episode</button>
        <button className="btn btn-ghost" disabled={locked} onClick={() => onExport("premiere")}>Export for Premiere</button>
        <button className="btn btn-ghost" disabled={locked} onClick={() => onExport("fcpxml")}>Export for Final Cut</button>
      </div>

      {video && (
        // The file name never changes between renders; the duration in the URL
        // makes the browser fetch the new render instead of its cached copy.
        <video
          key={`${video}-${duration}`}
          controls
          src={`${mcStreamUrl(video)}&v=${duration}`}
          style={{ width: "100%", borderRadius: "var(--radius-sm)", background: "#000", margin: "20px 0 12px" }}
        />
      )}
      {downloads.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginTop: video ? 0 : 16 }}>
          {downloads.map((d) => (
            <a key={d.path} className="btn btn-ghost btn-sm" href={mcFileUrl(d.path)} style={linkStyle}>
              <DownloadIcon size={13} /> {d.label}
            </a>
          ))}
          {video && (
            <button className="btn btn-primary btn-sm" disabled={locked} onClick={onMakeClips}>Make clips from this episode</button>
          )}
        </div>
      )}
    </div>
  );
}
