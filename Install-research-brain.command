#!/bin/bash
# Double-click in Finder to install research brain, or to update it, and then open it.
# (Blocked or "permission denied"? See "Install it" in README.md: open Terminal, type "bash ", drag this file
# into the Terminal window and press Return.)
cd "$(dirname "$0")" || exit 1
clear
echo "Installing research brain."
echo "The first time takes a few minutes (it downloads about 280 MB). Keep this window open."
echo
if bash ./install.sh; then
  echo
  echo "Done. research brain is in your Applications folder and in Launchpad."
  echo "Opening it now. Next time, open it like any other app."
  open -a "research brain" 2>/dev/null || ("$HOME/.local/bin/research-brain" open >/dev/null 2>&1 &)
  echo
  read -r -p "You can close this window (or press Return)." _
else
  echo
  echo "Something went wrong. What it said is above: a screenshot of this window helps whoever helps you."
  read -r -p "Press Return to close this window." _
  exit 1
fi
