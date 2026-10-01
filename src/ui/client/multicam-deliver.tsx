import React from "react";
import { basename } from "./lib";
import { DownloadIcon } from "./icons";
import type { McSession } from "./multicam-types";
import { mcFileUrl, mcStreamUrl } from "./multicam-types";

const linkStyle = { textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 } as const;

export function DeliverActions({
  locked,
  exportable,
  onRender,
  onExport,
}: {
  locked: boolean;
  exportable: boolean;
  onRender: () => void;
  onExport: (format: "premiere" | "fcpxml") => void;
}) {
  const why = exportable ? undefined : "Editor timelines can't carry call layouts yet. Render the MP4 instead.";
  return (
    <>
      <button className="btn btn-ghost btn-sm" disabled={locked || !exportable} title={why} onClick={() => onExport("premiere")}>Export for Premiere</button>
      <button className="btn btn-ghost btn-sm" disabled={locked || !exportable} title={why} onClick={() => onExport("fcpxml")}>Export for Final Cut</button>
      <button className="btn btn-primary btn-sm" disabled={locked} onClick={onRender}>Render episode</button>
    </>
  );
}

export default function MulticamResults({
  session,
  locked,
  onMakeClips,
}: {
  session: McSession;
  locked: boolean;
  onMakeClips: () => void;
}) {
  const { video, stems = [], premiere, fcpxml, duration } = session.outputs;
  const downloads = [
    ...(video ? [{ path: video, label: basename(video) }] : []),
    ...stems.map((path) => ({ path, label: basename(path) })),
    ...(premiere ? [{ path: premiere, label: "Premiere XML" }] : []),
    ...(fcpxml ? [{ path: fcpxml, label: "Final Cut XML" }] : []),
  ];
  if (!downloads.length) return null;

  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 12 }}>Finished files</div>
      {video && (
        // The file name never changes between renders; the duration in the URL
        // makes the browser fetch the new render instead of its cached copy.
        <video
          key={`${video}-${duration}`}
          controls
          src={`${mcStreamUrl(video)}&v=${duration}`}
          style={{ width: "100%", borderRadius: "var(--radius-sm)", background: "#000", marginBottom: 12 }}
        />
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        {downloads.map((d) => (
          <a key={d.path} className="btn btn-ghost btn-sm" href={mcFileUrl(d.path)} style={linkStyle}>
            <DownloadIcon size={13} /> {d.label}
          </a>
        ))}
        {video && (
          <button className="btn btn-primary btn-sm" disabled={locked} onClick={onMakeClips}>Make clips from this episode</button>
        )}
      </div>
    </div>
  );
}
