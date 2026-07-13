#!/bin/sh
# Build the Remotion render bundle: the remotion/ project plus a production
# node_modules with only the render deps. Native bindings (@rspack, the Remotion
# compositor) are per-platform, so this MUST run on each target platform/arch in
# CI — the produced bundle is not portable across os/arch.
#
# Layout (extracted to the runtime dir): remotion/ + node_modules/ as siblings of
# backend/, so render.mjs resolves node_modules and the Python backend resolves
# runtime/remotion/render.mjs + runtime/node_modules.
#
# Usage: scripts/build-remotion.sh [out-dir]   (default: dist/remotion-bundle)
set -e
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/dist/remotion-bundle}"

rm -rf "$out"
mkdir -p "$out"
cp -R "$here/remotion" "$out/remotion"
cp "$here/tsconfig.json" "$out/tsconfig.json" 2>/dev/null || true

# Pin exact versions from the root lockfile so every release ships the tree we
# test against instead of whatever the caret range resolves to on release day.
locked() {
  v=$(cd "$here" && node -p "require('./package-lock.json').packages['node_modules/$1'].version" 2>/dev/null)
  if [ -z "$v" ] || [ "$v" = "undefined" ]; then
    echo "build-remotion: $1 not found in package-lock.json" >&2
    exit 1
  fi
  printf '%s' "$v"
}

remotion_bundler=$(locked "@remotion/bundler")
remotion_renderer=$(locked "@remotion/renderer")
remotion_core=$(locked "remotion")
react=$(locked "react")
react_dom=$(locked "react-dom")
dm_sans=$(locked "@fontsource/dm-sans")

cat > "$out/package.json" <<JSON
{
  "name": "podcli-remotion-bundle",
  "private": true,
  "type": "module",
  "dependencies": {
    "@remotion/bundler": "$remotion_bundler",
    "@remotion/renderer": "$remotion_renderer",
    "remotion": "$remotion_core",
    "react": "$react",
    "react-dom": "$react_dom",
    "@fontsource/dm-sans": "$dm_sans"
  }
}
JSON

cd "$out"
npm install --omit=dev --no-audit --no-fund --no-package-lock
echo "remotion bundle -> $out"
