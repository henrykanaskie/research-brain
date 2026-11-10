#!/usr/bin/env bash
# Snapshot the research_brain vault into a local git repository (vault/.git).
#
#   scripts/vault_backup.sh [VAULT_DIR]      default: $RB_VAULT, else the library the app would open
#                                            (active profile in the data folder, or a legacy ./vault)
#
# First run: `git init`s the vault with a .gitignore that leaves out PDFs and derived data
# (pdfs/, .index/, .trash/, plus exports/ which View > Export regenerates, and backups/, the
# pre-migration zips).
# Every run: commits any changes as "vault snapshot <ISO date>".
# Nothing is pushed anywhere; add a remote yourself (e.g. a private repo) if you want off-machine copies.
# Safe to run when nothing changed (exits 0 without committing).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
default_vault() {  # same resolution as the app (app/paths.py is standard-library only)
  python3 "$HERE/../app/paths.py" 2>/dev/null || python3 "$HERE/../paths.py" 2>/dev/null || echo "$HERE/../vault"
}
VAULT="${1:-${RB_VAULT:-$(default_vault)}}"
AUTHOR_NAME="research_brain"
AUTHOR_EMAIL="research_brain@localhost"

if ! command -v git >/dev/null 2>&1; then
  echo "vault_backup: git is not installed (on macOS run: xcode-select --install)" >&2
  exit 1
fi
if [ ! -d "$VAULT" ]; then
  echo "vault_backup: no vault at $VAULT" >&2
  exit 1
fi
cd "$VAULT"

# The vault gets its own repo even if it sits inside another checkout: look for vault/.git itself.
if [ ! -d .git ]; then
  git init -q
  echo "vault_backup: initialised a git repository in $(pwd)"
fi

# Keep the ignore rules present even if someone deleted the file.
touch .gitignore
for pat in "pdfs/" ".index/" ".trash/" "exports/" "backups/"; do
  grep -qxF "$pat" .gitignore || printf '%s\n' "$pat" >> .gitignore
done

# Commit identity is set per command, so the user's global git config is never touched or used.
g() {
  git -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" -c commit.gpgsign=false "$@"
}

g add -A
if g diff --cached --quiet; then
  echo "vault_backup: no changes"
  exit 0
fi
STAMP="$(date +%Y-%m-%dT%H:%M:%S%z)"
GIT_AUTHOR_NAME="$AUTHOR_NAME" GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL" \
GIT_COMMITTER_NAME="$AUTHOR_NAME" GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL" \
  g commit -q --no-verify -m "vault snapshot $STAMP"
echo "vault_backup: committed $(git rev-parse --short HEAD) (vault snapshot $STAMP)"
