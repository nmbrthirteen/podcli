import React, { useEffect, useRef, useState } from "react";
import { PageHeader } from "./Page";
import { createPortal } from "react-dom";
import { Star, Download, Trash2, Link2, Plus, MoreVertical, Play, Pause } from "lucide-react";
import { api, basename, fmt } from "./lib";
import { useJob } from "./useJob";
import Tooltip from "./Tooltip";
import ConfirmDialog from "./ConfirmDialog";
import {
  useAssets,
  assetSrc,
  ASSET_GROUPS,
  type Asset,
  type AssetType,
} from "./useAssets";

const DEFAULTABLE: AssetType[] = ["logo", "outro", "intro", "music"];

function AudioPlayer({ src }: { src: string }) {
  const ref = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);

  const toggle = () => {
    const a = ref.current;
    if (!a) return;
    a.paused ? a.play() : a.pause();
  };
  const seek = (clientX: number, el: HTMLDivElement) => {
    const a = ref.current;
    if (!a || !dur) return;
    const r = el.getBoundingClientRect();
    a.currentTime = Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * dur;
  };
  const pct = dur ? (t / dur) * 100 : 0;

  return (
    <div className="asset-audio-player">
      <audio
        ref={ref}
        src={src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setT(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
      />
      <button className="asset-audio-btn" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
        {playing ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
      </button>
      <div
        className="asset-audio-track"
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); seek(e.clientX, e.currentTarget); }}
        onPointerMove={(e) => { if (e.buttons) seek(e.clientX, e.currentTarget); }}
      >
        <div className="asset-audio-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="asset-audio-time">{fmt(t || 0)}</span>
    </div>
  );
}

function Preview({ asset, onOpen }: { asset: Asset; onOpen: () => void }) {
  const src = assetSrc(asset.name);
  if (asset.type === "logo" || asset.type === "image") {
    return <img className="asset-thumb clickable" src={src} alt={asset.name} loading="lazy" onClick={onOpen} />;
  }
  if (asset.type === "outro" || asset.type === "intro" || asset.type === "video") {
    return <video className="asset-thumb clickable" src={src} muted preload="metadata" onClick={onOpen} />;
  }
  if (asset.type === "music" || asset.type === "audio") {
    return <div className="asset-thumb asset-thumb-audio"><AudioPlayer src={src} /></div>;
  }
  return <div className="asset-thumb asset-thumb-file clickable" onClick={onOpen}>{extLabel(asset.path)}</div>;
}

function AssetLightbox({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const src = assetSrc(asset.name);
  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-body" onClick={(e) => e.stopPropagation()}>
        {(asset.type === "logo" || asset.type === "image") && (
          <img src={src} alt={asset.name} style={{ width: "100%", borderRadius: 16, background: "var(--bg2)" }} />
        )}
        {(asset.type === "outro" || asset.type === "intro" || asset.type === "video") && (
          <video src={src} controls autoPlay style={{ width: "100%", borderRadius: 16 }} />
        )}
        {(asset.type === "music" || asset.type === "audio") && (
          <div className="lightbox-audio"><AudioPlayer src={src} /></div>
        )}
        {asset.type === "other" && (
          <div className="lightbox-file">{basename(asset.path)}</div>
        )}
        <div className="lightbox-name">{asset.name}</div>
      </div>
    </div>,
    document.body,
  );
}

function extLabel(path: string): string {
  const ext = (path.split(".").pop() || "").toUpperCase();
  return ext.length <= 4 ? ext : "FILE";
}

const MENU_WIDTH = 150;

