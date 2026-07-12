/**
 * Remotion's esbuild-loader reads a tsconfig via `require('typescript')` unless
 * `tsconfigRaw` is already set. The runtime lives under the user's home dir, so
 * Node walks up and resolves whatever TypeScript it finds there — a TypeScript 7
 * install has no `ts.sys` and hard-crashes the bundle. Pinning tsconfigRaw skips
 * that lookup. Empty matches the shipped default: the runtime bundle has no
 * typescript, and every composition imports React, so esbuild's classic JSX
 * transform is correct.
 */
const TSCONFIG_RAW = { compilerOptions: {} };

const pinTsconfig = (entry) =>
  entry &&
  typeof entry === "object" &&
  typeof entry.loader === "string" &&
  entry.loader.includes("esbuild-loader")
    ? { ...entry, options: { ...entry.options, tsconfigRaw: TSCONFIG_RAW } }
    : entry;

export const webpackOverride = (config) => ({
  ...config,
  module: {
    ...config.module,
    rules: (config.module?.rules ?? []).map((rule) =>
      Array.isArray(rule?.use) ? { ...rule, use: rule.use.map(pinTsconfig) } : rule,
    ),
  },
});
