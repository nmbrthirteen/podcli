import React from "react";
import { NavLink, Link, Outlet } from "react-router-dom";

const icons: Record<string, React.ReactNode> = {
  library: <path d="M3 5h7v14H3zM14 5h7v9h-7z" />,
  episode: <path d="M5 3l14 9-14 9z" />,
  thumbnail: <path d="M3 4h18v16H3z M3 15l5-4 4 3 4-4 5 4" />,
  knowledge: <path d="M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2z M9 3v18" />,
  config: <path d="M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M19 12a7 7 0 0 0-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 0 0-1.7-1L14.5 2h-5l-.3 2.9a7 7 0 0 0-1.7 1l-2.4-1-2 3.4L2.1 11a7 7 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 1.7 1l.3 2.9h5l.3-2.9a7 7 0 0 0 1.7-1l2.4 1 2-3.4-2-1.6a7 7 0 0 0 .1-1z" />,
  integrations: <path d="M10 3v6M14 3v6M7 9h10v3a5 5 0 0 1-10 0z M11 17v4" />,
  analytics: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
};

function Icon({ name }: { name: string }) {
  return (
    <svg className="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {icons[name]}
    </svg>
  );
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
        <NavLink to="/thumbnail" className="sidebar-link"><Icon name="thumbnail" /> Thumbnail</NavLink>

        <div className="sidebar-section">Workspace</div>
        <NavLink to="/knowledge" className="sidebar-link"><Icon name="knowledge" /> Knowledge</NavLink>
        <NavLink to="/config" className="sidebar-link"><Icon name="config" /> Config</NavLink>
        <NavLink to="/integrations" className="sidebar-link"><Icon name="integrations" /> Integrations</NavLink>
        <NavLink to="/mcp" className="sidebar-link"><Icon name="config" /> MCP Setup</NavLink>

        <div className="sidebar-section">Insights</div>
        <NavLink to="/analytics" className="sidebar-link"><Icon name="analytics" /> Analytics</NavLink>
      </aside>

      <main className="shell-main">
        <Outlet />
      </main>
    </div>
  );
}
