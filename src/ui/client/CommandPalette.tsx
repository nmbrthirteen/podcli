import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { Search, FileText, Film, Package, CornerDownLeft } from "lucide-react";
import { api } from "./lib";

interface Cmd {
  id: string;
  label: string;
  sub?: string;
  group: "Pages" | "Clips" | "Assets";
  keywords?: string;
  run: () => void;
}

const PAGES: { label: string; path: string; kw: string }[] = [
  { label: "Library", path: "/", kw: "clips home dashboard" },
  { label: "New episode", path: "/episode", kw: "upload transcribe process video" },
  { label: "Content", path: "/content", kw: "titles descriptions hashtags" },
  { label: "Highlights", path: "/highlights", kw: "reel moments" },
  { label: "Thumbnails", path: "/thumbnails", kw: "cover image" },
  { label: "Assets", path: "/assets", kw: "logo outro intro music brand kit" },
  { label: "Knowledge", path: "/knowledge", kw: "brand voice banned words" },
  { label: "Config", path: "/config", kw: "settings api keys tokens" },
  { label: "Integrations", path: "/integrations", kw: "youtube davinci" },
  { label: "MCP setup", path: "/mcp", kw: "claude codex cursor" },
  { label: "Analytics", path: "/analytics", kw: "performance views retention ctr" },
];

const GROUP_ICON = {
  Pages: <FileText size={15} />,
  Clips: <Film size={15} />,
  Assets: <Package size={15} />,
};

export default function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [clips, setClips] = useState<{ id: string; title: string }[]>([]);
  const [assets, setAssets] = useState<{ name: string; type: string }[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const loaded = useRef(false);

  const close = useCallback(() => { setOpen(false); setQuery(""); setActive(0); }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onTrigger = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("open-command-palette", onTrigger);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("open-command-palette", onTrigger);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    if (loaded.current) return;
    Promise.allSettled([
      api<{ id: string; title: string }[]>("/history?limit=100").then((h) => setClips(h || [])),
      api<{ name: string; type: string }[]>("/assets").then((a) => setAssets(a || [])),
    ]).then((r) => {
      // Only cache success, so a failed fetch retries on the next open.
      if (r.every((x) => x.status === "fulfilled")) loaded.current = true;
    });
  }, [open]);

  const go = useCallback((run: () => void) => { close(); run(); }, [close]);

  const results = useMemo<Cmd[]>(() => {
    const q = query.trim().toLowerCase();
    const pageCmds: Cmd[] = PAGES.map((p) => ({
      id: `p:${p.path}`, label: p.label, group: "Pages", keywords: p.kw,
      run: () => navigate(p.path),
    }));
    const clipCmds: Cmd[] = clips.map((c) => ({
      id: `c:${c.id}`, label: c.title || "Untitled clip", sub: "Clip", group: "Clips",
      run: () => navigate(`/clip/${c.id}`),
    }));
    const assetCmds: Cmd[] = assets.map((a) => ({
      id: `a:${a.name}`, label: a.name, sub: a.type, group: "Assets",
      run: () => navigate("/assets"),
    }));
    const all = [...pageCmds, ...clipCmds, ...assetCmds];
    if (!q) return pageCmds;
    const match = (c: Cmd) =>
      c.label.toLowerCase().includes(q) ||
      (c.sub?.toLowerCase().includes(q) ?? false) ||
      (c.keywords?.includes(q) ?? false);
    return all.filter(match).slice(0, 24);
  }, [query, clips, assets, navigate]);

  const groups = useMemo(() => {
    const order: Cmd["group"][] = ["Pages", "Clips", "Assets"];
    return order
      .map((g) => ({ group: g, items: results.filter((r) => r.group === g) }))
      .filter((g) => g.items.length > 0);
  }, [results]);

  const flat = results;
  const activeIdx = Math.min(active, Math.max(0, flat.length - 1));

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="cmdk-overlay" onClick={close}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-head">
          <Search size={17} className="cmdk-search-icon" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") close();
              else if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, flat.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
              else if (e.key === "Enter" && flat[activeIdx]) { e.preventDefault(); go(flat[activeIdx].run); }
            }}
            placeholder="Search pages, clips, and assets"
          />
          <kbd className="cmdk-kbd">esc</kbd>
        </div>

        <div className="cmdk-list">
          {flat.length === 0 && <div className="cmdk-empty">No matches for “{query}”.</div>}
          {groups.map((g) => (
            <div key={g.group} className="cmdk-group">
              <div className="cmdk-group-label">{g.group}</div>
              {g.items.map((c) => {
                const idx = flat.indexOf(c);
                return (
                  <button
                    key={c.id}
                    className={`cmdk-item${idx === activeIdx ? " active" : ""}`}
                    onMouseEnter={() => setActive(idx)}
                    onClick={() => go(c.run)}
                  >
                    <span className="cmdk-item-icon">{GROUP_ICON[c.group]}</span>
                    <span className="cmdk-item-label">{c.label}</span>
                    {c.sub && <span className="cmdk-item-sub">{c.sub}</span>}
                    {idx === activeIdx && <CornerDownLeft size={13} className="cmdk-item-enter" />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
