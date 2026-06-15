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

cat > "$out/package.json" <<'JSON'
{
  "name": "podcli-remotion-bundle",
  "private": true,
  "type": "module",
  "dependencies": {
    "@remotion/bundler": "^4.0.441",
    "@remotion/renderer": "^4.0.441",
    "remotion": "^4.0.441",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  }
}
JSON

cd "$out"
npm install --omit=dev --no-audit --no-fund --no-package-lock
echo "remotion bundle -> $out"
