# Storage, persistence and distribution

This page answers four questions. Where is my library saved? How are papers stored? How do PDFs get
in, including paywalled ones? How do people install and update research_brain? Each section says
what the app does today, and where it says "design only" the reason is given.

## The short version

- **One library per person, on their own computer.** research_brain runs on your laptop and serves
  `http://127.0.0.1:8765`. Nothing is uploaded. There is no server for the lab to run.
- **No accounts and no passwords.** Your computer login is your identity. Everything saves to disk as
  you work. UI state that matters is saved in the library too, not just in the browser.
- **A paper is a folder of plain files:** a Markdown note with the metadata, the PDF if there is one,
  and the extracted text. Duplicates are caught by DOI, arXiv id and the PDF's sha256.
- **Free PDFs download automatically.** Adding a paper by DOI, arXiv id or title starts a background
  search: arXiv first, then Semantic Scholar's open-access link, then Unpaywall (only if you've set a
  contact email). If nothing is found, the paper is kept with its metadata only.
- **Paywalled PDFs come through your browser.** On a PDF tab or a publisher page, the extension's
  **Save PDF to research_brain** downloads the file in your own browser session, with your
  subscription or university login, and posts the bytes to the local app. The app never sees or uses
  your credentials. It's one paper per click; there is no bulk download.
- **Updates never touch your data.** The app's code and your library live in different places.
- **Install with one command:** `uv tool install <release wheel or git URL>` (or pipx, or
  `./install.sh`). Then run `research-brain`.

## 1. Where your data lives

| System | Data folder |
| --- | --- |
| macOS | `~/Library/Application Support/research_brain/` |
| Linux | `$XDG_DATA_HOME/research_brain/` (default `~/.local/share/research_brain/`) |
| Windows | `%APPDATA%\research_brain\` |

Inside it:

```
settings.json               which profile is open, your Unpaywall email, custom profile folders
storage.log                 one line per move, migration, import, export and profile change
profiles/
  default/                  a library ("vault")
    notes/<id>.md           one Markdown file per paper, note or fragment (YAML frontmatter)
    pdfs/<id>.pdf           the paper's PDF, when there is one
    text/<id>.txt           text extracted from the PDF, with [[page N]] markers
    state/                  data you can't rebuild: meta.json (library format), review.json,
                            engagement.json, ui.json, acquire.json
    memory/  topics/        Research memory and your topics
    backups/                zips made automatically before a format upgrade
    .index/                 derived data only (search index, embeddings, PDF hashes). Safe to delete.
    .trash/                 deleted notes
  thesis/                   another profile, same layout
```

**Which library a run opens** (`app/paths.py`; the first match wins):

1. `--vault DIR` or `RB_VAULT`. This pins one folder, and profile switching is turned off.
2. `--profile NAME` or `RB_PROFILE`.
3. `active_profile` in `settings.json`, which the app's profile switcher writes.
4. `default`.

`RB_DATA_DIR` moves the whole data folder, for a portable install or for tests.
`research-brain where` prints the result of these rules.

**Updates never touch data.** The app is installed as a Python package (or a checkout). Your library
is in the data folder. Reinstalling, updating or deleting the app leaves it alone.

**Older libraries in `./vault`.** Before 0.6 the library lived in `vault/` inside the app folder. If
that folder has notes and the `default` profile has none, research_brain **keeps using it in place**.
It logs a notice in the terminal and on `/api/library`, and the app shows a hint once per session.
Moving it is a separate, safe step. Run `research-brain move-legacy-vault`, or click **Move it on next
start** in Library settings, which runs the move during the restart while no server is open. The move:

1. copies the folder to a temporary folder next to the destination,
2. checks the file count and the total size match,
3. renames the copy into place,
4. renames the old folder to `vault.moved-<date>` (it's kept, not deleted),
5. records `legacy_vault_moved` in `settings.json` and writes a line to `storage.log`.

It refuses to overwrite a default profile that already holds files.

## 2. How a paper is stored

A paper is **three plain files that share one id** (for example `attention-is-all-you-need-2017`):

- `notes/<id>.md`: title, authors, year, venue, DOI, arXiv id, Semantic Scholar id, URL and abstract
  in the YAML frontmatter, plus your summary, tags, links, reading status and free notes. This file is
  the source of truth. It opens in any editor and in Obsidian.
- `pdfs/<id>.pdf`: the original PDF, kept unchanged.
- `text/<id>.txt`: the extracted text, used by search, passages, critiques and Research.

**Linked papers: link + text, no PDF file (the default from a publisher page).** The extension's
**Save to research_brain (link + text)** stores the paper as `notes/<id>.md` with `source: linked` and `url:`
(the article's canonical address) in the frontmatter, plus `text/<id>.txt`: the article's full text as you see it
in your browser, with `[[section <heading>]]` markers for an HTML article (so citations read "§3.2") or
`[[page N]]` markers for a PDF read in the browser (the PDF bytes are read once and not kept). There is no
`pdfs/<id>.pdf`. Search, passages, similarity, the graph, citation links, critiques, chat and Research all use
the text, so a linked paper works like any other. Citations open the article itself, at the passage
(`#:~:text=`) or the page (`#page=N`).

