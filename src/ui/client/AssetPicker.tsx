import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, Check, Upload } from "lucide-react";
import { basename } from "./lib";
import { useAssets, assetSrc, ASSET_GROUPS, type Asset, type AssetType } from "./useAssets";

function Thumb({ asset }: { asset: Asset }) {
  const src = assetSrc(asset.name);
  if (asset.type === "logo" || asset.type === "image") {
    return <img className="picker-thumb" src={src} alt="" />;
  }
  if (asset.type === "outro" || asset.type === "intro" || asset.type === "video") {
    return <video className="picker-thumb" src={src} muted preload="metadata" />;
  }
  return <div className="picker-thumb picker-thumb-file" />;
}

export default function AssetPicker({
  type,
  value,
  onChange,
  label,
  allowNone = true,
  disabled = false,
}: {
  type: AssetType;
  value: string;
  onChange: (path: string) => void;
  label?: string;
  allowNone?: boolean;
  disabled?: boolean;
}) {
  const { assets, uploadFile } = useAssets({ live: false });
  const [open, setOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const options = assets.filter((a) => matchesGroup(a.type, type));
  const current = options.find((a) => a.path === value || a.name === value) || null;

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  async function onUpload(f: File) {
    setUploading(true);
    try {
      const asset = await uploadFile(f, type);
      onChange(asset.path);
      setOpen(false);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="asset-picker" ref={ref}>
      {label && <span className="asset-picker-label">{label}</span>}
      <button className="asset-picker-btn" disabled={disabled} onClick={() => setOpen((v) => !v)}>
        {current ? (
          <>
            <Thumb asset={current} />
            <span className="asset-picker-name">{current.name}</span>
          </>
        ) : (
          <span className="asset-picker-name muted">{value ? basename(value) : "None"}</span>
        )}
        <ChevronDown size={14} style={{ marginLeft: "auto", flexShrink: 0 }} />
      </button>

      {open && (
        <div className="asset-picker-menu">
          {allowNone && (
            <button className="asset-picker-opt" onClick={() => { onChange(""); setOpen(false); }}>
              <span className="asset-picker-name muted">None</span>
              {!value && <Check size={14} />}
            </button>
          )}
          {options.map((a) => (
            <button key={a.name} className="asset-picker-opt" onClick={() => { onChange(a.path); setOpen(false); }}>
              <Thumb asset={a} />
              <span className="asset-picker-name">{a.name}</span>
              {a.default && <span className="asset-default-tag">default</span>}
              {current?.name === a.name && <Check size={14} style={{ marginLeft: "auto" }} />}
            </button>
          ))}
          <input
            ref={fileRef}
            type="file"
            accept={ASSET_GROUPS.find((g) => g.type === type)?.accept ?? "*/*"}
            style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onUpload(f); }}
          />
          <div className="asset-picker-foot">
            <button className="asset-picker-opt" disabled={uploading} onClick={() => fileRef.current?.click()}>
              {uploading ? <div className="spinner sm" /> : <Upload size={14} />}
              <span className="asset-picker-name">Upload new</span>
            </button>
            <Link to="/assets" className="asset-picker-link" onClick={() => setOpen(false)}>Manage</Link>
          </div>
        </div>
      )}
    </div>
  );
}

function matchesGroup(assetType: AssetType, groupType: AssetType): boolean {
  if (groupType === "outro") return assetType === "outro" || assetType === "video";
  if (groupType === "music") return assetType === "music" || assetType === "audio";
  return assetType === groupType;
}
