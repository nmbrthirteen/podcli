import React, { useState } from "react";
import { api } from "./lib";
import { TrashIcon } from "./icons";
import PersonChip from "./multicam-person-chip";
import type { McSessionSummary } from "./multicam-types";

export default function MulticamStart({
  sessions,
  people,
  onPeopleChange,
  busy,
  onStart,
  onOpen,
  onDelete,
}: {
  sessions: McSessionSummary[];
  people: string[];
  onPeopleChange: (people: string[]) => void;
  busy: boolean;
  onStart: (seed: { folder?: string; files?: string[] }) => Promise<boolean>;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [pathDraft, setPathDraft] = useState("");
  const [browsing, setBrowsing] = useState(false);

  async function browse(query: string, seed: (d: { folder?: string; file_paths?: string[] }) => { folder?: string; files?: string[] } | null) {
    setBrowsing(true);
    try {
      const d = await api<{ folder?: string; file_paths?: string[] }>(`/browse-file?${query}`);
      const picked = seed(d);
      if (picked) await onStart(picked);
    } catch {
      /* dialog cancelled */
    } finally {
      setBrowsing(false);
    }
  }

  async function commitPath() {
    const folder = pathDraft.trim();
    if (folder && (await onStart({ folder }))) setPathDraft("");
  }

  const disabled = browsing || busy;

  return (
    <>
      <div className="section card">
        <div className="card-title" style={{ marginBottom: 4 }}>Start a multicam edit</div>
        <p className="card-desc">Pick the folder with one episode's camera and mic files. podcli guesses who is in each file.</p>

        <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
          <button className="btn btn-primary" disabled={disabled} onClick={() => browse("folder=1", (d) => (d.folder ? { folder: d.folder } : null))}>
            {busy ? "Reading files…" : "Choose episode folder"}
          </button>
          <button className="btn btn-ghost" disabled={disabled} onClick={() => browse("multiple=1", (d) => (d.file_paths?.length ? { files: d.file_paths } : null))}>
            Pick files instead
          </button>
        </div>

        <input
          type="text"
          placeholder="Or paste a folder path, press Enter to start"
          value={pathDraft}
          disabled={disabled}
          onChange={(e) => setPathDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitPath(); } }}
          style={{ width: "100%", fontFamily: "var(--font-mono)", fontSize: 12, marginBottom: 16 }}
        />

        <label className="field-label">People</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {people.map((name, i) => (
            <PersonChip
              key={i}
              value={name}
              onChange={(v) => onPeopleChange(people.map((n, idx) => (idx === i ? v : n)))}
              onRemove={() => onPeopleChange(people.filter((_, idx) => idx !== i))}
            />
          ))}
          <button className="btn btn-ghost btn-sm" onClick={() => onPeopleChange([...people, `Person ${people.length + 1}`])}>
            + Add person
          </button>
        </div>
      </div>

      {sessions.length === 0 ? (
        <div className="empty-state">No multicam edits yet. Choose a folder of recordings above to start one.</div>
      ) : (
        <>
          <div className="card-title" style={{ marginBottom: 12 }}>Saved edits</div>
          <div className="stream-in">
            {sessions.map((s) => (
              <div
                key={s.session_id}
                className="card"
                style={{ display: "flex", alignItems: "center", gap: 12, padding: 16, cursor: "pointer" }}
                onClick={() => !busy && onOpen(s.session_id)}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="clip-card-title" style={{ marginBottom: 4 }}>{s.name}</div>
                  <div className="meta" style={{ gap: 8 }}>
                    <span className="hint">{s.sources} files · {s.cameras} cameras{s.shots > 0 ? ` · ${s.shots} shots` : ""}</span>
                    <span className={`pill ${s.video ? "pill-green" : s.synced ? "pill-sky" : "pill-amber"}`}>
                      {s.video ? "Rendered" : s.synced ? "Synced" : "Not synced"}
                    </span>
                  </div>
                </div>
                <button
                  className="btn btn-danger btn-sm"
                  aria-label={`Delete ${s.name}`}
                  disabled={busy}
                  onClick={(e) => { e.stopPropagation(); onDelete(s.session_id); }}
                >
                  <TrashIcon />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
