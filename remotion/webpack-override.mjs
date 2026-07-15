const TSCONFIG_RAW = { compilerOptions: {} };

const pinTsconfig = (entry) =>
  entry &&
  typeof entry === "object" &&
  typeof entry.loader === "string" &&
  entry.loader.includes("esbuild-loader")
    ? { ...entry, options: { ...entry.options, tsconfigRaw: TSCONFIG_RAW } }
    : entry;

const isFontAssetRule = (rule) =>
  rule &&
  rule.type === "asset/resource" &&
  rule.test instanceof RegExp &&
  String(rule.test).includes("woff");

export const webpackOverride = (config) => ({
  ...config,
  module: {
    ...config.module,
    rules: (config.module?.rules ?? []).map((rule) => {
      if (isFontAssetRule(rule)) {
        return { ...rule, type: "asset/inline" };
      }
      return Array.isArray(rule?.use) ? { ...rule, use: rule.use.map(pinTsconfig) } : rule;
    }),
  },
});
