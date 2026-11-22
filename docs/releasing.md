# Releasing and rolling out updates

Releases are versioned (`vX.Y.Z`, from `__version__` in `app/__init__.py`) and published by
`.github/workflows/release.yml`. People can install and update from a shared folder, so nobody needs
access to the private GitHub repository.

## Publish a release

1. Bump `__version__` in `app/__init__.py` (and `version` in `extension/manifest.json` if the extension
   changed). Use a new minor version when the library format changes (`SCHEMA_VERSION` in
   `app/migrate.py`).
2. Commit, then tag and push:

   ```bash
   git tag v0.8.0 && git push origin main v0.8.0
   ```

3. The workflow runs the full test suite on Linux, Windows and macOS, and drives the installed app on Windows
   (`tests/windows_installed.py`: installed by `install.ps1`, opened from its Start menu entry, under an ordinary user
   folder and one the code page can't spell) (pushes to a branch test on Linux only, to save
   Actions minutes: on a private repository Windows minutes count double and macOS ten times), checks the tag matches
   `__version__`, builds the wheel, installs it in a clean environment as a smoke test, and creates a
   GitHub release with:

   | File | What it is |
   | --- | --- |
   | `research_brain-X.Y.Z-py3-none-any.whl` | the app |
   | `latest.json` | the version, the wheel's file name and the notes; what `research-brain update` reads |
   | `install.sh`, `install.ps1` | the installers (they install the wheel `latest.json` names) |
   | `Install-research-brain.command`, `Install-research-brain.cmd` | double-click installers for macOS and Windows: they run the installer above, then open the app |
   | `constraints.txt` | the exact dependency versions the release was tested with |
   | `research-brain-extension-X.Y.Z.zip` | the browser extension |
   | `research_brain-X.Y.Z.tar.gz` | the source archive |

   The notes are the commit subjects since the previous tag, so write commit subjects for people.

## Roll it out

Upload `research-brain-installer-X.Y.Z.zip` to a shared folder (OneDrive, SharePoint, Google Drive, Box:
in the browser is fine) and tell people. It holds the installers (the Mac one with its executable bit kept: single
files downloaded in a browser lose it), the wheel, `latest.json`, `constraints.txt`, the extension zip, README.md and
`READ ME FIRST.txt`. People download it, unzip it and run the installer; running a newer one later updates them in
place. To go back, they run an older installer.

```bash
gh release download vX.Y.Z --pattern "research-brain-installer-*"
```

The installers copy the wheel to a temporary folder before installing, because shared-drive paths have spaces
("My Drive", "OneDrive - University") that uv would otherwise split.

**Library format changes.** A release that migrates the library is one-way: the old version refuses to
open a migrated library rather than damage it (a zip backup is made before every migration, in the
profile's `backups/`). Say so in the notes, and don't roll back past it.

## Updates

Once installed, people don't need a new zip. The app reads the latest release's `latest.json` from GitHub (the
repository is public, so no account is needed) at most once a day, and when there's a newer version an
**Update** button appears in the top bar. **Update now** downloads the wheel and `constraints.txt` into the
data folder's `update/`, starts a small script on its own and quits; the script waits for research brain to
stop, reinstalls with whatever installed it, and starts it again. The window stays open, says what's happening
and reloads on the new version, which says "Updated to X" (or shows what went wrong, with the end of
`update/update.log`). Settings › About shows the version, Check now and the same button. Details:
`app/updates.py`, `app/updates_api.py`, `static/updates.js`. So publishing a release is all it takes:
the zip is only for first installs.

The browser extension isn't updated this way (it's loaded unpacked): when it changes, say so in the notes.

For admins, `research-brain update` installs the newest release from GitHub (it needs a `GITHUB_TOKEN`
or the `gh` command signed in while the repository is private), or from a folder of release files with
`research-brain update --source <folder>`; `--to X.Y.Z` installs a specific version.

## Installers

`install.sh` / `install.ps1` get uv if it's missing (`RB_NO_UV=1` skips that), then install with
`uv tool install --python 3.12`: 3.12 is what the pins in `constraints.txt` are built and tested for, and
uv downloads it if the computer doesn't have it. Without uv they fall back to pipx, then to a private
virtualenv, and only apply the pins on Python 3.12 or newer, since older Pythons have no wheels for them
(the first Python 3.10 on the PATH would otherwise mean compiling numpy from source).
`research-brain update` reinstalls with whichever of these installed it; on Windows it finishes in a new
window, because the running `research-brain.exe` can't replace itself.

## Signing (not done)

There are no native app bundles, so there's nothing to sign or notarize: people run an installer script
and a Python command. A double-click app (PyApp or Briefcase) would need an Apple Developer ID and a
Windows code-signing service to avoid Gatekeeper and SmartScreen warnings. Worth it only for people who
won't use a terminal.
