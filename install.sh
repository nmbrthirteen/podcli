#!/bin/sh
# podcli installer — downloads the prebuilt native binary (no Go, Node, Python,
# or ffmpeg needed; the binary provisions those itself on first run).
# Usage: curl -fsSL https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.sh | sh
# Uninstall: curl -fsSL https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.sh | sh -s -- --uninstall
set -eu

REPO="nmbrthirteen/podcli"
err() { echo "podcli-install: $*" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || err "curl is required"

os=$(uname -s 2>/dev/null || echo unknown)
arch=$(uname -m 2>/dev/null || echo unknown)
case "$os" in
  Darwin) goos=darwin; home_dir="$HOME/Library/Application Support/podcli" ;;
  Linux) goos=linux; home_dir="${XDG_DATA_HOME:-$HOME/.local/share}/podcli" ;;
  *) err "unsupported OS: $os (on Windows use install.ps1)" ;;
esac
bin_dir="$home_dir/bin"

if [ "${1:-}" = "--uninstall" ]; then
  echo "Uninstalling podcli…"
  for d in /usr/local/bin "$HOME/.local/bin"; do
    link="$d/podcli"
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$bin_dir/podcli" ]; then
      if rm -f "$link"; then
        echo "  removed link: $link"
      else
        echo "  warning: could not remove link: $link" >&2
      fi
    fi
  done
  for p in "$bin_dir" "$home_dir/runtime" "$home_dir/models" "$home_dir/tools"; do
    if [ -e "$p" ]; then
      if rm -rf "$p"; then
        echo "  removed: $p"
      else
        echo "  warning: could not remove: $p" >&2
      fi
    fi
  done
  echo "  removed app files from: $home_dir"
  echo "  kept user data (config, knowledge, presets, assets, history, cache)."
  echo "  To remove everything: rm -rf '$home_dir'"
  exit 0
fi

case "$arch" in
  x86_64|amd64) goarch=amd64 ;;
  arm64|aarch64) goarch=arm64 ;;
  *) err "unsupported architecture: $arch" ;;
esac
target="${goos}-${goarch}"
if [ "$target" = "darwin-amd64" ]; then
  err "Intel Macs aren't supported yet (coming in v2.0.1). Apple Silicon, Linux, and Windows are available."
fi
mkdir -p "$bin_dir"

version="${PODCLI_VERSION:-}"
if [ -z "$version" ]; then
  version=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | sed -n 's/.*"tag_name":[ ]*"v\{0,1\}\([^"]*\)".*/\1/p' | head -1)
  [ -n "$version" ] || err "could not resolve the latest release"
fi

asset="podcli-${target}"
base="https://github.com/$REPO/releases/download/v${version}"
echo "Installing podcli v${version} (${target})…"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fSL --proto '=https' --tlsv1.2 "$base/$asset" -o "$tmp/$asset" || err "download failed"

if curl -fsSL "$base/checksums.txt" -o "$tmp/sums" 2>/dev/null; then
  want=$(grep "[ /]$asset\$" "$tmp/sums" | awk '{print $1}' | head -1)
  if [ -n "$want" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
      got=$(sha256sum "$tmp/$asset" | awk '{print $1}')
    else
      got=$(shasum -a 256 "$tmp/$asset" | awk '{print $1}')
    fi
    [ "$got" = "$want" ] || err "checksum mismatch (got $got want $want)"
    echo "  checksum verified"
  else
    echo "  no checksum entry for $asset — skipped verification" >&2
  fi
else
  echo "  no checksums.txt in release — skipped verification" >&2
fi

cp "$tmp/$asset" "$bin_dir/podcli"
chmod 0755 "$bin_dir/podcli"

# Apple Silicon's kernel kills cross-compiled (Linux-built) arm64 binaries whose
# signature it won't accept, even with a valid-on-disk ad-hoc signature. Re-sign
# ad-hoc on the Mac so the binary runs.
if [ "$goos" = "darwin" ] && command -v codesign >/dev/null 2>&1; then
  codesign --force --sign - "$bin_dir/podcli" >/dev/null 2>&1 || true
fi
echo "  installed: $bin_dir/podcli"

linked=""
for d in /usr/local/bin "$HOME/.local/bin"; do
  if [ -d "$d" ] && [ -w "$d" ]; then
    ln -sf "$bin_dir/podcli" "$d/podcli" 2>/dev/null && { linked="$d/podcli"; break; }
  fi
done

echo
if [ -n "$linked" ]; then
  echo "Done — run:  podcli"
else
  echo "Done. Add podcli to your PATH:"
  echo "  export PATH=\"$bin_dir:\$PATH\""
  echo "Then run:  podcli"
fi