function OverflowMenu({ items }: { items: { label: string; onClick: () => void; danger?: boolean }[] }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    // Skip the trigger so its onClick can toggle closed, not reopen.
    const onDocMouseDown = (e: MouseEvent) => {
      if (btnRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const dismiss = () => setOpen(false);
    document.addEventListener("mousedown", onDocMouseDown);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [open]);

  function openMenu() {
    const r = btnRef.current?.getBoundingClientRect();
    if (r) {
      const menuHeight = items.length * 34 + 8;
      const left = Math.max(8, Math.min(r.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 8));
      let top = r.bottom + 4;
      if (top + menuHeight > window.innerHeight - 8) top = Math.max(8, r.top - menuHeight - 4);
      setPos({ top, left });
    }
    setOpen((v) => !v);
  }

  return (
    <>
      <button ref={btnRef} className="icon-btn" onClick={openMenu}>
        <MoreVertical size={16} />
      </button>
      {open && pos && createPortal(
        <div
          className="overflow-menu"
          style={{ position: "fixed", top: pos.top, left: pos.left, right: "auto", width: MENU_WIDTH }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {items.map((it) => (
            <button
              key={it.label}
              className="overflow-menu-item"
              style={it.danger ? { color: "var(--red)" } : undefined}
              onClick={() => { setOpen(false); it.onClick(); }}
            >
              {it.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}

function AssetCard({
  asset,
  selected,
  canDefault,
  onToggleSelect,
  onToggleDefault,
  onRename,
  onDelete,
  onOpen,
}: {
  asset: Asset;
  selected: boolean;
  canDefault: boolean;
  onToggleSelect: () => void;
  onToggleDefault: () => void;
  onRename: (newName: string) => void;
  onDelete: () => void;
  onOpen: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(asset.name);

  function commitRename() {
    // Guard against Enter + unmount-blur both firing on the same rename.
    if (!renaming) return;
    setRenaming(false);
    const next = draft.trim();
    if (next && next !== asset.name) onRename(next);
    else setDraft(asset.name);
  }

  return (
    <div className={`asset-card${selected ? " selected" : ""}`}>
      <label className="asset-check">
        <input type="checkbox" checked={selected} onChange={onToggleSelect} />
      </label>
      <Preview asset={asset} onOpen={onOpen} />
      <div className="asset-row">
        <div className="asset-meta">
          {renaming ? (
            <input
              className="asset-rename"
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitRename();
                if (e.key === "Escape") { setDraft(asset.name); setRenaming(false); }
              }}
            />
          ) : (
            <div className="asset-name" title={asset.path}>
              {asset.name}
              {asset.default && <span className="asset-default-tag">default</span>}
            </div>
          )}
          <div className="asset-sub">{basename(asset.path)}</div>
        </div>
        <div className="asset-actions">
          {canDefault && (
            <Tooltip label={asset.default ? "Remove default" : "Set as default"}>
              <button
                className={`icon-btn${asset.default ? " active" : ""}`}
                onClick={onToggleDefault}
              >
                <Star size={15} fill={asset.default ? "currentColor" : "none"} />
              </button>
            </Tooltip>
          )}
          <OverflowMenu
            items={[
              { label: "Rename", onClick: () => { setDraft(asset.name); setRenaming(true); } },
              { label: "Download", onClick: () => triggerDownload(asset.name) },
              { label: "Delete", onClick: onDelete, danger: true },
            ]}
          />
        </div>
      </div>
    </div>
  );
}

function triggerDownload(name: string) {
  const a = document.createElement("a");
  a.href = `${assetSrc(name)}?dl=1`;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export default function AssetsPage() {
  const { assets, loading, error, uploadFile, importUrl, setDefault, clearDefault, rename, remove } = useAssets();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [preview, setPreview] = useState<Asset | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ title: string; message: string; run: () => void } | null>(null);

  const wrap = (p: Promise<unknown>) => p.catch((e) => setMsg(e instanceof Error ? e.message : String(e)));

  function toggleSelect(name: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  }

  async function performDelete(name: string) {
    await wrap(remove(name));
    setSelected((p) => {
      const n = new Set(p);
      n.delete(name);
      return n;
    });
  }

  function requestDelete(name: string) {
    setPendingDelete({
      title: `Delete "${name}"?`,
      message: "This removes it from your asset library and cannot be undone.",
      run: () => performDelete(name),
    });
  }

  function requestBulkDelete() {
    const names = [...selected];
    setPendingDelete({
      title: `Delete ${names.length} asset${names.length > 1 ? "s" : ""}?`,
      message: "This removes them from your asset library and cannot be undone.",
      run: async () => { for (const name of names) await performDelete(name); },
    });
  }

  function bulkDownload() {
    // Stagger so browsers don't block rapid multiple downloads.
    [...selected].forEach((name, i) => setTimeout(() => triggerDownload(name), i * 300));
  }

  return (
    <div className="app">
      <PageHeader title="Assets" />

      {error && <div className="error-bar">{error}</div>}
      {msg && <div className="error-bar" onClick={() => setMsg(null)}>{msg}</div>}

      {selected.size > 0 && (
        <div className="asset-bulkbar">
          <span>{selected.size} selected</span>
          <button className="btn btn-ghost btn-sm" onClick={bulkDownload}>
            <Download size={14} /> Download
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())}>Clear</button>
          <button className="btn btn-danger btn-sm" onClick={requestBulkDelete}>
            <Trash2 size={14} /> Delete
          </button>
        </div>
      )}

      <BrandDefaults onError={setMsg} />

      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text2)" }}>
          <div className="spinner sm" /> Loading…
        </div>
      ) : (
        ASSET_GROUPS.map((group) => (
          <AssetSection
            key={group.type}
            group={group}
            assets={assets.filter((a) => matchesGroup(a.type, group.type))}
            selected={selected}
            canDefault={DEFAULTABLE.includes(group.type)}
            onToggleSelect={toggleSelect}
            onDelete={requestDelete}
            onOpen={setPreview}
            onRename={(name, next) => wrap(rename(name, next))}
            onToggleDefault={(a) => wrap(a.default ? clearDefault(a.name) : setDefault(a.name))}
            onUpload={async (file) => {
              setBusy(group.type);
              try {
                await uploadFile(file, group.type);
              } catch (e) {
                setMsg(e instanceof Error ? e.message : "Upload failed");
              } finally {
                setBusy(null);
              }
            }}
            onImportUrl={(url) => importUrl(url, group.type)}
            busy={busy === group.type}
          />
        ))
      )}

      <div className="asset-bottom-space" />

      {preview && <AssetLightbox asset={preview} onClose={() => setPreview(null)} />}
      <ConfirmDialog
        open={!!pendingDelete}
        title={pendingDelete?.title || ""}
        message={pendingDelete?.message}
        onConfirm={() => { pendingDelete?.run(); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

const CAPTION_STYLES: { id: string; label: string; className: string; sample: string }[] = [
  { id: "hormozi", label: "Hormozi", className: "cap-hormozi", sample: "THIS CHANGES EVERYTHING" },
  { id: "karaoke", label: "Karaoke", className: "cap-karaoke", sample: "word by word reveal" },
  { id: "subtle", label: "Subtle", className: "cap-subtle", sample: "clean and understated" },
  { id: "branded", label: "Branded", className: "cap-branded", sample: "PILL HIGHLIGHT" },
];

function BrandDefaults({ onError }: { onError: (m: string) => void }) {
  const [style, setStyle] = useState<string | null>(null);

  useEffect(() => {
    api<{ settings?: { captionStyle?: string } }>("/ui-state")
      .then((s) => setStyle(s.settings?.captionStyle || "branded"))
      .catch(() => setStyle("branded"));
  }, []);

  function pick(id: string) {
    setStyle(id);
    api("/ui-state", {
      method: "POST",
      body: JSON.stringify({ _source: "ui", settings: { captionStyle: id } }),
    }).catch((e) => onError(e instanceof Error ? e.message : "Failed to save default"));
  }

  return (
    <div className="section">
      <div className="section-label">Default caption style</div>
      <div className="cap-grid">
        {CAPTION_STYLES.map((s) => (
          <button
            key={s.id}
            className={`cap-card${style === s.id ? " selected" : ""}`}
            onClick={() => pick(s.id)}
          >
            <div className="cap-preview">
              <span className={s.className}>{s.sample}</span>
            </div>
            <div className="cap-label">
              {s.label}
              {style === s.id && <span className="asset-default-tag">default</span>}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function matchesGroup(assetType: AssetType, groupType: AssetType): boolean {
  if (groupType === "outro") return assetType === "outro" || assetType === "video";
  if (groupType === "music") return assetType === "music" || assetType === "audio";
  return assetType === groupType;
}

function AssetSection({
  group,
  assets,
  selected,
  canDefault,
  onToggleSelect,
  onDelete,
  onOpen,
  onRename,
  onToggleDefault,
  onUpload,
  onImportUrl,
  busy,
}: {
  group: { type: AssetType; label: string; accept: string };
  assets: Asset[];
  selected: Set<string>;
  canDefault: boolean;
  onToggleSelect: (name: string) => void;
  onDelete: (name: string) => void;
  onOpen: (asset: Asset) => void;
  onRename: (name: string, newName: string) => void;
  onToggleDefault: (asset: Asset) => void;
  onUpload: (file: File) => void;
  onImportUrl: (url: string) => Promise<string>;
  busy: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [urlOpen, setUrlOpen] = useState(false);
  const [urlDraft, setUrlDraft] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobErr, setJobErr] = useState<string | null>(null);
  const job = useJob(jobId);

  useEffect(() => {
    if (job?.status === "done") setJobId(null);
    if (job?.status === "error") {
      setJobErr(job.error || "Download failed");
      setJobId(null);
    }
  }, [job?.status]);

  async function submitUrl() {
    const u = urlDraft.trim();
    if (!u) return;
    setUrlDraft("");
    setUrlOpen(false);
    setJobErr(null);
    try {
      setJobId(await onImportUrl(u));
    } catch (e) {
      setJobErr(e instanceof Error ? e.message : "Download failed");
    }
  }

  return (
    <div className="section">
      <div className="asset-section-head">
        <div className="section-label" style={{ margin: 0 }}>
          {group.label} <span className="asset-count">{assets.length}</span>
        </div>
        <button className="btn btn-ghost btn-sm asset-url-toggle" onClick={() => setUrlOpen((v) => !v)}>
          <Link2 size={14} /> Add from URL
        </button>
      </div>

      {urlOpen && (
        <div className="asset-url-row">
          <input
            type="text"
            placeholder="https://… (direct file or video URL)"
            value={urlDraft}
            autoFocus
            onChange={(e) => setUrlDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitUrl()}
          />
          <button className="btn btn-primary btn-sm" onClick={submitUrl}>Add</button>
        </div>
      )}

      {jobErr && <div className="hint" style={{ color: "var(--red)" }}>{jobErr}</div>}
      {jobId && (
        <div className="file-badge">
          <div className="spinner sm" />
          <div className="name">Downloading{job?.progress ? ` · ${Math.round(job.progress)}%` : "…"}</div>
        </div>
      )}

      <div className="asset-grid">
        {assets.map((a) => (
          <AssetCard
            key={a.name}
            asset={a}
            selected={selected.has(a.name)}
            canDefault={canDefault}
            onToggleSelect={() => onToggleSelect(a.name)}
            onToggleDefault={() => onToggleDefault(a)}
            onRename={(next) => onRename(a.name, next)}
            onDelete={() => onDelete(a.name)}
            onOpen={() => onOpen(a)}
          />
        ))}
        <input
          ref={fileRef}
          type="file"
          accept={group.accept}
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUpload(f);
            e.target.value = "";
          }}
        />
        <button className="asset-add-tile" disabled={busy} onClick={() => fileRef.current?.click()}>
          {busy ? <div className="spinner sm" /> : <Plus size={20} />}
          <span>Upload {group.label.toLowerCase()}</span>
        </button>
      </div>
    </div>
  );
}
