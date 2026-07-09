import React, { useEffect } from "react";
import { NavLink, Link, Outlet } from "react-router-dom";
import {
  LayoutGrid,
  Play,
  FileText,
  Image,
  BookOpen,
  Settings,
  Plug,
  Terminal,
  BarChart3,
  Scissors,
  Package,
  Search,
} from "lucide-react";
import CommandPalette from "./CommandPalette";

const icons: Record<string, typeof LayoutGrid> = {
  library: LayoutGrid,
  episode: Play,
  content: FileText,
  thumbnail: Image,
  knowledge: BookOpen,
  config: Settings,
  integrations: Plug,
  mcp: Terminal,
  analytics: BarChart3,
  highlights: Scissors,
  assets: Package,
};

function Icon({ name }: { name: string }) {
  const Glyph = icons[name];
  return <Glyph className="ico" strokeWidth={1.8} />;
}

export default function Layout() {
  useEffect(() => {
    fetch("/api/session-cache/clear", { method: "POST" }).then((res) => {
      if (!res.ok) console.warn("Failed to clear session cache", res.status);
    }).catch((err: unknown) => {
      console.warn("Failed to clear session cache", err);
    });
  }, []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="sidebar-logo">
          <img src="/podcli-logo.svg" alt="podcli" />
        </Link>

        <button className="sidebar-search" onClick={() => window.dispatchEvent(new Event("open-command-palette"))}>
          <Search className="ico" strokeWidth={1.8} size={15} />
          <span>Search</span>
          <kbd>⌘K</kbd>
        </button>

        <div className="sidebar-section">Studio</div>
        <NavLink to="/" end className="sidebar-link"><Icon name="library" /> Library</NavLink>
        <NavLink to="/episode" className="sidebar-link"><Icon name="episode" /> New episode</NavLink>
        <NavLink to="/content" className="sidebar-link"><Icon name="content" /> Content</NavLink>
        <NavLink to="/highlights" className="sidebar-link"><Icon name="highlights" /> Highlights</NavLink>
        <NavLink to="/thumbnails" className="sidebar-link"><Icon name="thumbnail" /> Thumbnails</NavLink>

        <div className="sidebar-section">Workspace</div>
        <NavLink to="/assets" className="sidebar-link"><Icon name="assets" /> Assets</NavLink>
        <NavLink to="/knowledge" className="sidebar-link"><Icon name="knowledge" /> Knowledge</NavLink>
        <NavLink to="/config" className="sidebar-link"><Icon name="config" /> Config</NavLink>
        <NavLink to="/integrations" className="sidebar-link"><Icon name="integrations" /> Integrations</NavLink>
        <NavLink to="/mcp" className="sidebar-link"><Icon name="mcp" /> MCP setup</NavLink>

        <div className="sidebar-section">Insights</div>
        <NavLink to="/analytics" className="sidebar-link"><Icon name="analytics" /> Analytics</NavLink>
      </aside>

      <main className="shell-main">
        <Outlet />
      </main>
      <CommandPalette />
    </div>
  );
}
