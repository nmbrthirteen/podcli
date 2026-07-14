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
mv "$out/remotion/package.json" "$out/package.json"
mv "$out/remotion/package-lock.json" "$out/package-lock.json"

cd "$out"
npm ci --omit=dev --no-audit --no-fund
echo "remotion bundle -> $out"
