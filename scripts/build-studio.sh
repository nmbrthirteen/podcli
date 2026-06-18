#!/bin/sh
# Build the studio web-UI bundle: a single self-contained server (esbuild) plus
# the built SPA. Rendering is delegated to the Python backend at runtime, so the
# bundle needs no node_modules — only a Node runtime to execute it.
#
# Usage: scripts/build-studio.sh [out-dir]   (default: dist/studio)
set -e
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/dist/studio}"
cd "$here"

# Type-check the SPA — vite/esbuild don't, so undefined-var bugs in the client
# would otherwise ship silently (blank pages at runtime).
npx tsc --noEmit -p src/ui/client/tsconfig.json
npm run build   # tsc + vite -> dist/ui/web-server.js + dist/ui/public

rm -rf "$out"
mkdir -p "$out"
banner="import{createRequire as _cr}from'module';const require=_cr(import.meta.url);"
# Studio web-UI server + its built SPA.
node -e "require('esbuild').buildSync({entryPoints:['dist/ui/web-server.js'],bundle:true,platform:'node',format:'esm',outfile:'$out/web-server.mjs',banner:{js:\"$banner\"},logLevel:'error'})"
cp -r dist/ui/public "$out/public"
# MCP stdio server (the mcp__podcli__* tools Claude/Codex drive).
node -e "require('esbuild').buildSync({entryPoints:['dist/index.js'],bundle:true,platform:'node',format:'esm',outfile:'$out/mcp-server.mjs',banner:{js:\"$banner\"},logLevel:'error'})"
echo "studio + mcp bundle -> $out"
