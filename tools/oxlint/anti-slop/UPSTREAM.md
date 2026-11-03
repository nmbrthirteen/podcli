# anti-slop provenance

- Source repository: https://github.com/dmmulroy/anti-slop
- Source commit: 13909c9e07590f5a85d93c62712259fe14a883f7
- Installer: `skills/install-anti-slop/scripts/install.mjs` from that commit, run from the repository root
- Installed paths:
  - `tools/oxlint/anti-slop/` (generic plugin entry point `index.ts`, `rules/`, `shared/`)
  - `tools/oxlint/anti-slop/effect/` (opt-in Effect plugin, copied but not registered)
  - `tools/oxlint/anti-slop/vendor/eslint-stylistic/` (vendored license and its own `UPSTREAM.md`)
- Configuration: `.oxlintrc.json` registers `./tools/oxlint/anti-slop/index.ts` as `anti-slop` and enables all 19 generic rules at `"error"`, including the native `oxc/no-accumulating-spread`.
- Dependencies: `oxlint` and `@oxlint/plugins`, both pinned to 1.78.0 as devDependencies.

## Deviations from upstream

- Plugin source: none. The copied files are unmodified.
- Effect rules: not enabled, because `effect` is not a direct dependency.
- Ignores: `.oxlintrc.json` adds `**/node_modules/**` and `dist/**` to the upstream agent-directory list and the plugin directory.
