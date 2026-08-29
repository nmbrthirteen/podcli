import { describe, expect, it } from "vitest";
import { webpackOverride } from "./webpack-override.mjs";
import { hdCanvas, overlayFilter } from "./render.mjs";

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

  it("inlines woff font assets so Remotion tabs do not fetch them", () => {
    const config = webpackOverride({
      module: {
        rules: [
          {
            test: /\.(woff(2)?|otf|ttf|eot)(\?v=\d+\.\d+\.\d+)?$/,
            type: "asset/resource",
          },
          {
            test: /\.(png|svg|jpg)$/,
            type: "asset/resource",
          },
        ],
      },
    });
    expect(config.module.rules[0].type).toBe("asset/inline");
    expect(config.module.rules[1].type).toBe("asset/resource");
  });
});

describe("hd overlay canvas", () => {
  it("authors vertical overlays at 1080x1920 even for 4k video", () => {
    expect(hdCanvas(2160, 3840)).toEqual({ width: 1080, height: 1920 });
    expect(hdCanvas(1080, 1920)).toEqual({ width: 1080, height: 1920 });
  });

  it("authors landscape overlays at 1920x1080", () => {
    expect(hdCanvas(3840, 2160)).toEqual({ width: 1920, height: 1080 });
  });

  it("scales the whole overlay including logo to the export", () => {
    expect(overlayFilter()).toContain("scale2ref");
  });
});
