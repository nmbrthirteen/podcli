import React from "react";
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
} from "lucide-react";

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
};

function Icon({ name }: { name: string }) {
  const Glyph = icons[name];
  return <Glyph className="ico" strokeWidth={1.8} />;
}

export default function Layout() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="sidebar-logo">
          <img src="/podcli-logo-transparent.png" alt="podcli" />
        </Link>

        <div className="sidebar-section">Studio</div>
        <NavLink to="/" end className="sidebar-link"><Icon name="library" /> Library</NavLink>
        <NavLink to="/episode" className="sidebar-link"><Icon name="episode" /> New episode</NavLink>
        <NavLink to="/content" className="sidebar-link"><Icon name="content" /> Content</NavLink>
        <NavLink to="/thumbnails" className="sidebar-link"><Icon name="thumbnail" /> Thumbnails</NavLink>

        <div className="sidebar-section">Workspace</div>
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
    </div>
  );
}
