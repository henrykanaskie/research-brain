#!/usr/bin/env bash
# Install (or remove) the research_brain launchd agents on macOS:
#   com.research_brain.server  runs ./run.sh at login, restarts it if it crashes
#                              (log: ~/Library/Logs/research_brain.log)
#   com.research_brain.backup  runs scripts/vault_backup.sh every night at 02:30
#                              (log: ~/Library/Logs/research_brain-backup.log)
#
# Usage:  scripts/install_launchd.sh              install / reinstall both
#         scripts/install_launchd.sh --uninstall  stop and remove both
# Options: --vault DIR (default: $RB_VAULT, else the library the app opens now)   --no-server   --no-backup
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATES="$REPO/scripts/launchd"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
VAULT="${RB_VAULT:-$(python3 "$REPO/app/paths.py" 2>/dev/null || echo "$REPO/vault")}"
MODE=install
WANT_SERVER=1
WANT_BACKUP=1

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) MODE=uninstall ;;
    --vault) shift; VAULT="${1:?--vault needs a directory}" ;;
    --no-server) WANT_SERVER=0 ;;
    --no-backup) WANT_BACKUP=0 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

if [ "$(uname -s)" != "Darwin" ] && [ -z "${RB_LAUNCHD_DRY_RUN:-}" ]; then
  echo "install_launchd.sh is for macOS (launchd). On Linux use a systemd user unit or cron instead." >&2
  exit 1
fi

# launchctl wrapper; RB_LAUNCHD_DRY_RUN=1 prints instead of running (for testing off-Mac).
lc() {
  if [ -n "${RB_LAUNCHD_DRY_RUN:-}" ]; then echo "launchctl $*"; else launchctl "$@"; fi
}

# Escape a value for both XML text and a sed replacement (delimiter |).
esc() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/[\\|&]/\\&/g'
}

unload() {  # label
  lc bootout "$DOMAIN/$1" >/dev/null 2>&1 || true
}

LABELS=()
[ "$WANT_SERVER" = 1 ] && LABELS+=(com.research_brain.server)
[ "$WANT_BACKUP" = 1 ] && LABELS+=(com.research_brain.backup)

if [ "$MODE" = uninstall ]; then
  for label in "${LABELS[@]}"; do
    unload "$label"
    rm -f "$AGENTS/$label.plist"
    echo "Removed $label"
  done
  echo "Logs (if any) are left in ~/Library/Logs/research_brain*.log; your vault is untouched."
  exit 0
fi

mkdir -p "$AGENTS" "$HOME/Library/Logs"
VAULT="$(mkdir -p "$VAULT" && cd "$VAULT" && pwd)"
chmod +x "$REPO/run.sh" "$REPO/scripts/vault_backup.sh"
R="$(esc "$REPO")"; V="$(esc "$VAULT")"; H="$(esc "$HOME")"

for label in "${LABELS[@]}"; do
  src="$TEMPLATES/$label.plist"
  dst="$AGENTS/$label.plist"
  sed -e "s|__REPO__|$R|g" -e "s|__VAULT__|$V|g" -e "s|__HOME__|$H|g" "$src" > "$dst.tmp"
  if command -v plutil >/dev/null 2>&1; then plutil -lint -s "$dst.tmp"; fi
  mv "$dst.tmp" "$dst"
  unload "$label"
  lc bootstrap "$DOMAIN" "$dst"
  echo "Installed $label -> $dst"
done

[ "$WANT_SERVER" = 1 ] && echo "The server now starts at login: http://127.0.0.1:${RB_PORT:-8765} (log: ~/Library/Logs/research_brain.log)"
[ "$WANT_BACKUP" = 1 ] && echo "Vault $VAULT is snapshotted nightly at 02:30. Run one now: launchctl kickstart $DOMAIN/com.research_brain.backup"
echo "Undo with: scripts/install_launchd.sh --uninstall"