Why: your browser has your institutional access, and that access belongs to you, not to the app. The
extension reads what you are looking at, in your session, one paper per click; the server never uses
your credentials, and keeping only text and a link keeps the library small (a few hundred KB per paper
instead of several MB) and avoids storing publishers' files you may not be allowed to keep.

**Upgrading to the PDF, one click.** In the app, **Get the PDF** on a linked paper first runs the
open-access search below; if there is no free copy it tells you to click **Save PDF too** in the
extension on the article page (`POST /api/import/pdf-bytes?note=<id>`). Uploading a file or a later
open-access hit works too. Once a PDF is attached the note says `source: pdf`, and its text is re-extracted
from the PDF with page markers. To save PDFs by default, set *Library settings › When saving from a
publisher page › Link + text + PDF* (`save_mode: "pdf"` in settings.json); with open-access downloads on,
linked papers then also get the free-copy search. With the default (`"link"`) nothing is downloaded.

**Metadata-only papers are normal.** A paper added by DOI with no free PDF is a full note: it has
citations, appears in the graph and can hold your notes. A PDF can be attached later, by dropping it
on the paper, through the extension, or through a later open-access hit.

**Duplicates.** Before creating a note, every import path checks:

- the DOI, the arXiv id and the Semantic Scholar id against existing notes;
- the page URL against existing papers (arXiv abs/pdf/html URLs, doi.org links, the exact URL);
- the **sha256 of the PDF bytes** against every stored PDF (`.index/pdf_hashes.json`, which re-hashes
  only files whose size or mtime changed).

A known paper that has no PDF gets the new PDF attached. A paper that already has a PDF keeps it:
nothing is replaced silently.

## 3. Getting PDFs "if there's access"

There are two routes. Neither ever uses your password, cookies or institutional login on the server.

### a) Open access, automatically (`app/acquire.py`)

After a paper is added by DOI, arXiv id, title or a Semantic Scholar id, a background thread looks for
a legal free copy. It never blocks the add, and at most two run at once. Candidates are tried in order:

1. **arXiv**: `https://arxiv.org/pdf/<id>`, when the paper has an arXiv id.
2. **Semantic Scholar** `openAccessPdf`, which comes with the same lookup at no extra cost.
3. **Unpaywall** by DOI: `best_oa_location`, then the other `oa_locations`. Unpaywall asks callers to
   identify themselves, so this step runs only after you enter a **contact email** in
   *Settings › Library settings* (or set `RB_UNPAYWALL_EMAIL`). The email goes only to Unpaywall.

Every download goes through `app/urlfetch.download_pdf`, the same SSRF-guarded fetcher the URL import
uses:

