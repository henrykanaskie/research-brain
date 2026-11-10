#!/usr/bin/env bash
# research_brain launcher: sets up a virtualenv on first run, then starts the server
# and opens the app in your browser.  Usage: ./run.sh   (Ctrl+C to stop)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${RB_PORT:-8765}"
# RB_HOST: interface to bind. Default 127.0.0.1 (this Mac only). Set RB_HOST=0.0.0.0 (or your
# Tailscale IP) only to reach the app from your phone; see docs/mobile-capture.md for the tradeoff.
HOST="${RB_HOST:-127.0.0.1}"
# RB_NO_OPEN=1: don't open a browser tab (used by the launchd login agent).
NO_OPEN="${RB_NO_OPEN:-}"
VENV=".venv"

pick_python() {
  for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      echo "$py"; return
    fi
  done
  echo "research_brain needs Python 3.10+. Install it (e.g. 'brew install python') and re-run." >&2
  exit 1
}

if [ ! -x "$VENV/bin/python" ]; then
  PY="$(pick_python)"
  echo "→ Creating virtualenv with $($PY --version)…"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --upgrade pip
fi

if [ ! -f "$VENV/.deps-ok" ] || [ requirements.txt -nt "$VENV/.deps-ok" ] || [ constraints.txt -nt "$VENV/.deps-ok" ]; then
  echo "→ Installing dependencies…"
  # constraints.txt: the exact, tested versions (see the file). Fall back to the plain ranges only if a pin
  # can't be installed here (no build for this Python/OS).
  if ! "$VENV/bin/python" -m pip install -q -r requirements.txt -c constraints.txt; then
    echo "   (the tested versions in constraints.txt didn't install here; using the newest compatible ones)"
    "$VENV/bin/python" -m pip install -q -r requirements.txt
  fi
  if [ "${RB_EMBEDDER:-auto}" != "tfidf" ]; then
    echo "→ Installing local embedding model support (sentence-transformers; one-time, a few hundred MB)…"
    "$VENV/bin/python" -m pip install -q "sentence-transformers>=3.0" \
      || echo "   (couldn't install sentence-transformers — falling back to TF-IDF similarity)"
  fi
  touch "$VENV/.deps-ok"
fi

if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ] && [ ! -x "$HOME/.claude/local/claude" ]; then
  echo "⚠ Couldn't find the 'claude' CLI on PATH. Claude features need Claude Code installed and logged in."
  echo "  (If it's installed somewhere unusual: export RB_CLAUDE_BIN=/full/path/to/claude)"
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use — research_brain may already be running: http://127.0.0.1:$PORT"
  [ -n "$NO_OPEN" ] || open "http://127.0.0.1:$PORT" 2>/dev/null || true
  exit 0
fi

case "$HOST" in 0.0.0.0|::|"") LOCAL="127.0.0.1" ;; *) LOCAL="$HOST" ;; esac
if [ -z "$NO_OPEN" ]; then
  ( for _ in $(seq 1 60); do
      if curl -s "http://$LOCAL:$PORT/api/status" >/dev/null 2>&1; then
        open "http://$LOCAL:$PORT" 2>/dev/null || xdg-open "http://$LOCAL:$PORT" 2>/dev/null || true
        break
      fi
      sleep 0.5
    done ) &
fi

if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  echo "⚠ RB_HOST=$HOST: the server is reachable from other devices on that network, with no password."
  echo "  Only do this on a network you trust (e.g. Tailscale). See docs/mobile-capture.md."
fi
# app/cli.py supervises the server: it restarts it when you switch profile in the app, and prints which
# library (vault) it uses. Your data lives in the per-user data folder, not here (see
# docs/storage-and-distribution.md); an older ./vault with notes keeps working in place.
exec "$VENV/bin/python" -m app.cli serve --host "$HOST" --port "$PORT" --no-open
