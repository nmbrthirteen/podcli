import React, { useState } from "react";
import { fmt, basename } from "./lib";
import { AudioIcon } from "./icons";
import PersonChip from "./multicam-person-chip";
import type { McEdit, McPerson, McRole, McSession, McSource } from "./multicam-types";
import { mcImageUrl } from "./multicam-types";

const selectStyle = { width: "auto" } as const;

export default function MulticamSources({
  session,
  previewCameras,
  locked,
  onEdit,
  onSync,
}: {
  session: McSession;
  previewCameras: Record<string, string>;
  locked: boolean;
  onEdit: McEdit;
  onSync: () => void;
}) {
  const [nameDraft, setNameDraft] = useState<Record<string, string>>({});
  const synced = session.sources.some((s) => s.sync?.status);

  const savePeople = (people: McPerson[]) => onEdit({ action: "map", people });

  function commitName(person: McPerson) {
    const draft = nameDraft[person.id]?.trim();
    if (!draft || draft === person.name) return;
    savePeople(session.people.map((p) => (p.id === person.id ? { ...p, name: draft } : p)));
  }

  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 4 }}>Who is in each file</div>
      <p className="card-desc">Check the guesses below, then sync once everyone looks right.</p>

      <label className="field-label">People</label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 18 }}>
        {session.people.map((person) => (
          <PersonChip
            key={person.id}
            value={nameDraft[person.id] ?? person.name}
            onChange={(v) => setNameDraft((d) => ({ ...d, [person.id]: v }))}
            onBlur={() => commitName(person)}
            onRemove={() => savePeople(session.people.filter((p) => p.id !== person.id))}
            disabled={locked}
            role={person.role || "host"}
            onRole={(role) => savePeople(session.people.map((p) => (p.id === person.id ? { ...p, role } : p)))}
          />
        ))}
        <button
          className="btn btn-ghost btn-sm"
          disabled={locked}
          onClick={() => savePeople([...session.people, { id: "", name: `Person ${session.people.length + 1}` }])}
        >
          + Add person
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {session.sources.map((source) => (
          <SourceRow
            key={source.id}
            source={source}
            people={session.people}
            previewPath={previewCameras[source.id]}
            onEdit={(edit) => onEdit({ action: "map", sources: [{ id: source.id, ...edit }] })}
            disabled={locked}
          />
        ))}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
        <button className={`btn ${synced ? "btn-ghost" : "btn-primary"}`} disabled={locked} onClick={onSync}>
          {synced ? "Sync again" : "Sync sources"}
        </button>
      </div>
    </div>
  );
}

function SourceRow({
  source,
  people,
  previewPath,
  onEdit,
  disabled,
}: {
  source: McSource;
  people: McPerson[];
  previewPath?: string;
  onEdit: (edit: { role?: McRole; person?: string; channel_people?: string[] }) => void;
  disabled: boolean;
}) {
  const split = source.channel_people.length > 0;
  const details = [
    fmt(source.duration),
    source.kind === "video" ? `${source.width}x${source.height}` : "Audio",
    source.has_audio ? `${source.audio_channels}ch audio` : "No audio",
  ];
  const personOptions = people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>);

  return (
    <div className="card" style={{ display: "flex", alignItems: "center", gap: 14, padding: 12, margin: 0 }}>
      <div style={{
        width: 64, height: 40, borderRadius: "var(--radius-sm)", background: "var(--surface2)",
        display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden", flexShrink: 0,
      }}>
        {source.kind === "video" && previewPath ? (
          <img src={mcImageUrl(previewPath)} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        ) : (
          <span style={{ color: "var(--text3)" }}><AudioIcon /></span>
        )}
      </div>

      <div style={{ minWidth: 0, width: 200 }}>
        <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={source.path}>
          {source.name || basename(source.path)}
        </div>
        <div className="hint">
          {source.parent ? "Cut from the call recording" : source.members.length ? "Side by side from everyone's own files" : details.join(" · ")}
        </div>
      </div>

      {source.guessed && source.role !== "ignore" && <span className="pill pill-amber">Guessed</span>}

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto", flexWrap: "wrap", justifyContent: "flex-end" }}>
        <select value={source.role} disabled={disabled} style={selectStyle} onChange={(e) => onEdit({ role: e.target.value as McRole })}>
          <option value="camera" disabled={source.kind === "audio"}>Camera</option>
          <option value="mic" disabled={!source.has_audio}>Mic</option>
          <option value="ignore">Ignore</option>
        </select>

        {source.role === "camera" && !source.members.length && (
          <select value={source.person || "wide"} disabled={disabled} style={selectStyle} onChange={(e) => onEdit({ person: e.target.value })}>
            {personOptions}
            <option value="wide">Everyone (wide)</option>
          </select>
        )}

        {source.role === "mic" && !split && (
          <select value={source.person} disabled={disabled} style={selectStyle} onChange={(e) => onEdit({ person: e.target.value })}>
            <option value="">Shared room mic</option>
            {personOptions}
          </select>
        )}

        {source.role === "mic" && split && [0, 1].map((ch) => (
          <select
            key={ch}
            value={source.channel_people[ch] || ""}
            disabled={disabled}
            style={selectStyle}
            onChange={(e) => {
              const next = [source.channel_people[0] || "", source.channel_people[1] || ""];
              next[ch] = e.target.value;
              onEdit({ channel_people: next });
            }}
          >
            <option value="">{ch === 0 ? "Left: nobody" : "Right: nobody"}</option>
            {people.map((p) => <option key={p.id} value={p.id}>{`${ch === 0 ? "Left" : "Right"}: ${p.name}`}</option>)}
          </select>
        ))}

        {source.role === "mic" && source.audio_channels === 2 && (
          <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={split}
              disabled={disabled}
              onChange={(e) => onEdit({ channel_people: e.target.checked ? people.slice(0, 2).map((p) => p.id) : [] })}
            />
            One person per side
          </label>
        )}
      </div>
    </div>
  );
}
