import { describe, expect, it } from "vitest";
import { webpackOverride } from "./webpack-override.mjs";

// TypeScript 7 dropped the JS compiler API, so @remotion/bundler's esbuild-loader dies
// on `typescript.sys.readFile` the moment it can resolve a TypeScript it wasn't built
// for. Pinning tsconfigRaw is the only thing that stops it looking. Nothing else in CI
// renders a frame, so without this test that pin can be deleted and every caption
// render breaks while CI stays green.
const esbuildEntries = (config) =>
  config.module.rules
    .flatMap((rule) => (Array.isArray(rule.use) ? rule.use : []))
    .filter((entry) => entry?.loader?.includes("esbuild-loader"));

const remotionLikeConfig = () => ({
  module: {
    rules: [
      { test: /\.css$/, use: [{ loader: "css-loader" }] },
      {
        test: /\.tsx?$/,
        use: [
          {
            loader: "/app/node_modules/@remotion/bundler/dist/esbuild-loader/index.js",
            options: { loader: "tsx", target: "chrome85" },
          },
        ],
      },
    ],
  },
});

describe("webpackOverride", () => {
  it("pins tsconfigRaw on every esbuild-loader rule", () => {
    const entries = esbuildEntries(webpackOverride(remotionLikeConfig()));
    expect(entries).toHaveLength(1);
    for (const entry of entries) {
      expect(entry.options.tsconfigRaw).toBeDefined();
    }
  });

  it("keeps the loader's existing options", () => {
    const [entry] = esbuildEntries(webpackOverride(remotionLikeConfig()));
    expect(entry.options.loader).toBe("tsx");
    expect(entry.options.target).toBe("chrome85");
  });

  it("leaves non-esbuild rules untouched", () => {
    const config = webpackOverride(remotionLikeConfig());
    expect(config.module.rules[0].use[0]).toEqual({ loader: "css-loader" });
  });
});
