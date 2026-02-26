import { readFile, writeFile, copyFile, mkdir } from "fs/promises";
import { existsSync } from "fs";
import { join, basename } from "path";
import { paths } from "../config/paths.js";
import type { Asset, AssetRegistry } from "../models/index.js";

export class AssetManager {
  private registryPath = paths.assetsRegistry;
  private assetsDir = paths.assets;

  private async ensureDir() {
    if (!existsSync(this.assetsDir)) {
      await mkdir(this.assetsDir, { recursive: true });
    }
  }

  async load(): Promise<AssetRegistry> {
    try {
      if (!existsSync(this.registryPath)) return { assets: [] };
      const raw = await readFile(this.registryPath, "utf-8");
      return JSON.parse(raw) as AssetRegistry;
    } catch {
      return { assets: [] };
    }
  }

  private async save(registry: AssetRegistry): Promise<void> {
    await this.ensureDir();
    await writeFile(this.registryPath, JSON.stringify(registry, null, 2), "utf-8");
  }

  async register(name: string, filePath: string, type: Asset["type"]): Promise<Asset> {
    if (!existsSync(filePath)) {
      throw new Error(`File not found: ${filePath}`);
    }
    const registry = await this.load();
    // Update if name exists, otherwise add
    const existing = registry.assets.findIndex((a) => a.name === name);
    const asset: Asset = { name, type, path: filePath, addedAt: new Date().toISOString() };
    if (existing >= 0) {
      registry.assets[existing] = asset;
    } else {
      registry.assets.push(asset);
    }
    await this.save(registry);
    return asset;
  }

  async unregister(name: string): Promise<void> {
    const registry = await this.load();
    registry.assets = registry.assets.filter((a) => a.name !== name);
    await this.save(registry);
  }

  async list(type?: string): Promise<Asset[]> {
    const registry = await this.load();
    if (type) return registry.assets.filter((a) => a.type === type);
    return registry.assets;
  }

  /** Resolve a name or path to an absolute file path. */
  async resolve(nameOrPath: string): Promise<string | null> {
    if (!nameOrPath) return null;
    // Check if it's a registered asset name
    const registry = await this.load();
    const asset = registry.assets.find((a) => a.name === nameOrPath);
    if (asset && existsSync(asset.path)) return asset.path;
    // Otherwise treat as a direct path
    if (existsSync(nameOrPath)) return nameOrPath;
    return null;
  }

  /** Copy a file into ~/.podcli/assets/ and register it. */
  async importFile(sourcePath: string, name: string, type: Asset["type"]): Promise<Asset> {
    await this.ensureDir();
    const destPath = join(this.assetsDir, basename(sourcePath));
    await copyFile(sourcePath, destPath);
    return this.register(name, destPath, type);
  }
}
