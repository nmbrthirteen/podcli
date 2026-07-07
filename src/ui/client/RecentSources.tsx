import React, { useEffect, useState } from "react";
import { api } from "./lib";

interface Source {
  path: string;
  name: string;
  exists: boolean;
}

export default function RecentSources({
  onPick,
  exclude = [],
}: {
  onPick: (path: string) => void;
  exclude?: string[];
}) {
  const [items, setItems] = useState<Source[]>([]);

  useEffect(() => {
    api<Source[]>("/sources").then(setItems).catch(() => {});
  }, []);

  const options = items.filter((s) => !exclude.includes(s.path));
  if (options.length === 0) return null;

  return (
    <select
      className="recent-sources"
      value=""
      onChange={(e) => {
        if (e.target.value) onPick(e.target.value);
        e.target.value = "";
      }}
    >
      <option value="">Recent sources…</option>
      {options.map((s) => (
        <option key={s.path} value={s.path}>{s.name}</option>
      ))}
    </select>
  );
}
