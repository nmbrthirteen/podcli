import type { Express } from "express";
import multer from "multer";
import { existsSync } from "fs";
import { mkdir } from "fs/promises";
import { join, extname } from "path";
import { v4 as uuidv4 } from "uuid";
import type { PythonExecutor } from "../services/python-executor.js";
import { paths } from "../config/paths.js";

function routeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export interface ConfigIntegrationRouteDeps {
  executor: PythonExecutor;
  uploadDir: string;
}

export function registerConfigIntegrationRoutes(
  app: Express,
  deps: ConfigIntegrationRouteDeps
): void {
  const { executor, uploadDir } = deps;
  const projectRoot = paths.projectRoot;

  app.get("/api/config/status", async (_req, res) => {
    try {
      const result = await executor.execute<Record<string, unknown>>("manage_config", { action: "status" });
      res.json(result.data ?? {});
    } catch (err) {
      res.status(500).json({ error: routeError(err) });
    }
  });

  app.post("/api/config/migrate", async (_req, res) => {
    try {
      const result = await executor.execute<Record<string, unknown>>("manage_config", { action: "migrate" });
      res.json(result.data ?? {});
    } catch (err) {
      res.status(500).json({ error: routeError(err) });
    }
  });

  app.get("/api/config/export", async (_req, res) => {
    try {
      const bundlePath = join(paths.working, `profile-export-${uuidv4()}.zip`);
      await mkdir(paths.working, { recursive: true });
      const result = await executor.execute<{ bundle: string }>("manage_config", {
        action: "export",
        bundle_path: bundlePath,
      });
      const file = result.data?.bundle ?? bundlePath;
      if (!existsSync(file)) {
        res.status(500).json({ error: "Export failed: bundle not created" });
        return;
      }
      res.download(file, "podcli-profile.zip");
    } catch (err) {
      res.status(500).json({ error: routeError(err) });
    }
  });

  const configUpload = multer({
    storage: multer.diskStorage({
      destination: async (_req, _file, cb) => {
        await mkdir(uploadDir, { recursive: true });
        cb(null, uploadDir);
      },
      filename: (_req, _file, cb) => {
        cb(null, `profile-import-${uuidv4()}.zip`);
      },
    }),
    limits: { fileSize: 512 * 1024 * 1024 },
    fileFilter: (_req, file, cb) => {
      const ext = extname(file.originalname).toLowerCase();
      if (ext === ".zip") cb(null, true);
      else cb(new Error("Profile import must be a .zip file"));
    },
  });

  app.post("/api/config/import", configUpload.single("bundle"), async (req, res) => {
    try {
      if (!req.file?.path) {
        res.status(400).json({ error: "Missing bundle file" });
        return;
      }
      const activate = req.body?.activate === "1" || req.body?.activate === true;
      const result = await executor.execute<Record<string, unknown>>("manage_config", {
        action: "import",
        bundle_path: req.file.path,
        activate,
      });
      res.json(result.data ?? {});
    } catch (err) {
      res.status(500).json({ error: routeError(err) });
    }
  });

  app.get("/api/integrations", async (_req, res) => {
    try {
      const result = await executor.execute<{ integrations: unknown[] }>("manage_integrations", {
        action: "list",
      });
      res.json(result.data ?? { integrations: [] });
    } catch (err) {
      res.status(500).json({ error: routeError(err) });
    }
  });

  app.post("/api/integrations/:name", async (req, res) => {
    const name = req.params.name;
    const enabled = !!req.body?.enabled;
    try {
      const result = await executor.execute<{ name: string; enabled: boolean }>("manage_integrations", {
        action: enabled ? "enable" : "disable",
        name,
      });
      res.json(result.data ?? { name, enabled });
    } catch (err) {
      res.status(500).json({ error: routeError(err) });
    }
  });

  app.get("/api/integration-info", (_req, res) => {
    const distPath = join(projectRoot, "dist", "index.js");
    res.json({
      dist_path: distPath,
      project_root: projectRoot,
      server_ok: existsSync(distPath),
    });
  });
}
