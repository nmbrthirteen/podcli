import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./Layout";
import StudioHome from "./StudioHome";
import ClipDetail from "./ClipDetail";
import EpisodeWorkspace from "./EpisodeWorkspace";
import ThumbnailStudio from "./ThumbnailStudio";
import ContentStudio from "./ContentStudio";
import HighlightsPage from "./HighlightsPage";
import AssetsPage from "./AssetsPage";
import AnalyticsPage from "./AnalyticsPage";
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
          <Route path="/content" element={<ContentStudio />} />
          <Route path="/highlights" element={<HighlightsPage />} />
          <Route path="/reel" element={<Navigate to="/highlights" replace />} />
          <Route path="/thumbnails" element={<ThumbnailStudio />} />
          <Route path="/thumbnail" element={<Navigate to="/thumbnails" replace />} />
          <Route path="/clip/:id" element={<ClipDetail />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/integrations" element={<IntegrationsPage />} />
          <Route path="/mcp" element={<McpSetupPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
