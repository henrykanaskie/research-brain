#!/usr/bin/env bash
# Install or update research_brain as a command-line app (`research-brain`), separate from your data.
#
#   ./install.sh                         from a checkout or an unpacked release (installs this folder)
#   RB_SOURCE=<wheel file, wheel URL or git+https URL@tag> ./install.sh
#   curl -fsSL <release URL>/install.sh | RB_SOURCE=<wheel URL> bash        (the one-line version)
#
#   ./install.sh  (inside a release folder, e.g. the lab's shared update folder: installs its latest.json)
#
# Uses uv (installed first if missing; it brings its own Python), else pipx, else a private virtualenv in
# the data folder with a link in ~/.local/bin. Re-running it updates the app. Your library is never touched: it lives in
#   macOS ~/Library/Application Support/research_brain   Linux ~/.local/share/research_brain
# It also adds a "research brain" app (Launchpad/Dock on macOS, the app menu on Linux) that starts it only when
# you open it and stops it after its window closes. Windows: use install.ps1.
# Options (environment): RB_EMBEDDINGS=1 also installs sentence-transformers (better similarity, ~500 MB).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
SRC="${RB_SOURCE:-}"
if [ -z "$SRC" ]; then
  if [ -f "$HERE/pyproject.toml" ] && [ -d "$HERE/app" ]; then SRC="$HERE"
  elif [ -f "$HERE/latest.json" ]; then SRC="$HERE"   # run from a release / the lab's update folder
  else echo "Set RB_SOURCE to a research_brain wheel (file or URL), a release folder, or git+https://...@vX.Y.Z" >&2; exit 2; fi