- http and https only;
- public addresses only, checked again on every redirect, with at most 3 redirects;
- a size cap (`RB_OA_MAX_MB`, default 60 MB) and a per-paper time budget (`RB_OA_DEADLINE`, 90 s);
- the body must start with `%PDF`.

The result is written to `state/acquire.json` with one of these states:

- `found` (with the source and sha256)
- `not-found` (with what was tried)
- `duplicate` (the same bytes are already another note's PDF)
- `has-pdf`

The open page refreshes when a PDF arrives. `GET /api/notes/{id}/acquire` shows the status, and
`POST` runs the search again. You can turn the feature off in settings, or with `RB_ACQUIRE=0`.

### b) Paywalled papers, through your browser (the extension)

Publishers allow subscribers to download PDFs in their browser. They don't allow a program to use
your login on a server, and storing your password would be a liability. So:

- On a publisher article page the popup's main button is **Save to research_brain (link + text)**, which
  saves no file at all (see section 2: `extension/extract.js` reads the page's citation metadata and main
  text in the tab, and the popup posts them as JSON to `POST /api/import/linked`).
- On a PDF tab, or a publisher page that names its PDF (`<meta name="citation_pdf_url">`), the
  popup also shows **Save PDF too** (**Save PDF to research_brain** on pages that aren't articles).
- Clicking it injects `extension/grab_pdf.js` into **that tab only** (activeTab + scripting; no new
  permissions). The script fetches that one URL as the page itself would, with
  `credentials: "include"`, so your browser applies whatever access you have: a subscription, campus
  IP, or a proxy/SSO login.
- It checks the response is a PDF (`%PDF`, at most 150 MB) and hands the bytes to the popup in 4 MB
  chunks. The popup posts them to `POST /api/import/pdf-bytes` on 127.0.0.1, with
  `Content-Type: application/pdf` and the page's URL, DOI and title.
- The server checks the type and size and sniffs `%PDF`. It dedupes by sha256, URL, DOI and arXiv id,
  attaches the PDF to an existing metadata-only paper or creates a new one, and opens it in the viewer.

The rules are user-initiated, one PDF per click, and no crawling, prefetching or bulk download.
`application/pdf` bodies can't be sent cross-site without a CORS preflight, and the origin guard
refuses everything except this app and the pinned extension id. The extension still has no AI and
talks only to 127.0.0.1 or localhost:8765.

If a publisher's page and its PDF are on different hosts, the browser may block the page-side
download (CORS). The popup then says to open the PDF in its own tab and click again, which always
works because the request is then same-origin.

## 4. Persistence and "logging in"

**Recommendation: no accounts and no passwords.** The reasons:

- There's no shared server to log in to. Each researcher's library is a folder on their own laptop,
  and the server only listens on 127.0.0.1.
- A password on a localhost app guards nothing the OS login doesn't already guard. Anyone who can use
  your computer account can read the files directly anyway. FileVault (disk encryption) plus the
  screen lock are the real protection. `app/security.py` sets the vault to mode 0700 and new files to
  0600.
- A login screen would add a forgotten-password failure mode and session expiry, and gain nothing in
  return.

**"Their stuff is saved"** therefore means:

- **Everything autosaves to disk.** Every edit is written to its Markdown file within about a second,
  using a temp file and an atomic rename. Review history, open counts, topics and memory are files
  in the vault.
- **UI state that matters lives in the library, not the browser.** `static/settings.js` mirrors these
  keys to `state/ui.json` (`PUT /api/ui-state`, every few seconds and when the tab is hidden): panel
  width, theme, similarity and citation toggles, threshold, graph filter and saved views, chat mode,
  recent commands, the tour flag, and the last open note. A new browser, a cleared cache, or a switch
  from Chrome to Safari gets them back. On the first load in a fresh browser it copies them in, reloads
  once, and reopens the last note. Purely cosmetic, per-device things stay in localStorage.
- **Profiles** cover shared machines and separate projects. A profile is just another vault folder.
  Switch or create one in *Settings › Library settings* (or run `research-brain profiles ...`). Under
  `research-brain serve` (which `run.sh` uses), the server restarts into the chosen profile in about a
  second. Under a bare `uvicorn` command, the switch applies on the next start. The open profile's
  name shows next to the logo when it isn't `default`, and in the tab title.

**Alternatives considered:**

- *Per-user passwords in the app.* No security gain on a single-user laptop, as above. Rejected.
- *A central lab server with accounts.* It would make sharing easy, but paper storage would then
  become a copyright and IT problem, the PDFs would be on someone else's machine, and the server
  would need backups, TLS and admin time. This is the right design only if the lab wants a shared
  library. That's a different product. The zip export/import below covers "send me your library".
- *Sync through a cloud folder (iCloud or Dropbox).* Point a profile at a synced folder with
  `research-brain profiles create shared --path ~/Dropbox/rb` and it works for **one person on
  several machines**. Don't open the same library on two machines at once, because the files would
  conflict. This isn't the default.

## 5. Library format version and migrations (`app/migrate.py`)

`state/meta.json` records the library's format version (`schema`), the app version that last opened
it, and a history of migrations. On every start, before anything reads the vault:

- a new, empty vault gets the current version;
- a vault with notes but no `meta.json` is version 0;
- an older vault is upgraded one step at a time. **A zip backup is made first** into `backups/`. It
  holds everything a migration can touch: all of the vault except `pdfs/`, which no migration rewrites,
  and derived folders. `meta.json` is written after each step, so an interrupted upgrade resumes;
- a vault from a **newer** app stops the server with a clear message. An old app never rewrites a
  library that a newer one has upgraded;
- a file lock stops two processes from migrating at once.

Migration 1 moves `review.json` and `engagement.json` from `.index/` (derived) to `state/` (yours).
New migrations are added to `MIGRATIONS` and must be idempotent.

## 6. Backups and moving to a new laptop

- **Whole-library zip.** Use *Settings › Library settings › Export library (.zip)*,
  `GET /api/vault/export`, or `research-brain export lib.zip`. The zip holds notes, PDFs (optional),
  text, state, memory, topics and a manifest (app version, format version, counts), and leaves out
  derived folders. On the new laptop, run **Import a library zip** (or `research-brain import lib.zip
  --profile laptop`). It always creates a **new profile**, so nothing is overwritten. The import is
  checked entry by entry before anything is written:
  - no absolute paths, drive letters, backslashes, `..` or symlinks (zip-slip);
  - every resolved path must stay inside the target folder;
  - only the known top-level folders are extracted;
  - entry count, file size, total size and compression ratio are capped (zip bombs).

  Then the library is migrated if needed, and you can switch to it.
- **Nightly snapshots.** `scripts/vault_backup.sh` commits the open library's notes and state to a
  git repo inside the vault (PDFs, derived data and `backups/` are left out). The macOS login agent
  from `scripts/install_launchd.sh` runs it at 02:30. Both scripts use the same resolution rules as
  the app.
- **Time Machine** already covers `~/Library/Application Support`.

## 7. Distribution

**Package.** `pyproject.toml` builds a normal wheel named `research-brain`. The `app/` source folder
is installed as the `research_brain` package, with `static/`, `extension/` and `scripts/` inside it.
It installs the `research-brain` command:

```
research-brain                    start the app and open the browser (same as `serve`)
research-brain serve --port 8765 [--profile NAME | --vault DIR] [--no-open] [--host H]
research-brain --version
research-brain where              data folder, library, extension folder, settings file
research-brain profiles [list | create NAME [--path DIR] | use NAME]
research-brain export FILE.zip [--no-pdfs]
research-brain import FILE.zip --profile NAME
research-brain move-legacy-vault
```

`serve` supervises the server process. When the app asks for a restart (a profile switch, or moving
the old library) it starts the server again with the new settings. `run.sh` keeps working: it builds
its `.venv` and then runs `python -m app.cli serve`.

**Install and update** (recommended order):

1. `uv tool install <wheel URL>` or `uv tool install git+https://<lab git host>/research_brain@vX.Y.Z`.
   Update with `uv tool upgrade research-brain`, or install the newer tag. uv brings its own Python,
   so nobody has to fight Homebrew. (Publishing to PyPI isn't planned, because the tool is for the
   lab.)
2. `pipx install <wheel>`, then `pipx upgrade research-brain`.
3. The one-line installer, `curl -fsSL <release URL>/install.sh | RB_SOURCE=<wheel URL> bash`. It
   uses uv, then pipx, then falls back to a private venv in the data folder with a link in
   `~/.local/bin`. Running it again updates. `RB_EMBEDDINGS=1` adds sentence-transformers.
4. From a checkout: `./run.sh`, as before.

**Releases.** Tag `vX.Y.Z`. `.github/workflows/release.yml` runs the tests on every OS, builds and
smoke-tests the wheel, and publishes it with the installers, `constraints.txt`, the extension zip and
`latest.json` as a GitHub release. The lab copies those files into a shared update folder; installs from
there remember it, show an **Update** chip when a newer version lands, and update with `research-brain
update`. See [releasing.md](releasing.md).

**Browser extension.**

- *Unpacked* (today): `chrome://extensions` › Developer mode › Load unpacked. Pick `extension/`, or
  the folder that `research-brain where` prints, copied somewhere stable first. Updates are manual: copy
  the new folder over the old one and click reload.
- *Chrome Web Store, unlisted* (recommended once more than a handful of people use it): it updates
  automatically, and the id is stable, which `app/security.py` pins. *Design only:* it needs a
  developer account and a review, and that is the lab's decision.
- *Version sync:* the extension's `manifest.json` version is released together with the app. The
  extension only uses endpoints the app keeps stable (`/api/status`, `/api/match`, `/api/fragments`,
  `/api/clip`, `/api/search`, `/api/import/url`, `/api/import/pdf-bytes`). A newer app keeps accepting
  older extensions. *Design only:* a version check in the popup, which would compare
  `/api/library.version` with a minimum.

**Claude CLI.** The AI features (summaries, critiques, explanations, Ask your library, Research) run
the `claude` command-line tool, logged in with the user's own account. `research-brain` prints a note
when the tool is missing. **Everything else works without it:**

- the library, PDFs and open-access download;
- the viewer, highlights and the extension;
- search (keyword and local embeddings), the graph, citations from Semantic Scholar;
- reading review, topics, Zotero import, export, profiles and backups.

`RB_CLAUDE_BIN` points at a non-standard install.

## What's implemented where

| Piece | File |
| --- | --- |
| Data folder, profile resolution, settings file, legacy `./vault`, safe move, storage log | `app/paths.py` |
| Profiles: list, create, select | `app/profiles.py` |
| Format version and migrations with backup | `app/migrate.py` |
| Open-access PDF search and download, sha256 index | `app/acquire.py` |
| Whole-library zip export and import | `app/vaultzip.py` |
| HTTP endpoints for all of the above, PDF bytes from the extension | `app/storage_api.py` |
| `research-brain` command and restart supervisor | `app/cli.py`, `pyproject.toml`, `install.sh` |
| Library settings dialog, profile label, UI state sync | `static/settings.js` |
| "Save PDF to research_brain" | `extension/clip.js`, `extension/clip.html`, `extension/grab_pdf.js` |
| Linked papers: `POST /api/import/linked`, `?keep_pdf=0` / `?note=` on PDF bytes, save mode | `app/storage_api.py` |
| "Save to research_brain (link + text)": metadata and article text from the page | `extension/extract.js`, `extension/clip.js` |
| Section markers → passages with section labels; citations into the article | `app/chunks.py`, `app/server.py`, `static/app.js` |
| Tests | `tests/test_storage.py`, `tests/test_linked.py`, `tests/e2e/test_linked_e2e.py`, `tests/e2e/test_extension_e2e.py` |
