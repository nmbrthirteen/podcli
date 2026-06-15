#!/bin/sh
# Sync the repo Python backend into files/ for go:embed. Run via `go generate
# ./...` before building. files/ is gitignored; this is its only writer.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../../backend"
dest="$here/files"
rm -rf "$dest"
mkdir -p "$dest"
rsync -a \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='venv' \
  --exclude='.venv' \
  --exclude='requirements.txt' \
  --exclude='models/res10_300x300_ssd_iter_140000.caffemodel' \
  --exclude='models/deploy.prototxt' \
  "$src"/ "$dest"/
echo "synced backend -> $dest"
