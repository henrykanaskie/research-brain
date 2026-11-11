# research_brain

A local knowledge graph for papers and notes. Drop in a PDF, write a 2–3 sentence summary in your own
words, and have your local Claude critique it against the paper. Notes connect automatically by meaning,
Claude explains *why* two things are connected, Semantic Scholar shows a paper's references and citations,
and you can ask questions of your whole library.

Everything runs on your computer. The AI part uses whichever AI tool you already have on this computer:
Claude Code, Codex CLI, Gemini CLI, GitHub Copilot CLI, OpenCode, Cursor's agent, or a local model server
(Ollama, LM Studio). It never needs an API key, and research_brain itself pays for nothing. Notes are plain
Markdown files, so your library folder also opens as an Obsidian vault.

## Install and start it

```bash
./install.sh        # from a checkout or release: installs the `research-brain` command (uv, pipx or a venv)
research-brain      # starts the app and opens the browser; Ctrl+C stops it
```

Or, without installing, from a checkout:

```bash
cd ~/coding_projects/research_brain
./run.sh            # first run builds .venv and installs deps (a few minutes), then opens the browser
```

You can also double-click `research_brain.command` in Finder. `research-brain --version` prints the
version; `research-brain where` shows where your library is. Updating the app never touches your data.

**Your data** lives in a per-user folder, separate from the app: `~/Library/Application Support/research_brain/`
on macOS (`~/.local/share/research_brain/` on Linux, `%APPDATA%\research_brain\` on Windows). Each
library ("profile") is a folder under `profiles/`. There are no accounts or passwords: your computer
login is your identity and everything autosaves. An older `./vault` with notes keeps working where it
is; **View → Library settings** offers to move it. Details, and how PDFs are obtained:
[docs/storage-and-distribution.md](docs/storage-and-distribution.md).

**Requirements:** Python 3.10+, plus one AI tool installed and signed in (see "Choosing an AI agent"
below). Everything except critiques, explanations and questions works without one. For Claude Code, the
app finds `claude` on your PATH, `~/.local/bin`, Homebrew, or nvm; if it lives somewhere else, set
`export RB_CLAUDE_BIN=/path/to/claude`.

## Choosing an AI agent

**View → Library settings → AI agent** (or the command palette: "Choose AI agent") lists every AI tool
research_brain knows, whether it is installed and signed in, and how to install the ones that aren't.
Pick one; the top bar then says "<agent> ready". Without a choice, the first one found is used, in this
order: Claude Code, Codex CLI, Gemini CLI, GitHub Copilot CLI, OpenCode, Cursor agent, Ollama, LM Studio.

research_brain never asks for an API key. It runs the tool you already use, signed in with your own
account, so what it costs is whatever that tool costs you. Roughly (check each provider's current terms;
plans and free tiers change):

| Agent | What it uses |
| --- | --- |
| Claude Code | your Claude plan |
| Codex CLI | your ChatGPT plan |
| Gemini CLI | your Google account (it has had a free tier for personal accounts) |
| GitHub Copilot CLI | your GitHub Copilot plan (GitHub has offered a limited free plan) |
| OpenCode, Cursor agent | the account or plan you signed them in with |
| Ollama, LM Studio (or any OpenAI-compatible server on this computer) | free and offline; needs a capable machine, and answers are weaker than the big hosted agents |

For a local server, the settings let you pick the model (default: the first one installed) and change
its address. Only addresses on this computer are accepted (`127.0.0.1`, `localhost`, `::1`).

Research mode works with every agent. Claude Code keeps its own faster path; with the others the app runs
the research loop itself: the model asks for a search or a page in a tiny JSON format, research_brain
looks it up and sends back what it found, and the model never gets tools.

## What you can do

| Thing | How |
| --- | --- |
| Add a paper | Drag a PDF anywhere onto the window, or **Add PDF**. The app finds the DOI or arXiv id in the PDF (its XMP metadata first, then the first pages) and fills in the citation details from Crossref, arXiv & co. (see "How metadata is found" below). |
| Add without a PDF | **Add by DOI / arXiv / title** (⌘⇧A, or ⌘K and type "add paper"). Accepts `10.xxxx/...`, `2301.01234`, arXiv or doi.org URLs, a PMID, an ISBN (books), an OSTI or NTRS link, or a title. |
| Summaries | Write the summary yourself, then **Critique my summary**. Claude reads the full PDF text and reports errors, what's missing, what's vague, and questions to check yourself. It does not rewrite the summary for you. |
| Review | **Review** in the top bar (it shows how many are due, and hides when nothing is) brings back your own summaries and fragment notes for recall: write what you remember, **Reveal** your original side by side, then grade yourself. Claude can optionally **Check my recall** in at most three lines. Details below. |
| Graph views | **Filter** at the top left of the graph fades everything except a type, year range, tag or region. Save a filter plus the current search as a named view. Double-click a node (or select it and press **F**) to show only its 2-hop neighbourhood; **Esc** or **Show all** brings everything back. |
| Notes on a specific part | **Fragments**: a quote, equation or passage plus your note about it. Each fragment is its own small node orbiting its paper, and it links by meaning to other papers. Add one from the note panel, or highlight text in the built-in PDF viewer (**Open in viewer**), or clip from any web page with the Chrome extension. |
| Connections | Dashed lines are semantic similarity (local embeddings; tune with the slider). Solid lines are links you made. Click any line, or **Why?** in the related list, to have Claude explain the relationship. |
| Find more papers | **Discover papers** on a paper shows its references, citing papers, and recommendations. **Add** imports one and links it. |
| Ask your library | **Ask your library**: Claude reads the most relevant passages of your papers (page by page) and your own notes, and cites them as chips like *Paper title p. 9*. Clicking a page citation opens the PDF at that page in a new tab. The passages it read are listed under each answer. |
| Research mode | In **Ask your library**, switch **Quick** to **Research** (or **Ask with research** in the command palette). Claude then searches your library itself: several searches at once, opening the pages around a hit, following citations, and consulting memory. You see each step as it happens ("Searching: …", "Reading pp. 4–5 of …"), then the answer with `[[note-id]] p. N` citations. The steps fold away when it's done. Most questions take 3–12 s. **Faster model** uses `haiku` for that question. |
| Memory | **Memory** (chat header, or G then M) shows the library map Research reads first (regions, key notes, recent notes, reading queue, where the PDFs are; generated by the app) and the short findings Claude saved while researching. Findings are labelled *Added via AI*, list their sources, and can be edited or deleted. Files: `vault/memory/`. |
| Citation links | Papers that cite each other are joined by thin ink lines with an arrowhead at the cited paper (from Semantic Scholar references; only papers already in your library). New imports are linked automatically in the background; for papers you already have, run the backfill below once. Toggle them under **View → Show citation links**. |

| Idea cards | **Ideas** on a paper: its key claims, equations, measured quantities (value ± uncertainty, unit, conditions) and methods as small cards. **Suggest ideas** has the AI propose them, each with a quote that must be found word for word in the paper; you **Confirm**, **Edit** or **Reject** each (J/K, Y, E, X). The same idea in other papers is linked, and a value that disagrees is flagged. **G then I** opens every card as a table you can sort, filter and export as CSV. See *Idea cards* below. |
| Cite and export | **Alt+C** (or **Cite** on a paper): copy `\cite{key}`, a formatted citation or a bibliography in APS, IEEE, APA and any CSL style; export BibTeX/BibLaTeX/RIS/CSL-JSON with stable keys; keep a `.bib` file updated automatically. See *Citing and bibliographies* below. |

Math works everywhere: `$...$` inline and `$$...$$` display (KaTeX).

## Keyboard shortcuts and help

Press **?** for every shortcut, grouped, with a search field ("highlight" finds the viewer's
"Add note from selection"; "g" lists the G-chords). **⌘K** (Ctrl+K) is the command palette: type
any action and press Enter; your recent actions come first and each row shows its shortcut. The
palette also has **Help** (a one-page guide) and **Help: quick tour** (five coach marks, shown once on
first run). The ones you'll use most: **/** search, **N** new note, **⌘⇧A** add a paper by id,
**⌘O** import a PDF, **A** ask your library, **G then R** review, **G then T** topics, **G then I** ideas, **S** and **B**
write the summary or notes, **C** critique, **Alt+C** cite papers, **Shift+L** link to…, **O** open the PDF, **F** focus,
**⌘[ / ⌘]** back and forward, **Esc** closes the innermost thing. In the viewer: **j/k** pages,
**N** add a note from the selection, **[ / ]** fragments, **G then G** back to the graph.

## What the AI can and can't do

Whichever agent you pick, it can only read the text research_brain sends it. It gets no tools: it can't
browse, click, open files, run code or reach the internet. Agent CLIs run from an empty private folder,
with their tools, extensions and project instructions switched off wherever the CLI allows it, and are
stopped if they try to use a tool anyway. For Claude, every call runs your `claude` CLI with no built-in
tools, no MCP servers from your settings, and Claude in Chrome off. Research mode gets exactly one MCP server, research_brain's own (search, read pages, citations,
memory; read-only except for appending a short sourced finding). Before Claude does anything,
research_brain checks the list of tools the session reports and kills the run if anything else is there.
Private notes are never sent at all (see [Private notes](#private-notes-your-own-ideas-stay-on-your-computer)).
Details: [SECURITY.md](SECURITY.md).

## Chrome extension (web clipping)

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick the `extension/` folder.
3. On any page (including arXiv, journal sites, and Chrome's PDF viewer), select text, then right-click and choose **Add to research_brain…**, or use the keyboard shortcut listed in `extension/README.md`.
4. **Save to research_brain (link + text)** (popup, on a publisher article page, an arXiv page or a PDF tab): saves the paper as a *linked paper*: its link, its full text and its citation details (title, authors, DOI, journal, year, abstract), read from the page you are looking at in your own browser session. No PDF file is kept. **Save PDF too** (the second button) also downloads the PDF in your browser, with your own access to the site (subscription, university login). research_brain never sees your login and never downloads from a paywalled site itself. One paper per click.
5. To annotate a page in place, click the toolbar icon and choose **Annotate this page** (or press Alt+Shift+A): select text, then **Highlight** or **Highlight + note**. Highlights reappear when you come back. On a PDF tab the popup offers **Annotate this PDF in research_brain**, which imports it and opens the built-in viewer. The extension has no AI and talks only to research_brain on this computer; see `extension/README.md`.

arXiv and DOI pages get imported as papers automatically. Other pages become a source note.

### Saving papers: link + text, or the PDF too

By default a paper saved from a publisher page is **link + text**: research_brain keeps the article's URL, its full
text (`vault/text/<id>.txt`) and its metadata, not the PDF file. Search, similar papers, the graph, citation links
and the AI features (chat, Research, critiques) all work from the text, so nothing is lost. Linked papers look like
any other paper; the panel shows a small link icon, **Open article** (the publisher's page, in your browser) and
**Get the PDF**, which first looks for a free legal copy (arXiv, Semantic Scholar, Unpaywall) and otherwise asks you
to click **Save PDF too** in the extension on the article page. Attaching a PDF (that way, by upload, or by the
open-access search) turns the paper into a regular PDF paper; its text is re-read from the PDF, page by page.

Citations into a linked article point to a **section** (`[[id]] §3.2`, or the heading for unnumbered sections)
and open the article at that passage (a `#:~:text=` link); a PDF read in the browser is cited by page and opens at
`#page=N`. To save PDFs by default instead, choose **Library settings → When saving from a publisher page → Link +
text + PDF**; the extension's main button then saves both.

## How metadata is found

Citation details come from **identifiers first, scraping second** (`app/metadata.py`). Whenever a paper is added
(PDF, DOI/arXiv/title, the browser extension, a URL, Zotero), the app looks up its authoritative record in the
background and the panel updates by itself (it waits up to 3 s, so usually the first view is already right):

| You have | Asked | Gives |
| --- | --- | --- |
| a DOI | Crossref (`api.crossref.org`); a DOI Crossref doesn't know (DataCite: Zenodo, theses in repositories, datasets) by DOI content negotiation on `doi.org` | journal / conference / book / report records: authors with particles and suffixes, container title and its abbreviation (*Phys. Plasmas*), volume, issue, pages or article number (APS `065001`), ISSN, publisher, date |
| an arXiv id | the arXiv API | the preprint; if arXiv knows the journal DOI, the published version's Crossref record wins and the arXiv id is kept |
| a PMID / PMCID | NCBI E-utilities | PubMed's record (then Crossref, when it names a DOI) |
| an ISBN | Open Library | books |
| an OSTI or NASA NTRS link | the OSTI / NTRS APIs | DOE and NASA reports, conference papers |
| only a title | a Crossref title search, accepted only for a near-exact title match | |
| nothing else | Semantic Scholar, then what the page or PDF said | |

The record is stored in the note as CSL-JSON (`csl`, used for citations and exports) with `meta_source` and
`meta_checked`; title, authors, year, venue, DOI and abstract follow it. Titles keep the publisher's
capitalisation; HTML and MathML are stripped, TeX math (`$...$`) is kept. A better source replaces a worse one,
never the other way round; what you edit in the panel is kept (and a later lookup won't undo it); **papers from
Zotero keep Zotero's metadata**, only blanks are filled in. **Refresh metadata** (panel, or ⌘K) looks a paper up
again. Answers are cached for 30 days in `vault/.index/metadata_cache.json`, and requests are spaced out per
service. Only identifiers (and a title, for the title search) are sent; setting the contact email in Library
settings adds it to the requests (Crossref's "polite pool"). `RB_METADATA=0` turns lookups off.

**Optional: Zotero's translation-server.** For pages that carry no identifier at all, the app can ask Zotero's
own site translators, if you run the translation-server on this computer:

```bash
docker run -d -p 127.0.0.1:1969:1969 --rm --name translation-server zotero/translation-server
```

It is used only when it answers on `127.0.0.1:1969` (`RB_TRANSLATION_SERVER_PORT` to change the port, `0` to never
ask); the app sends it the page URL, and it fetches the page itself. Stop it with `docker kill translation-server`.

## Import from Zotero

**Add → Import from Zotero** brings in your local Zotero library: papers, their child notes, and the
highlights you made in Zotero's PDF reader.

1. If you're in Zotero groups, pick the library first (**My Library** or a group). Then pick **Entire library** or a collection. Sub-collections are included, and the counts show how many papers each one holds.
2. Leave **Copy PDFs** and **Import highlights as fragments** on (both default), then click **Import**.
3. You can close the dialog while it runs. When it finishes it shows a summary and the graph refreshes.

| From Zotero | Becomes |
| --- | --- |
| Regular item (article, preprint, thesis…) | A paper: title, authors in order, year, DOI, arXiv id (from the URL, Extra or a `10.48550/arXiv.*` DOI), URL, abstract, venue, tags |
| Child notes | The paper's notes (HTML converted to Markdown) |
| PDF attachment | Copied into `vault/pdfs/`, with text extracted for Claude |
| Highlights and comments | Fragments under the paper: the highlighted text is the quote, your comment is the note, and the page is filled in (`p. <page label>`) |

- **Zotero is never modified.** The app copies `zotero.sqlite` (and its `-wal` file) to a temp folder and reads the copy, so Zotero can stay open.
- **Safe to run again.** Each note remembers its Zotero key (`zotero_key`). Papers you already have, matched by DOI, arXiv id or exact title, are not duplicated. They get a PDF if they had none, highlights they don't have yet (matched by quoted text), and any blank metadata filled in. Your own summary and notes are never overwritten.
- **Highlights stay in sync.** On a re-import, a highlight whose text, comment or page you changed in Zotero updates its fragment, and a highlight you deleted in Zotero moves its fragment to `vault/.trash/`. If you've edited a fragment in research_brain since it was imported, it is never changed or removed; the summary lists it as "kept (edited)".
- It doesn't call Semantic Scholar, so a few hundred papers import in seconds. Items in Zotero's trash and standalone notes are skipped.
- The library is found at `~/Zotero`, or at a custom data directory set in Zotero's preferences. If yours lives somewhere else, start the app with `export RB_ZOTERO_DIR=/path/to/Zotero`.

## Where things live

Inside your library folder (`research-brain where` prints it; below it's called `vault/`):

```
vault/notes/*.md   your notes (YAML frontmatter + Markdown body); plain files, editable in any editor
vault/pdfs/        imported PDFs           vault/text/   extracted text (for Claude)
vault/state/       your Review schedule and history (review.json), open counts (engagement.json),
                   UI state (ui.json), open-access results (acquire.json), library format (meta.json),
                   privacy settings (privacy.json) and the log of what was sent to AI (ai_log.jsonl),
                   evidence you triaged for your open questions (questions/<id>.json);
                   included in the nightly backup and the zip export
vault/.index/      embeddings, chat passage index, search index (meta.sqlite), cached explanations
                   (derived only; safe to delete, rebuilt automatically)
vault/topics/*.md  your topics: name, description, ordered members, dismissed and snoozed recommendations
vault/memory/      Research memory: core.md (library map, generated), findings.md (Added via AI, editable)
vault/cards/       idea cards, one JSON file per paper (claims, equations, quantities, methods; your review)
vault/.trash/      deleted notes (nothing is permanently deleted by the app)
vault/exports/     read-only HTML sites made with View → Export
vault/backups/     automatic zips made before a library format upgrade
```

Next to the profiles: `settings.json` (open profile, Unpaywall email) and `storage.log` (moves,
migrations, imports, exports).

## Private notes (your own ideas stay on your computer)

Open questions and standalone notes are **private** by default; papers, fragments and summaries are
shareable. Private items are never sent to an AI agent (chat, critique, Research, review checks) and are left
out of reading-list exports; search, similarity and the graph still work on them, because those run locally.
Click the lock in a note's header to switch one item, or change the defaults in **Library settings ›
Privacy**, which also shows *What was sent to AI*: a log of every request (agent, model, purpose, item ids,
size; never the content). A local model server can be allowed to read private items (off by default). The
library zip is a full backup and includes private items. Details: [SECURITY.md](SECURITY.md#privacy-of-your-own-ideas-appprivacypy).

## Idea cards

A second brain works at the level of ideas, not files. Each paper's **Ideas** section (under Fragments) holds
small cards: a **claim** the paper makes, a key **equation** (LaTeX), a **measured quantity** (name, symbol,
value ± uncertainty, unit, conditions) or a **method** (Langmuir probe, OES, collisional-radiative model, PIC…).

- **Suggest ideas** sends the paper's own text (abstract, results and conclusions first; never your notes,
  summaries or questions, and never a private paper) to your AI agent, part by part, and asks for at most 25
  cards per paper as JSON. Every card must come with a quote copied from the paper; the app looks the quote up
  in the text (ignoring line breaks, hyphenation, ligatures and punctuation) and **drops any card whose quote
  isn't there**, or whose number isn't in its quote. The stored quote is the paper's own words with its page
  (PDF) or section (linked article); click it to open the viewer or the article there. Duplicates are skipped.
  It works with every agent; with none, add cards by hand.
- **Machine-suggested until you confirm.** Suggested cards are quiet (dashed blue-grey rule). **Confirm** (Y),
  **Edit** (E) or **Reject** (X); J and K move between cards; **Confirm all** takes them all. Confirmed and edited
  cards are your knowledge (red rule), and only they are used by chat and Research (`cards_search`). **Add card**
  writes your own: it is private if your standalone notes are (Library settings › Privacy).
- **Units.** Values are converted to SI with pint for comparing (temperatures in eV become kelvin, `cm-3`
  becomes m⁻³, mTorr, sccm and gauss are understood); the original value and unit are kept.
- **Across papers.** Cards that say the same thing in different papers are linked (local embeddings, same kind;
  quantities by name or symbol and compatible units). A quantity that disagrees with another paper's under
  comparable conditions (beyond the combined uncertainty, or by more than 30% when neither gives one) shows
  **Disagrees with <paper> (p. N)**. Conditions with a clearly different operating point (e.g. 300 V vs 600 V)
  aren't compared.
- **Ideas view** (**G then I**, or *Open ideas*): every card in the library. Quantities are a table (value, unit,
  SI value, conditions, paper key, page, status, conflicts): click a header to sort, filter by text (e.g. "Hall
  thruster plume") or by quantity ("electron temperature", "T_e"), and **Export CSV**.
- Files: `vault/cards/<paper-id>.json`, plain JSON you can diff; they are in the backup and the library zip.
  The links are derived (`vault/.index/card_links.json`). Deleting a paper moves its cards to `.trash/`.

## Open questions

A second brain holds your open questions and keeps gathering evidence for them. **Add › New question** (or **Q**)
and write it down, for example *Could the breathing-mode frequency be predicted from the Kr CRM outputs?*, with a
sentence or two on what you mean. From then on, whenever a paper, passage, note or idea card bears on it, the
question collects it, and shows **3 new since you last looked**.

- **Your answer.** Each question has its status (open, answered, parked) and *Your answer*: your current
  thinking, in Markdown with LaTeX and [[links]], like any note. A question is a note (`type: question` in
  `vault/notes/`), so it has backlinks, tags, topics, search and a place on the graph.
- **Triage.** Evidence is grouped as *To triage*, *Supports*, *Against*, *Related* and *Dismissed*, each with the
  passage, its source and a p. / § link that opens the viewer or the article there. Click a piece and press
  **S** (supports), **A** (against), **R** (related) or **D** (dismiss); ↑ ↓ move. Add a few words of your own on
  any piece. **Mark all seen** clears the new count; **Look again** searches right away.
- **Calm.** At most 10 new pieces per question per day, at most two from one paper at a time, never the same
  passage twice, and a dismissed passage (or a dismissed note, paper or card) never comes back. Parked and
  answered questions stop collecting. The **Questions** button in the top bar shows the total new count;
  **Shift+Q** lists your questions with their counts.
- **Local only.** Matching runs on this computer: the same hybrid passage search as chat (BM25 + the embedding
  model, or TF-IDF), whole-note similarity and the idea cards' local search, in a background thread a moment
  after something changes. Questions are private by default and never go to an AI agent; the optional
  *Summarise the evidence* says plainly that it is unavailable while the question is private, and even for a
  shareable question it leaves out evidence from your private notes. Questions themselves are never sources: they
  aren't in the passage index, chat or Research.
- **Sensitivity.** *Library settings › Open questions* sets the match threshold (default 0.12 for TF-IDF, 0.40 for
  sentence-transformers) and the daily cap. With TF-IDF a match must also share three of the question's words.
- Files: your triage decisions are in `vault/state/questions/<question-id>.json` (backed up with the library);
  deleting a question moves them to `.trash/`.

## Profiles, backups as a zip, and moving to a new laptop

- **View → Library settings** (or the command palette: *Library settings*, *Switch profile*): the open
  library and its folder, profiles (create, switch; the app restarts into the new one), the open-access
  settings, and **Export library (.zip)** / **Import a library zip**. An import always becomes a new
  profile, so nothing is overwritten.
- Command line: `research-brain profiles [create NAME | use NAME]`, `research-brain export lib.zip
  [--no-pdfs]`, `research-brain import lib.zip --profile laptop`, `research-brain serve --profile NAME`
  or `--vault DIR`.
- **Open-access PDFs:** adding a paper by DOI, arXiv id or title downloads a free legal copy in the
  background when there is one (arXiv, Semantic Scholar, and Unpaywall once you enter a contact email in
  Library settings). Otherwise the paper is kept with its metadata, and you can attach a PDF later.

## Works offline

Everything the interface needs ships in `static/vendor/`: KaTeX 0.16.11 (woff2 fonts), marked 12.0.2,
force-graph 1.52.0, PDF.js 4.10.38 (with its CMaps and standard fonts), and the Instrument Sans and
STIX Two Text fonts. The app makes no CDN or font requests, so the graph, editor, math and PDF viewer
all work on a plane. Only the things that are online by nature need a connection: Semantic Scholar
lookups (Add by DOI, Find related papers) and Claude. `tests/test_offline.py` fails if a page ever
loads a script or stylesheet from the internet again.

## Backups and starting at login (macOS)

`scripts/vault_backup.sh` snapshots your notes into a git repository inside the vault (by default the
library the app opens; pass a folder to choose another). The first run
`git init`s `vault/` with a `.gitignore` for `pdfs/`, `.index/`, `.trash/`, `exports/` and `backups/`, then each run
commits whatever changed as `vault snapshot <date>` (author `research_brain <research_brain@localhost>`;
your own git identity is never used). PDFs are left out to keep the repo small, so back up `vault/pdfs/`
some other way (Time Machine, iCloud). Nothing is pushed; add a private remote yourself if you want an
off-machine copy.

```bash
scripts/vault_backup.sh                 # snapshot now
git -C vault log --oneline              # history;  git -C vault show HEAD~3:notes/<id>.md  to see an old version
```

To have the server start at login and the backup run every night, install the launchd agents once:

```bash
scripts/install_launchd.sh              # installs both; --no-server or --no-backup for just one
scripts/install_launchd.sh --uninstall  # stop and remove both
```

| Agent | What it does | Log |
| --- | --- | --- |
| `com.research_brain.server` | Runs `./run.sh` at login and restarts it if it crashes. Doesn't open a browser tab; visit http://127.0.0.1:8765. | `~/Library/Logs/research_brain.log` |
| `com.research_brain.backup` | Runs `vault_backup.sh` at 02:30 (or when the Mac next wakes). | `~/Library/Logs/research_brain-backup.log` |

The installer fills in this folder's absolute path, so re-run it if you move the folder. Run the
first backup immediately with `launchctl kickstart gui/$UID/com.research_brain.backup`. If you start
the app with `./run.sh` while the login agent is already running, it just opens the existing app.

## Review (active recall)

Reading a summary you wrote is not the same as remembering it. **Review** asks you to recall it first.

- **What comes up:** every paper or note with a summary (you see its title, authors and year), and every fragment with a note (you see the quoted passage, its paper and location). Nothing is generated; the answers are your own words.
- **One card:** write what you remember (math and `[[links]]` work as in the panel), then **Reveal** (Space, or ⌘/Ctrl+Enter while typing). Your original appears beside what you wrote, under the red margin rule. Grade yourself with **Again**, **Hard**, **Good** or **Easy** (keys 1 to 4); each button shows when you'll see the card next. Esc closes.
- **Check my recall** (optional): Claude compares what you wrote with your original and names, in at most three short lines, what you missed or got wrong. It never writes the answer for you.
- **Schedule:** SM-2 spaced repetition. Cards you know well come back after days, then weeks, then months; **Again** brings a card back tomorrow. At most five new cards are introduced per day, so a large import doesn't bury you. The end screen shows how many you did and your streak (days in a row with at least one review).
- The schedule is in `vault/state/review.json`. Deleting a note removes its card. Editing a summary keeps its schedule; you'll recall the new version next time.

## Critique history

Every **Critique my summary** is kept. Under the latest critique, **Earlier critiques (n)** lists each earlier one by date and verdict. Open one to read it, and to see how your summary has changed since: words you've added are red, words you've removed are struck through.

## Graph views

- **Filter** (top left of the graph): show papers, notes and/or fragments, a year range, one tag, or one region. Everything else fades rather than disappearing, so you keep your bearings. Fragments follow their paper's year, tags and region. The button shows how many nodes match.
- **Saved views:** type a name under **Saved views** and press Enter to save the current filter together with the search box text. Pick one to restore both. Views are kept in this browser (localStorage), not in the vault.
- **Neighbourhood focus:** double-click a node, or select it and press **F**, to hide everything except the nodes within two links of it (including their fragments) and zoom to them. Press **Esc**, **F** again, or **Show all** to return.

## Reading status, topics and what to read next

- **Reading status:** every paper and note has a compact control above its title: To read, Reading, Read,
  Later, Not relevant (click the active one again to clear it). Papers you add start as **To read**.
  The graph never colours nodes by status; use **Filter → Status** to fade everything else instead.
- **Saved because:** one line under the title for why you kept a paper ("the erosion rates, for the wall
  model"). It sits on a red margin rule, because it is your thinking.
- **Park for later (L):** sets the status to Later and puts the cursor in "Saved because", for the moment
  you realise a paper isn't for now but will be.
- **Topics:** your own groups, many-to-many, independent of the graph's regions. Add a paper under
  **Topics** just below its title (type to find one, or type a new name to create it; Shift+T jumps there).
  Each topic is a Markdown file in `vault/topics/`, so it's portable, readable in Obsidian and backed up.
- **Topics drawer (T, or Topics in the top bar):** "Next to read" across all topics, your topics with
  counts, and **Suggest topics from regions** (turn a region the graph found into a topic, name editable).
  A topic shows its queue and its papers grouped by status (Not relevant is collapsed). **Show on graph**
  fades everything outside the topic.
- **The queue never shows more than 5.** First the topic's own to-read papers, most relevant first
  (similar to what you've read or are reading in the topic, and cited by its papers). Then papers you
  don't have yet, merged from three sources with reciprocal rank fusion: papers cited by two or more of
  the topic's papers, Semantic Scholar's recommendations for the topic's papers, and similarity to the
  topic. Each says why it's there. **Accept** imports it as To read in the topic, **Dismiss** removes it
  for good (and tells Semantic Scholar what you don't want), **Not now** hides it for 14 days.
  **Show more** reveals five more.
- Recommendations are computed in the background and cached for a day (or **Refresh recommendations**),
  so opening a queue is instant. Offline, you see the last ones (or just your to-read papers) with a note.

Keys (shown in the command list when it's available): **T** topics, **N** next to read, **L** park for
later, **Shift+T** add the open paper to a topic, **Alt+1…5** set status (To read, Reading, Read, Later,
Not relevant).

## Sharing a reading list (export)

**View → Export** writes a self-contained, read-only website to `vault/exports/<date_time>/`. With a
search active it exports the matching notes; otherwise it asks "Export all N notes?". Each note gets a
page with its metadata, abstract, your summary, notes and fragments, with math rendered by the bundled
KaTeX. Links between exported notes work; links to notes you didn't export show as plain text, so
nothing outside the selection leaks. Claude's critiques are left out. Tick **Include PDFs** to copy the
papers' PDFs too (only if you're allowed to share them). The folder works opened straight from disk:
zip it and send it, or drop it on any static host. Everything happens locally. API:
`POST /api/export {"ids": [...] | null, "include_pdfs": false, "title": "Reading list"}`.

## Citing and bibliographies

research brain formats citations itself, offline, with the Citation Style Language (CSL): the same style files
Zotero, Mendeley and pandoc use (formatted by [citeproc-py](https://github.com/citeproc-py/citeproc-py)).

- **Cite while you write.** Press **Alt+C** (or *Cite papers…* in the command palette), search your library, pick
  one or more papers (Enter selects, click works too) and copy **`\cite{key1,key2}`** for LaTeX, an **in-text
  citation** like (Doe, 2024) or [1], or the **bibliography**. A paper's panel has a **Cite** button with the same
  choices plus *Download .bib*; a note that links papers with `[[ ]]` gets *Copy bibliography* / *Export .bib* for
  those papers, and so does each topic in the Topics drawer.
- **Pasting into Word, Google Docs or Pages.** Copies put formatted HTML (italics, bold volume numbers) and plain
  text on the clipboard together, so a word processor keeps the formatting and a text editor gets clean text.
  There is **no live-updating Word or Google Docs plugin**: a pasted bibliography doesn't renumber or update when
  you change a paper; paste it again. If you need live citations inside Word or Docs, Zotero's plugin is the tool
  for that (you can import your research brain papers into Zotero through a .bib or RIS export).
- **Styles.** Shipped (offline, in `app/csl/styles/`, CC BY-SA 3.0, see `app/csl/LICENSE.md`): American Physical
  Society (the default), AIP, IEEE, AIAA, Nature, APA 7th and Chicago author-date. Add any other of the 10,000+
  styles from [zotero.org/styles](https://www.zotero.org/styles): download its `.csl` file and add it in **View ›
  Library settings › Citations** (it is checked and saved to `<library>/styles/`). Dependent styles (ones that only
  point to a parent style) aren't supported; add the parent. citeproc-py doesn't implement every corner of CSL, so
  a few styles format some items imperfectly (e.g. AIP and AIAA leave the arXiv number out of a preprint); if a
  style can't be used you get a message, and the exports below always work.
- **Exports.** `.bib` as BibTeX (ASCII, accents written as LaTeX `{\"u}`) or BibLaTeX (UTF-8, for biber), RIS
  (EndNote, Mendeley, Zotero) and CSL-JSON (pandoc). Titles keep their capitals where it matters (acronyms,
  `{Hall}` in sentence-case titles, chemical formulas); arXiv preprints get `eprint` / `archivePrefix` (BibLaTeX:
  `eprinttype`). Command palette: *Export the library as .bib*. API: `/api/export/bibtex|ris|csl-json`.
- **Citation keys never change.** A paper's key comes from its `citekey` property; papers imported from Zotero keep
  their Zotero / Better BibTeX key. Otherwise research brain makes one like Better BibTeX: first author's family
  name + year + first significant title word, lower-case ASCII (`boeuf2018kinetic`), with `a`, `b`, … when two
  papers would share it. The first time a key is copied or exported it is written into the note (`citekey:` in its
  properties, without changing the note's "updated" time), so editing the title later never breaks a `\cite`.
  Change it in the Cite menu's *Key* field, or edit the property in Obsidian.
- **Auto-updating .bib files.** In **Library settings › Citations › Auto-updating .bib files**, give the full path of
  a `.bib` file (e.g. inside your thesis folder or a Dropbox/git folder Overleaf syncs), pick the whole library or
  a topic and BibTeX or BibLaTeX. research brain rewrites that file (atomically) whenever a paper in it changes,
  including edits made in Obsidian, and on every start. It writes only that file, refuses symbolic links, and never
  overwrites a `.bib` it didn't create (the file starts with `% Written by research brain`). The list shows when
  each file was last written.
- **Browser extension.** The popup has a **Cite** tab: search, pick, copy. On Overleaf the main button is
  `\cite{…}`; on Google Docs it is the formatted citation.

## On your phone

research brain runs on your computer; a phone or tablet opens it over Tailscale. Under 900px wide the app
rearranges itself for touch: a bottom tab bar (Library, Graph, Add, Review, Ask), a searchable list of your
library with status and topic filters and one-tap to-read triage, notes in a sheet you swipe down to close,
review with big grade buttons, and an Add screen for links, quick notes, photos of a whiteboard and PDFs.
Add it to the home screen and it becomes an app in the share sheet: share a link from any app to save it to
read. Desktop-only things (Zotero, exports, auto `.bib` files, profiles) say "Available on your computer".

Every device other than the computer itself must be paired first: Library settings, Phones and tablets,
Pair a phone shows a QR code. [docs/mobile-capture.md](docs/mobile-capture.md) has the setup (Tailscale,
`tailscale serve` for https), the iOS Shortcut for clipping selected text, and the security details.

## Search, live updates and streaming

- **Search** (the box at the top, and `/` to focus it) is full-text: it looks through titles, summaries, notes, quotes, abstracts, tags and authors, matches word beginnings (`thrus` finds "thruster"), and ranks title matches first.
- **Live updates.** Edit a note in Obsidian or any editor and the open page picks it up within a second: the graph refreshes, and the note you have open reloads unless you have unsaved changes in it.
- **Streaming.** Critiques and **Ask your library** answers appear word by word as Claude writes them (usually within 2–3 seconds), with math typeset as soon as each formula is complete.

## Obsidian

Open `vault/` as an Obsidian vault and your notes, links, tags and math work there too, because each
note's file name is its id, so `[[note-id|Title]]` links resolve in Obsidian. Editing properties in
Obsidian is safe. Summary mentions, manual links and fragment nesting are app-only, and renaming files
in Obsidian breaks links. [docs/obsidian.md](docs/obsidian.md) lists what works and what doesn't.

## Settings (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `RB_VAULT` | — | Use this library folder (pins it: profile switching is off). Default: the open profile in the data folder |
| `RB_PROFILE` | `default` | Open this profile (overrides the one picked in the app) |
| `RB_DATA_DIR` | per-user data folder | Move the whole data folder (portable installs, tests) |
| `RB_ACQUIRE` | `1` | `0` turns off the background open-access PDF download |
| `RB_METADATA` | `1` | `0` turns off metadata lookups (Crossref, arXiv, PubMed, Open Library, OSTI, NTRS) |
| `RB_METADATA_CACHE_DAYS` / `RB_METADATA_WAIT` | `30` / `3` | How long answers are cached; seconds an import waits for its lookup before answering |
| `RB_TRANSLATION_SERVER_PORT` | `1969` | Port of an optional local Zotero translation-server (always 127.0.0.1); `0` = don't use one |
| `RB_UNPAYWALL_EMAIL` | — | Contact email for Unpaywall (same as the Library settings field) |
| `RB_OA_MAX_MB` / `RB_OA_DEADLINE` | `60` / `90` | Size cap (MB) and time budget (s) per open-access download |
| `RB_UPLOAD_MAX_MB` | `150` | Largest PDF the extension's Save PDF may send |
| `RB_LINKED_MAX_CHARS` | `5000000` | Longest article text the extension's Save (link + text) may send |
| `RB_PORT` | `8765` | Port (the Chrome extension assumes 8765; change it in its settings too) |
| `RB_HOST` | `127.0.0.1` | Interface `run.sh` binds. Only change it to reach the app from your phone over Tailscale (see [docs/mobile-capture.md](docs/mobile-capture.md)); other devices must be paired. |
| `RB_NO_OPEN` | — | `1` stops `run.sh` opening a browser tab (the login agent sets this) |
| `RB_AGENT` | saved choice | Use this AI agent id (`claude`, `codex`, `gemini`, `copilot`, `opencode`, `cursor`, `ollama`, `openai-local`) instead of the one picked in the app |
| `RB_AGENT_TIMEOUT` | `240` | Seconds before a call to a non-Claude agent gives up |
| `RB_RESEARCH_BUDGET` / `RB_RESEARCH_MAX_STEPS` | `150` / `6` | Time (s) and lookups for the app-driven Research loop (agents other than Claude) |
| `RB_RESEARCH_LOOP` | — | `universal` makes Claude use the app-driven Research loop too |
| `RB_OLLAMA_URL`, `RB_OPENAI_LOCAL_URL` | `http://127.0.0.1:11434`, `http://127.0.0.1:1234/v1` | Local model server addresses (loopback only; the settings field does the same) |
| `RB_CLAUDE_BIN` | auto-detect | Path to the `claude` CLI |
| `RB_CLAUDE_MODEL` | your CLI default | e.g. `sonnet` for faster critiques, `opus` for deeper ones |
| `RB_AGENT_MODEL` | your CLI default | Model for Research mode. `sonnet` or `haiku` make it noticeably faster |
| `RB_AGENT_FAST_MODEL` | `haiku` | Model used when you tick **Faster model** in Research |
| `RB_AGENT_BUDGET` | `45` | Seconds Research may spend searching before it writes the best answer so far |
| `RB_AGENT_MAX_TURNS` | `8` | Most tool rounds Research may take |
| `RB_CLAUDE_TIMEOUT` | `240` | Seconds before a Claude call gives up |
| `RB_EMBEDDER` | `auto` | `tfidf` to skip the neural embedding model |
| `RB_EMBED_MODEL` | `all-MiniLM-L6-v2` | Any sentence-transformers model |
| `RB_ZOTERO_DIR` | `~/Zotero` | Zotero data directory (the folder containing `zotero.sqlite`) |
| `RB_WATCH` | `1` | `0` turns off watching `vault/notes/` for outside edits (live updates) |
| `S2_API_KEY` | — | Optional free Semantic Scholar key (avoids occasional rate limiting) |
| `RB_CITES_ON_IMPORT` | `1` | `0` stops fetching citation links in the background when you import a paper |
| `RB_ALLOWED_HOSTS` | — | Extra host names the server answers to (e.g. a Tailscale MagicDNS name); see [SECURITY.md](SECURITY.md) |
| `RB_EXTENSION_IDS` | — | Extra Chrome extension ids allowed to call the app (the bundled extension's id is pinned) |
| `RB_MAX_JSON_MB`, `RB_MAX_UPLOAD_MB` | `16`, `300` | Largest request body and PDF upload |
| `RB_PDF_TIMEOUT` | `120` | Seconds a PDF may take to parse (in a separate, limited process) |

## How it works (short version)

- `app/server.py`: FastAPI server bound to `127.0.0.1` (unless you set `RB_HOST`). It rejects requests from other websites' origins, so only this app and your extension can use it.
- `app/store.py`: one Markdown file per note.
- `app/paths.py`, `app/profiles.py`, `app/migrate.py`: which library a run opens (data folder, profiles, legacy `./vault`), and the library format version with backed-up migrations.
- `app/acquire.py`, `app/vaultzip.py`, `app/storage_api.py`: open-access PDF download, whole-library zip export/import, and their endpoints (plus the extension's PDF upload). `app/cli.py` is the `research-brain` command.
- `app/meta.py`: a SQLite copy of every note's metadata plus a full-text index (`vault/.index/meta.sqlite`), kept in step with the files, so search and the graph stay fast with thousands of notes. Delete it and it's rebuilt.
- `app/live.py`: watches `vault/notes/` and tells open pages (Server-Sent Events) when a note changes outside the app.
- `app/embed.py`: each note's text (title, abstract, your summary, notes, quote) is split into chunks, embedded with a local sentence-transformers model, and mean-pooled. Similarity is cosine similarity, and each node shows its top neighbours. A paper's own fragments are excluded from its neighbours.
- `app/agents/`: the pluggable AI backends. `base.py` is the interface and the shared lockdown helpers (private working folder, clean environment, process-group kill, timeouts); one adapter per agent (`claude.py`, `ollama.py`, `openai_local.py`, and the other CLIs); `__init__.py` is the registry and the saved choice; `research.py` is the app-driven Research loop for agents that aren't Claude.
- `app/claude_cli.py`: runs `claude -p` with all tools disabled and no saved session, from an empty directory. The paper text goes in on stdin. Critiques and chat answers stream in as Claude writes them (`--output-format stream-json`).
- `app/agent.py`, `app/agent_mcp.py`: Research mode. The tools and trail live in the app; `agent_mcp.py` is a tiny stdio MCP bridge (standard library only, starts in ~40 ms) that forwards tool calls to the running app.
- `app/memory.py`: `vault/memory/`: the generated library map (`core.md`), findings (`findings.md`) and their validation.
- `app/cards.py`, `app/cards_api.py`, `app/units.py`: idea cards (`vault/cards/`): extraction with the quote check, review, unit normalisation (pint), cross-paper links and conflicts, search and CSV.
- `app/scholar.py`: Semantic Scholar, arXiv, and Crossref lookups.
- `app/metadata.py`: the citation record (CSL-JSON) from identifiers: Crossref, DOI content negotiation, arXiv, PubMed, Open Library, OSTI, NTRS, a local translation-server, Semantic Scholar; normalisation, merge rules, cache, rate limits, background updates.
- `app/chunks.py`: the passage index for chat: page-tagged ~800-character chunks of each paper's text and of your notes, embedded and cached per note.
- `app/citations.py`: citation links from Semantic Scholar references (on import and the backfill job), throttled to about one request per second.
- `app/export.py`: the static reading-list export.
- `static/`: the graph UI (force-graph, marked, KaTeX) and the PDF.js viewer; third-party code and fonts are in `static/vendor/`.
- `scripts/`: vault backup and the launchd installer (`scripts/launchd/` holds the plist templates).

## Citation links for papers you already have

New papers get their citation links on import. To add them for your existing library, run once (the app
must be running):

```bash
curl -X POST http://127.0.0.1:8765/api/backfill/citations         # -> {"job_id": "..."}
curl http://127.0.0.1:8765/api/backfill/citations/<job_id>        # progress: done / total, linked
```

It asks Semantic Scholar for each paper's references at about one request per second (a few minutes for
a few hundred papers), backs off when rate-limited, and only adds links, so it's safe to run again after
importing more papers. Papers need a DOI, arXiv id or Semantic Scholar id. An `S2_API_KEY` helps with
large libraries.

## Which embedding model? (eval)

`eval/embed_eval.py` compares embedding models (TF-IDF, all-MiniLM-L6-v2, SPECTER2) on pairs of notes
you label as related or not, and reports recall@5 and mean similarity. See [eval/README.md](eval/README.md):

```bash
.venv/bin/python eval/embed_eval.py --suggest > eval/pairs.csv   # then fill in the `related` column
.venv/bin/python eval/embed_eval.py                              # prints a table, writes eval/results.md
```

## Development and tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium     # once, for the browser tests
.venv/bin/python -m pytest -q tests                 # everything
.venv/bin/python -m pytest -q tests --ignore=tests/e2e   # skip the browser tests (a few seconds)
```

| Tests | Covers |
| --- | --- |
| `tests/test_api.py`, `test_wikilinks.py`, `test_zotero.py` | The API with a fake Claude and mocked Semantic Scholar |
| `tests/test_cards.py`, `tests/e2e/test_cards_e2e.py` | Idea cards: reply parsing, the quote check that drops made-up cards, dedup and the 25-card cap, only paper text sent (and logged), private papers refused, confirm/edit/reject/add, units and conflicts, cross-paper links, search, CSV, the agent tool and Research action; the panel review with the keyboard and the Ideas table |
| `tests/test_review.py` | Review: SM-2 intervals, queue order and the 5-new-per-day cap, grading and persistence, streaks, items vanishing with their notes, the recall check; critique history and migration |
| `tests/test_offline.py` | No page loads scripts or stylesheets from the internet; every vendored file exists |
| `tests/test_scripts.py` | `vault_backup.sh` run for real on a temp vault, the launchd installer in dry-run mode, plist templates |
| `tests/test_export.py` | `/api/export`: selection, link handling, self-contained output, PDFs |
| `tests/test_passages_citations.py` | Chat passage retrieval (a 12-page PDF with a fact on page 9), the passage cache, citation backfill and graph links (mocked Semantic Scholar), the embedding eval harness |
| `tests/test_agent.py` | Research and memory: the tool guard (unexpected tools abort, missing tool list fails closed), the MCP bridge against a running server, `remember` validation, the map's size cap, findings edit/delete, budget handling, the SSE shape (fake agent) |
| `tests/test_storage.py` | Data folder and legacy `./vault` (keep, safe move), profiles and the restart supervisor, schema migrations with backup, open-access PDFs (mocked downloads, SSRF guard, sha256 dedupe), PDF bytes from the extension, zip export/import (zip-slip), UI state, the CLI |
| `tests/test_metadata.py` | Metadata resolvers from fixture answers (Crossref, DataCite, arXiv, PubMed, Open Library, OSTI, NTRS, Semantic Scholar, translation-server), name/title/number normalisation, merge rules, cache, rate limits, the SSRF guard, background updates and Refresh metadata |
| `tests/test_obsidian.py` | Notes re-saved by Obsidian (block lists, quoting, unquoted dates) parse identically |
| `tests/e2e/` | Real server + Chromium: empty state, new note + autosave, LaTeX and `[[` autocomplete, backlinks, PDF import + viewer + fragment from a selection, critique and chat (fake Claude), `/` search, export, delete |

Tests that need a real server start it through `tests/rb_testserver.py`: a free port handed straight to
uvicorn (no port races), and the server is always stopped, even if the test run is killed. Every test has
a time limit (`RB_TEST_TIMEOUT`, default 240 s, via pytest-timeout), so a stuck test fails with a stack dump
instead of hanging the run.

The browser tests start their own server on a free port with `RB_FAKE_CLAUDE=1` and `RB_EMBEDDER=tfidf`
in a temp vault, block every request that isn't to that server, and are skipped (with the reason) if
Playwright or a browser isn't installed. `RB_E2E_HEADED=1` shows the browser. GitHub Actions runs the
whole suite on every push (`.github/workflows/tests.yml`).
