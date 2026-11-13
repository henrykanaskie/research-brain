# Releasing and rolling out updates

Releases are versioned (`vX.Y.Z`, from `__version__` in `app/__init__.py`) and published by
`.github/workflows/release.yml`. Lab members install and update from a shared folder, so nobody needs
access to the private GitHub repository.

## Publish a release

1. Bump `__version__` in `app/__init__.py` (and `version` in `extension/manifest.json` if the extension
   changed). Use a new minor version when the library format changes (`SCHEMA_VERSION` in
   `app/migrate.py`).
2. Commit, then tag and push:

   ```bash
   git tag v0.8.0 && git push origin main v0.8.0
   ```

3. The workflow runs the full test suite on Linux, Windows and macOS (pushes to a branch test on Linux only, to save
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

## Roll it out to the lab

Copy the release's files into the lab's update folder: anything synced to everyone's disk (OneDrive, Box,
Google Drive for desktop) or a network share. Overwrite `latest.json`, `install.*` and `constraints.txt`;
keep older wheels so anyone can go back (`research-brain update --to 0.7.0`).

```bash
gh release download v0.8.0 --dir "/path/to/Lab Drive/research-brain" --clobber
```

Within a day, everyone sees an **Update 0.8.0** chip; they quit the app and run `research-brain update`.
To roll back, put the previous `latest.json` back (or tell people to run `update --to`).

**Library format changes.** A release that migrates the library is one-way: the old version refuses to
open a migrated library rather than damage it (a zip backup is made before every migration, in the
profile's `backups/`). Say so in the notes, and don't roll back past it.

## Where the app looks for updates

`RB_UPDATE_SOURCE`, else the folder saved in **Library settings › Updates** (`research-brain update --source
<folder>`, set automatically when someone installs from the folder), else the repository's latest GitHub
release, which needs a `GITHUB_TOKEN` or the `gh` command signed in. It checks at most once a day,
reads only `latest.json`, and never installs anything by itself. `RB_UPDATE_CHECK=0` turns the check off.

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
