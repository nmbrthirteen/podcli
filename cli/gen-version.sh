#!/bin/sh
# Write VERSION from package.json, the single source of truth for the version.
# Run via `go generate ./...` before building. VERSION is this script's only writer.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
ver=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$here/../package.json" | head -1)
[ -n "$ver" ] || { echo "gen-version: no \"version\" in package.json" >&2; exit 1; }
printf '%s\n' "$ver" > "$here/VERSION"
echo "version $ver -> $here/VERSION"
