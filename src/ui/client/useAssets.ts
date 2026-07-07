import { useCallback, useEffect, useState } from "react";
import { api, upload } from "./lib";

export type AssetType =
  | "logo"
  | "outro"
  | "intro"
  | "music"
  | "video"
  | "image"
  | "audio"
  | "other";

export interface Asset {
  name: string;
  type: AssetType;
  path: string;
  addedAt: string;
  default?: boolean;
}

export const ASSET_GROUPS: { type: AssetType; label: string; accept: string }[] = [
  { type: "logo", label: "Logos", accept: "image/*" },
  { type: "outro", label: "Outros", accept: "video/*" },
  { type: "intro", label: "Intros", accept: "video/*" },
  { type: "music", label: "Music", accept: "audio/*" },
  { type: "image", label: "Images", accept: "image/*" },
  { type: "other", label: "Other", accept: "*/*" },
];

export function assetSrc(name: string): string {
  return `/api/assets/${encodeURIComponent(name)}/download`;
}

export function useAssets({ live = true }: { live?: boolean } = {}) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const items = await api<Asset[]>("/assets");
      setAssets(items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load assets");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    if (!live) return;
    const es = new EventSource("/api/events");
    const onChange = () => refresh();
    es.addEventListener("assets-updated", onChange);
    return () => es.close();
  }, [refresh, live]);

  const uploadFile = useCallback(
    async (file: File, type: AssetType, name?: string) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("type", type);
      if (name) fd.append("name", name);
      const asset = await upload<Asset>("/assets/upload", fd);
      await refresh();
      return asset;
    },
    [refresh],
  );

  const importUrl = useCallback(
    async (url: string, type: AssetType, name?: string) => {
      const res = await api<{ job_id: string }>("/assets/url", {
        method: "POST",
        body: JSON.stringify({ url, type, name }),
      });
      return res.job_id;
    },
    [],
  );

  const setDefault = useCallback(
    async (name: string) => {
      await api(`/assets/${encodeURIComponent(name)}/default`, { method: "POST" });
      await refresh();
    },
    [refresh],
  );

  const clearDefault = useCallback(
    async (name: string) => {
      await api(`/assets/${encodeURIComponent(name)}/default`, { method: "DELETE" });
      await refresh();
    },
    [refresh],
  );

  const rename = useCallback(
    async (name: string, newName: string) => {
      await api(`/assets/${encodeURIComponent(name)}/rename`, {
        method: "POST",
        body: JSON.stringify({ new_name: newName }),
      });
      await refresh();
    },
    [refresh],
  );

  const remove = useCallback(
    async (name: string) => {
      await api(`/assets/${encodeURIComponent(name)}`, { method: "DELETE" });
      await refresh();
    },
    [refresh],
  );

  return { assets, loading, error, refresh, uploadFile, importUrl, setDefault, clearDefault, rename, remove };
}
