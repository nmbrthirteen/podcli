import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./Layout";
import StudioHome from "./StudioHome";
import ClipDetail from "./ClipDetail";
import EpisodeWorkspace from "./EpisodeWorkspace";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<StudioHome />} />
          <Route path="/episode" element={<EpisodeWorkspace />} />
          <Route path="/clip/:id" element={<ClipDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
