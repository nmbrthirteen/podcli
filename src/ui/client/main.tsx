import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./Layout";
import StudioHome from "./StudioHome";
import ClipDetail from "./ClipDetail";
import EpisodeWorkspace from "./EpisodeWorkspace";
import ThumbnailTemplate from "./ThumbnailTemplate";
import KnowledgePage from "./KnowledgePage";
import ConfigPage from "./ConfigPage";
import IntegrationsPage from "./IntegrationsPage";
import McpSetupPage from "./McpSetupPage";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<StudioHome />} />
          <Route path="/episode" element={<EpisodeWorkspace />} />
          <Route path="/thumbnail" element={<ThumbnailTemplate />} />
          <Route path="/clip/:id" element={<ClipDetail />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/integrations" element={<IntegrationsPage />} />
          <Route path="/mcp" element={<McpSetupPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
