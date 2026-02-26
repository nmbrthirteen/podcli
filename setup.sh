#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ============================================================
# podcli — Install & Launch
# Usage:
#   ./setup.sh              # Install everything + open UI
#   ./setup.sh --install    # Install only (no launch)
#   ./setup.sh --ui         # Launch UI only (skip install)
#   ./setup.sh --mcp        # Show MCP config (for Claude Desktop/Code)
# ============================================================

MODE="${1:-full}"

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║        🎬  podcli  v1.0              ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ---- Install ----
install() {
  echo "━━━ [1/6] Checking system dependencies ━━━"

  local MISSING=0

  if ! command -v ffmpeg &>/dev/null; then
    echo "  ✗ FFmpeg not found"
    echo "    → macOS:   brew install ffmpeg"
    echo "    → Ubuntu:  sudo apt install ffmpeg"
    echo "    → Windows: choco install ffmpeg"
    MISSING=1
  else
    echo "  ✓ FFmpeg"
  fi

  if ! command -v python3 &>/dev/null; then
    echo "  ✗ Python 3 not found"
    MISSING=1
  else
    echo "  ✓ Python $(python3 --version 2>&1 | awk '{print $2}')"
  fi

  if ! command -v node &>/dev/null; then
    echo "  ✗ Node.js not found"
    MISSING=1
  else
    echo "  ✓ Node $(node --version)"
  fi

  if [ "$MISSING" -eq 1 ]; then
    echo ""
    echo "  Please install the missing dependencies above and re-run."
    exit 1
  fi

  echo ""
  echo "━━━ [2/6] Creating directories ━━━"
  CLIPPER_HOME="${PODCLI_HOME:-$HOME/.podcli}"
  mkdir -p "$CLIPPER_HOME"/{cache/transcripts,working,working/uploads,output,logs}
  echo "  ✓ $CLIPPER_HOME"

  echo ""
  echo "━━━ [3/6] Python virtual environment ━━━"
  if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  ✓ Created venv"
  else
    echo "  ✓ venv exists"
  fi

  source venv/bin/activate
  echo ""
  echo "━━━ [4/6] Installing Python packages ━━━"
  pip install -q --upgrade pip
  pip install -q -r backend/requirements.txt 2>&1 | tail -3
  echo "  ✓ Python packages ready"

  echo ""
  echo "━━━ [5/6] Installing Node packages ━━━"
  npm install --silent 2>&1 | tail -1
  echo "  ✓ Node packages ready"

  echo ""
  echo "━━━ [6/6] Building TypeScript ━━━"
  npx tsc
  echo "  ✓ Build complete"

  # Create .env
  VENV_PYTHON="$(pwd)/venv/bin/python3"
  if [ ! -f .env ]; then
    cp .env.example .env
  fi
  # Always update PYTHON_PATH to point at venv (handles re-runs)
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|^PYTHON_PATH=.*|PYTHON_PATH=$VENV_PYTHON|" .env
  else
    sed -i "s|^PYTHON_PATH=.*|PYTHON_PATH=$VENV_PYTHON|" .env
  fi
  echo "  ✓ .env configured (python: $VENV_PYTHON)"

  # Copy public assets to dist
  mkdir -p dist/ui/public
  cp -r src/ui/public/* dist/ui/public/

  echo ""
  echo "  ✅ Installation complete!"
  echo ""
}

# ---- Launch UI ----
launch_ui() {
  echo "  Starting web UI..."
  echo ""

  # Ensure public files are in dist
  mkdir -p dist/ui/public
  cp -r src/ui/public/* dist/ui/public/ 2>/dev/null || true

  # Always use venv python (absolute path, no spaces issue)
  if [ -f "venv/bin/python3" ]; then
    export PYTHON_PATH="$(cd "$(pwd)" && pwd)/venv/bin/python3"
    echo "  Using Python: $PYTHON_PATH"
  fi

  # Load .env (line-by-line to handle spaces in values)
  if [ -f .env ]; then
    while IFS='=' read -r key value; do
      # Skip comments and empty lines
      [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
      # PYTHON_PATH from venv takes priority over .env
      [[ "$key" == "PYTHON_PATH" ]] && continue
      export "$key=$value"
    done < .env
  fi

  exec npx tsx src/ui/web-server.ts
}

# ---- Show MCP config ----
show_mcp() {
  VENV_PYTHON="$(pwd)/venv/bin/python3"
  echo "  ── Claude Desktop config ──"
  echo "  Add to ~/Library/Application Support/Claude/claude_desktop_config.json:"
  echo ""
  echo "  {"
  echo "    \"mcpServers\": {"
  echo "      \"podcli\": {"
  echo "        \"command\": \"node\","
  echo "        \"args\": [\"$(pwd)/dist/index.js\"],"
  echo "        \"env\": {"
  echo "          \"PYTHON_PATH\": \"$VENV_PYTHON\""
  echo "        }"
  echo "      }"
  echo "    }"
  echo "  }"
  echo ""
  echo "  ── Claude Code ──"
  echo "  claude mcp add podcli -- node $(pwd)/dist/index.js"
  echo ""
}

# ---- Route ----
case "$MODE" in
  --install)
    install
    ;;
  --ui)
    launch_ui
    ;;
  --mcp)
    show_mcp
    ;;
  full|*)
    install
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    launch_ui
    ;;
esac
