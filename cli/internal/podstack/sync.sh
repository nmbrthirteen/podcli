#!/bin/sh
# Sync the repo PodStack slash commands into commands/ for go:embed. Run via
# `go generate ./...` before building. commands/ is gitignored; this is its
# only writer.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../../.claude/commands"
dest="$here/commands"
rm -rf "$dest"
mkdir -p "$dest"
cp "$src"/*.md "$dest"/
echo "synced PodStack commands -> $dest"
