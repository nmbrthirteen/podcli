#!/bin/sh
# Sync the repo Python backend into files/ for go:embed. Run via `go generate
# ./...` before building. files/ is gitignored; this is its only writer.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../../backend"
dest="$here/files"
rm -rf "$dest"
mkdir -p "$dest"
# Plain cp: this runs wherever `go generate` does, and on Windows Git Bash has
# no rsync while its tar reads the leading "D:" of an absolute path as a remote
# host. Skip venv before copying rather than deleting it afterwards.
for entry in "$src"/*; do
  case "${entry##*/}" in
    venv | .venv | requirements.txt) continue ;;
  esac
  cp -R "$entry" "$dest"/
done
rm -f "$dest/models/res10_300x300_ssd_iter_140000.caffemodel" \
  "$dest/models/deploy.prototxt"
find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$dest" -name '*.pyc' -delete
echo "synced backend -> $dest"