fi
UPDATE_SOURCE=""
if [ -d "$SRC" ] && [ -f "$SRC/latest.json" ]; then
  # a release folder: install the wheel latest.json names, and update from this folder from now on
  UPDATE_SOURCE="$(cd "$SRC" && pwd)"
  WHEEL="$(sed -n 's/.*"wheel": *"\([^"]*\)".*/\1/p' "$SRC/latest.json" | head -n1)"
  [ -n "$WHEEL" ] && [ -f "$UPDATE_SOURCE/$WHEEL" ] || { echo "latest.json in $SRC names a wheel that isn't there" >&2; exit 2; }
  SRC="$UPDATE_SOURCE/$WHEEL"
  [ -f "$UPDATE_SOURCE/constraints.txt" ] && HERE_CONSTRAINTS="$UPDATE_SOURCE/constraints.txt"
fi
SPEC="$SRC"
if [ "${RB_EMBEDDINGS:-}" = 1 ]; then
  case "$SRC" in /*) URL="file://$SRC" ;; *) URL="$SRC" ;; esac
  SPEC="research-brain[embeddings] @ $URL"
fi

pick_python() {
  for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      echo "$py"; return
    fi
  done
  case "$(uname -s)" in
    Darwin) hint="brew install python, or python.org" ;;
    *) hint="e.g. sudo apt install python3 python3-venv (Debian/Ubuntu) or sudo dnf install python3 (Fedora)" ;;
  esac
  echo "research_brain needs Python 3.10 or newer ($hint)." >&2
  exit 1
}

# Pin the exact dependency versions the app was tested with, when constraints.txt is next to this script.
HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo .)"
CONSTRAINTS="${HERE_CONSTRAINTS:-}"
[ -z "$CONSTRAINTS" ] && [ -f "$HERE/constraints.txt" ] && CONSTRAINTS="$HERE/constraints.txt"

# uv brings its own Python, so nobody has to install one first. Get it if it's missing (RB_NO_UV=1 skips
# this and uses pipx or your Python instead).
if ! command -v uv >/dev/null 2>&1 && [ "${RB_NO_UV:-}" != 1 ] && command -v curl >/dev/null 2>&1; then
  echo "→ Installing uv (Astral's Python installer, into ~/.local/bin)"
  if curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null; then
    export PATH="$HOME/.local/bin:$PATH"
  else echo "  Couldn't install uv; falling back to pipx or your Python."; fi
fi

if command -v uv >/dev/null 2>&1; then
  echo "→ Installing with uv"
  # Python 3.12: what the pinned versions in constraints.txt are built and tested for (uv downloads it if
  # you don't have it). Picking the first Python >= 3.10 on PATH could mean compiling numpy from source.
  # --compile-bytecode: compile now, so the first start after installing isn't seconds slower than the rest
  if [ -n "$CONSTRAINTS" ]; then uv tool install --force --compile-bytecode --python 3.12 -c "$CONSTRAINTS" "$SPEC"
  else uv tool install --force --compile-bytecode --python 3.12 "$SPEC"; fi
elif command -v pipx >/dev/null 2>&1; then
  echo "→ Installing with pipx"
  if [ -n "$CONSTRAINTS" ]; then pipx install --force --pip-args "-c $CONSTRAINTS" "$SPEC"
  else pipx install --force "$SPEC"; fi
else
  PY="$(pick_python)"
  # the pins in constraints.txt are for Python 3.12+; an older Python gets the newest versions that fit it
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || CONSTRAINTS=""
  case "$(uname -s)" in
    Darwin) DATA="$HOME/Library/Application Support/research_brain" ;;
    *) DATA="${XDG_DATA_HOME:-$HOME/.local/share}/research_brain" ;;
  esac
  VENV="$DATA/app-venv"   # the app's code only; your libraries are in $DATA/profiles
  echo "→ Installing into $VENV"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --upgrade pip
  if [ -n "$CONSTRAINTS" ]; then "$VENV/bin/python" -m pip install -q --upgrade -c "$CONSTRAINTS" "$SPEC"
  else "$VENV/bin/python" -m pip install -q --upgrade "$SPEC"; fi
  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENV/bin/research-brain" "$HOME/.local/bin/research-brain"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Add ~/.local/bin to your PATH to run research-brain from anywhere." ;; esac
fi

RB="$(command -v research-brain 2>/dev/null || echo "$HOME/.local/bin/research-brain")"
echo "→ Installed: $("$RB" --version)"
# Load everything once now: macOS checks newly installed native libraries (numpy, pypdfium2, the window)
# the first time they load, which made the first start after an install take ~20 s instead of ~1 s.
RB_PY="$(head -1 "$RB" | sed -n 's/^#! *//p')"
if [ -x "$RB_PY" ]; then
  echo "→ Getting it ready for its first start (up to half a minute)"
  "$RB_PY" -c "import research_brain.server
for m in ('numpy', 'pypdfium2', 'pypdf', 'lxml.etree',
          'watchdog.observers', 'citeproc', 'webview', 'AppKit', 'WebKit'):
    try:
        __import__(m)
    except ImportError:
        pass" >/dev/null 2>&1 || true
fi
if [ -n "$UPDATE_SOURCE" ]; then
  "$RB" update --source "$UPDATE_SOURCE" >/dev/null && echo "  Updates will come from $UPDATE_SOURCE (research-brain update)."
fi
if [ "${RB_NO_DESKTOP:-}" != 1 ]; then
  # an app entry that opens research brain only when you click it (Launchpad/Dock on macOS, the app menu on
  # Linux); the app stops by itself a few minutes after its window closes. Nothing runs at login.
  if APP_MSG="$("$RB" install-app 2>&1)"; then echo "$APP_MSG" | sed 's/^/  /'
  else echo "  ! Couldn't add the app entry: $APP_MSG"; echo "    Try again later with: research-brain install-app"; fi
  echo "  (RB_NO_DESKTOP=1 skips the app entry; research-brain install-app --remove takes it away.)"
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "  The 'claude' CLI isn't installed. Everything except summaries, critiques and Ask/Research works without it."
fi
echo "  Open it from the app icon, or with: research-brain open    (browser extension: see extension/README.md)"
