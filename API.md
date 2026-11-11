# research_brain — API contract

Local FastAPI server on http://127.0.0.1:8765. Frontend is static files in `static/`
served at `/` (index.html, app.js, style.css). All JSON. Errors: HTTP 4xx/5xx with
`{"detail": "<message>"}`.

Every request first passes the checks in app/security.py (SECURITY.md has the reasons):
- `Host` must be `127.0.0.1`, `localhost`, `[::1]` (any port), `RB_HOST` or `RB_ALLOWED_HOSTS`, else **421**.
- An `Origin`, if sent, must be this app (same host and port) or the research_brain extension, else **403**.
  Browser requests with `Sec-Fetch-Site: cross-site`/`same-site` and no Origin get **403** unless they are
  top-level navigations. Clients that send neither header (curl, iOS Shortcuts) are fine.
- POST/PUT/PATCH/DELETE bodies must be `application/json` (or `multipart/form-data` on the PDF uploads,
  `application/pdf` / `application/zip` on the raw-body imports), else **415**.
- Bodies over `RB_MAX_JSON_MB` (16) or uploads over `RB_MAX_UPLOAD_MB` (300) get **413**.

## Data model: Note

```json
{
  "id": "attention-is-all-you-need-2017",   // slug, stable
  "type": "paper" | "note",
  "title": "string",
  "authors": ["string"],                     // papers; [] for notes
  "year": 2017 | null,
  "venue": "string" | "",
  "doi": "string" | "",
  "arxiv": "string" | "",
  "s2_id": "string" | "",                    // Semantic Scholar paperId
  "url": "string" | "",
  "abstract": "string" | "",
  "summary": "string",                       // USER-written 2-3 sentences (markdown + $LaTeX$)
  "body": "string",                          // user's free notes (markdown + $LaTeX$ / $$...$$)
  "tags": ["string"],
  "links": ["note-id"],                      // manual links (undirected; stored on both sides)
  "cites": ["note-id"],                      // papers: library papers this one cites (Semantic Scholar references; directed)
  "status": "" | "to-read" | "reading" | "read" | "later" | "not-relevant",   // reading status; new papers start "to-read"
  "saved_because": "string",                 // the user's one-line "why I kept this"
  "topics": [{"slug","name"}],               // GET/PUT responses: topics this note belongs to
  "has_pdf": true | false,
  "has_text": true | false,                  // extracted full text available for Claude
  "source": "" | "linked" | "pdf",           // papers: "linked" = link + text, no PDF file kept (see "Linked papers")
  "csl": {CSL-JSON item} | null,             // papers: the bibliographic record (see "Paper metadata")
  "meta_source": "" | "crossref" | "datacite" | "arxiv" | "pubmed" | "openlibrary" | "osti" | "ntrs"
               | "translation-server" | "s2" | "page" | "zotero",
  "meta_checked": "ISO8601" | "",            // when the record was last resolved
  "_extra": {"citekey": "string", ...},      // frontmatter properties the app doesn't model; `citekey` = the paper's
                                             // citation key once copied/exported or set (see "Citing")
  "created": "ISO8601",
  "updated": "ISO8601",
  "critique": {"text": "markdown", "at": "ISO8601", "summary_snapshot": "string"} | null,   // the latest
  "critiques": [{"text", "at", "summary_snapshot"}]   // every critique, oldest first (see "Critique history")
}
```

## Endpoints

- `GET /api/status` → `{"claude": {"available": bool, "path": str|null, "detail": str},
   "embedder": "sentence-transformers:<model>" | "tfidf", "notes": int, "vault": str}`

- `GET /api/graph` →
  ```json
  {"nodes": [{"id","type","title","year","authors","tags","degree"}],
   "links": [{"source","target"}],                       // manual links, deduped
   "similar": [{"source","target","score"}]}             // semantic: for each node its top-5 neighbours, deduped, score in [0,1]
  ```
  `"embedder"` field also included. Frontend filters `similar` by a threshold slider
  (default 0.45 for sentence-transformers, 0.15 for tfidf — use `GET /api/status` embedder to pick).

- `GET /api/notes/{id}` → Note + `"similar": [{"id","title","type","score"}]` (top 8).
- `POST /api/notes` body `{"type":"note"|"paper","title", ...any Note fields}` → Note (201).
- `PUT /api/notes/{id}` body: partial Note fields (`title, summary, body, tags, authors, year, venue, url, abstract`) → Note.
- `DELETE /api/notes/{id}` → `{"ok": true}` (moved to `vault/.trash/`).
- `POST /api/notes/{id}/link` body `{"target": "id"}` → Note (adds manual link both ways).
- `DELETE /api/notes/{id}/link/{target}` → Note.
- `POST /api/notes/{id}/opened` → `{"opens": int}`. Counts a visit (the panel calls it when you open a note).
  Stored in `vault/state/engagement.json`, not in the note file, so it doesn't change `updated`. Graph nodes
  also carry `mass` (words you wrote in the summary, notes and fragment notes) and `opens`; together they set
  node size.

- `POST /api/import/pdf` multipart form field `file` (PDF) → Note (201). Extracts text,
  finds DOI/arXiv id, enriches from Semantic Scholar when possible. Returns existing note
  with `"duplicate": true` if same DOI/arXiv/s2_id already in library.
- `POST /api/import/lookup` body `{"query": "DOI | arXiv id | arxiv/doi URL | title"}` → Note (201)
  (metadata+abstract only, no PDF). 404 if Semantic Scholar finds nothing.
- `POST /api/notes/{id}/pdf` multipart `file` → Note (attach PDF to an existing paper).
- `GET /pdf/{id}` → the PDF file (inline).

### Claude (local `claude -p` CLI; each call can take 10–90 s — show a spinner)
- `POST /api/notes/{id}/critique` → `{"text": "markdown", "at": ISO, "summary_snapshot": str}`. 400 if summary empty.
  Appends to the note's `critiques` history and sets `critique` to the new one.
- `POST /api/explain` body `{"a": id, "b": id, "refresh": false}` →
  `{"text": "markdown", "cached": bool}`.
- `POST /api/chat` body `{"question": str, "history": [{"role":"user"|"assistant","content":str}]}` →
  `{"answer": "markdown (cites notes as [[note-id]] or [[note-id]] p. N)", "sources": [{"id","title","score"}],
   "passages": [{"id","title","page","src","text","score","pdf"}]}` — see "Passage retrieval" below.

### AI agents (app/agents; any AI tool on this computer, never an API key)
- `GET /api/status` → `{"claude": {"available", "path", "detail"}, "agent": {"id", "name", "kind",
  "available", "version", "path", "signed_in", "note"}, "embedder", "notes", "vault"}`. `claude` keeps its
  old shape but now describes the **active** agent, whichever it is.
- `GET /api/agents[?fresh=true]` → `{"agents": [{"id", "name", "kind": "cli"|"local-server", "home",
  "cost_note", "detection": {"available", "version", "path", "signed_in": bool|null, "note"},
  "model"?, "models"?, "url"?}], "active": id, "chosen": id|""}`. Detection is cached for 30 s;
  `fresh=true` checks again. `model`/`models`/`url` only for local servers.
- `PUT /api/agents/active` body `{"id": str, "model"?: str, "url"?: str}` → `{"active": id, "agent": {...}}`.
  Saved as `agent` (plus `agent_models` / `agent_urls`) in the settings file. `model: ""` / `url: ""`
  go back to the default. 404 unknown id; 400 for a URL that isn't on this computer (loopback only).
- Every AI call below (critique, explain, chat, review check, Research) goes to the active agent; the
  request and response formats are the same for all of them. Errors from the agent are 502s (or SSE
  `error` events) with a plain-language `detail`.

### Discovery (Semantic Scholar)
- `GET /api/notes/{id}/discover` →
  ```json
  {"references": [Candidate], "citations": [Candidate], "recommended": [Candidate]}
  Candidate = {"s2_id","title","year","authors":[str],"citationCount":int,
               "doi","arxiv","url","in_library": note-id|null}
  ```
  400 if the note has no s2_id/doi/arxiv.
- `POST /api/import/s2` body `{"s2_id": str, "link_to": note-id|null}` → Note (201).

All Claude/Semantic Scholar failures return 502/503 with a human-readable `detail`.

## ADDED: Fragments (notes on a specific part of a paper/note)

A fragment is a Note with `"type": "fragment"` plus these fields (present on ALL notes;
empty for non-fragments):
```json
{"parent": "note-id" | "",      // the paper/note this fragment belongs to (required for fragments)
 "quote": "string",             // the passage/equation text it points at (may contain LaTeX)
 "locator": "string",           // free label, e.g. "p.4, Eq. 7" or "Sec 3.2"
 "page": 4 | null}              // 1-based PDF page if known (set by the PDF viewer)
```
- Fragment `body` = the user's own note about that passage; `summary` unused.
- Fragments get their own embeddings (quote + body), so they show up in `similar` and
  can be linked/explained like any note. A fragment's own parent is excluded from its `similar` list.
- `POST /api/notes` with `{"type":"fragment","parent":id,"quote","locator","page","body","title"?}`.
  Title defaults to the locator, else first ~8 words of the quote. 400 if parent missing.
- `PUT /api/notes/{id}` additionally accepts `quote, locator, page` (fragments).
- `GET /api/notes/{id}` additionally returns `"fragments": [{"id","title","quote","locator","page","body"}]`
  (children of this note, sorted by page then created) — empty list for none.
- `DELETE` of a paper/note also moves its fragments to trash.
- `GET /api/graph`: nodes gain `"parent"` (fragment → parent id, else ""). Fragment→parent edges are
  included in `links` with `"kind": "part"`; manual links have `"kind": "manual"`.
- Fragments are excluded from Discover and from critique.

## ADDED: PDF viewer + browser-extension support
- `GET /viewer/{id}` → `static/viewer.html` (PDF.js viewer for note `id`; it fetches `/pdf/{id}`
  and `/api/notes/{id}`; selecting text lets you create a fragment with quote + page).
- `GET /api/search?q=text&limit=20&fragments=0` → `[{"id","title","type","year"}]`. Full-text search (SQLite FTS5)
  over title, summary, body, quote, abstract, tags and authors. Every word is a prefix (`thrus` finds "thruster"),
  all words must match, and results are ranked by bm25 with the title weighted highest (then tags/authors); a
  title that starts with the query comes first. Fragments are excluded unless `fragments=1`. Empty q → most
  recently updated. `limit` is capped at 100. See "Metadata index" below.
- `GET /api/match?url=<page url>` → `{"note": {"id","title","type"} | null}` — finds a library
  paper whose url/doi/arxiv matches the page (arxiv.org/abs|pdf/<id>, doi.org/<doi>, or exact url).
- `POST /api/clip` body `{"quote": str, "url": str, "page_title": str, "note": str,
   "locator": str, "target_id": id|null}` →
  `{"fragment": Note, "parent": Note, "created_parent": bool}` (201).
  Target resolution: `target_id` if given; else `/api/match` on url; else if the url contains an
  arXiv id or DOI, import it from Semantic Scholar; else create a plain `note` titled `page_title`
  with `url` as the source. Then creates the fragment under it.
- CORS: requests from `chrome-extension://*` and the app's own origin are allowed; any request
  carrying another `Origin` header is rejected with 403 (protects the local server from websites).

## ADDED: Regions (clusters)
`GET /api/graph` nodes gain `"cluster": int` (-1 = not in any region; fragments inherit their
parent's), and the response gains `"clusters": [{"id": int, "label": "plasma, simulation, thruster", "size": int}]`
— groups of top-level notes found by label propagation over semantic similarity + manual links,
labelled by their most distinctive terms (largest first).

## ADDED: Wikilinks ([[...]] mentions and backlinks, Obsidian-style)
Notes reference each other by writing `[[target]]` or `[[target|alias]]` in `summary`, `body` or `quote`
(code spans and fenced code are ignored). `target` resolves as:
1. an exact note id, else
2. a case-insensitive exact title match (whitespace collapsed); non-fragments win, then the oldest note.
Anything else is kept as *unresolved*. Self-links are ignored. Wikilinks are derived from text on every
index rebuild; they are never written into `links` (manual links stay separate).

- `GET /api/notes/{id}` additionally returns:
  ```json
  {"mentions":   [{"id","title","type"}],             // notes this one links to, in order of first mention, deduped
   "backlinks":  [{"id","title","type","snippet"}],   // notes whose text links here, newest first; snippet is
                                                      // ~160 chars of context around the [[...]], brackets kept
   "unresolved": ["text", ...]}                       // targets that match no note (first spelling kept)
  ```
- `GET /api/graph`: `links` gain `"kind": "mention"` edges (`source` = the note containing the wikilink,
  `target` = the referenced note). One edge per unordered pair, and none where a manual link or a
  fragment's part edge already joins the pair. Mention edges count toward `degree` and join the region
  clustering with weight 0.8 (a fragment's mention counts for its parent).
- Creating a note for an unresolved link is just `POST /api/notes {"type":"note","title": X}`; `[[X]]`
  then resolves by title.
- `PUT /api/notes/{id}` with a changed `title`: other notes' `[[Old title]]` and `[[Old title|alias]]`
  (case-insensitive, only when they resolved to this note) are rewritten to `[[New title]]` /
  `[[New title|alias]]`. If the new title can't be a link target (another note already has it, it equals
  another note's id, or it contains `[`, `]`, `|`), they become `[[note-id|Old title]]` /
  `[[note-id|alias]]` instead, so they keep pointing at this note. `[[note-id]]` links never need
  rewriting. The response gains `"rewritten": [ids of notes changed]` when any were.
- The UI inserts `[[Title]]` when that title belongs to exactly one note and is link-safe, else
  `[[note-id|Title]]`.

## ADDED: Zotero import
All notes gain `"zotero_key": "string"` (Zotero item key for papers, annotation key for fragments; "" otherwise).
Fragments imported from a Zotero annotation also store `"zotero_annotation"` (the annotation key) and
`"zotero_hash"` (fingerprint of the quote + note as imported, used to tell whether you have edited it since).
- `GET /api/zotero/status` → `{"found": bool, "dir": str, "items": int, "message": str, "job": job_id|null,
   "libraries": [{"id": int, "name": str, "kind": "user"|"group", "items": int}],
   "collections": [{"key","name","parentKey": key|null,"depth": int,"count": int,"library_id": int}]}`
   (tree order; count includes sub-collections). `libraries` lists your own library ("My Library") first, then
   group libraries by name; feeds are left out. `found: false` comes with a `message` saying what to do.
- `POST /api/zotero/import` body `{"collection_key": str|null, "include_pdfs": bool, "include_annotations": bool,
  "library_id": int|null}` → `{"job_id"}`. `library_id` limits the import to one library (null = all); a
  `collection_key` must belong to it. 503 if no library, 404 unknown library or collection, 409 if an import is
  already running.
- `GET /api/zotero/jobs/{id}` → `{"id","state": "running"|"done"|"error","done","total","created","updated","skipped",
   "fragments","pdfs","errors": [str],"current": "title","message": str,"library_id": int|null,
   "highlights_updated": int,"highlights_removed": int,"highlights_kept": int,"kept": [{"id","title"}]}`.
- **Annotation sync on re-import.** For a paper that's already in the library, fragments that came from Zotero
  annotations are compared with Zotero: a changed highlight text, comment or page updates the fragment
  (`highlights_updated`), and a deleted annotation moves the fragment to `vault/.trash/` (`highlights_removed`)
  — but only if you haven't edited that fragment's quote or note since it was imported. Edited fragments are
  never changed or removed; they're listed in `kept` / counted in `highlights_kept` ("kept (edited)"). Their page
  is still refreshed. Fragments you made yourself are never touched.

## ADDED: Static export (reading lists)
- `POST /api/export` body `{"ids": ["note-id", ...] | null, "include_pdfs": false, "title": "Reading list"}` →
  201 `{"path": "/abs/vault/exports/2026-11-09_184812", "name", "index": "<path>/index.html",
  "notes": int, "fragments": int, "pdfs": int}`.
  `ids: null` exports every paper and note. A selected paper/note brings its fragments (shown on its page);
  a fragment selected without its parent gets its own page. Writes `index.html`, `notes/<id>.html`,
  `assets/` (vendored KaTeX/marked/fonts) and, with `include_pdfs`, `pdfs/<id>.pdf`. All links are relative;
  `[[links]]` to notes outside the export render as plain text. Critiques are not exported.
  404 unknown id, 400 empty selection. Nothing is sent over the network.

## ADDED: Passage retrieval for chat
`POST /api/chat` (and its streaming twin) retrieve **passages**, not just whole notes: every paper's
extracted text is split into ~800-character chunks on paragraph boundaries that never cross a page
(page from the `[[page N]]` markers), and each note's abstract, summary, notes and each fragment's
quote + note are chunked too (a fragment's passages carry its `page`). Claude gets the top 12 passages
(at most 3 per note) headed `### [[note-id]] p. N`, plus the top 4 whole notes as an overview, and is told
to cite as `[[note-id]] p. N`.
- `passages[]`: `page` is an int or null (abstract/summary/notes); `src` is `"text" | "abstract" | "summary" |
  "notes" | "fragment"`; `text` is the passage as sent to Claude; `pdf` is the id of the note whose PDF holds
  the page (the paper itself, or a fragment's parent) or `""`.
- `sources[]` now lists the whole notes plus every note a passage came from (best score per note).
- Passage vectors are cached in `vault/.index/chunks-<embedder>.json` (+ `.npy` for sentence-transformers),
  keyed per note by a hash of its text fields and the text file's size/mtime; only changed notes are
  re-chunked and re-embedded. With TF-IDF a separate vectorizer is fitted over the passages.
- `GET /api/graph` nodes gain `"has_pdf": bool` (used to link page citations to the viewer).
- Frontend: `[[id]] p. N`, `[[id]], p. N` and `[[id]] (p. N)` render as one chip; for a paper (or a fragment
  whose parent) with a PDF it is a link to `/viewer/<id>#page=N` opening in a new tab.

## ADDED: Linked papers (link + text, no PDF file)
The extension's default **Save to research_brain (link + text)**. Logic in `app/storage_api.py`, page side in
`extension/extract.js`.
- `POST /api/import/linked` JSON `{"url", "title"?, "authors"?: [str], "doi"?, "arxiv"?, "year"?: int|str, "venue"?,
  "abstract"?, "text"?, "kind"?: "html"|"pdf", "references"?: [str], "pdf_url"?}` → 201 Note (`type: "paper"`,
  `source: "linked"`) + `"open"`, `"save_mode"`, `"text_attached"`, `"cites_added"`, `"pdf_url"`?, and
  `"duplicate"`/`"existing"` for a paper already in the library.
  - Validation: `url` http(s) without credentials (400); `kind` html or pdf (400); `text` ≤ `RB_LINKED_MAX_CHARS`
    (5,000,000; 413); nothing to save (no text, title or id) 400. Control and invisible characters are stripped,
    one-line fields collapsed and capped (title 500, author 200 × 300, venue 300, abstract 20,000, reference 1,000 × 3,000).
    `html` text keeps `[[section <heading>]]` markers (page markers are removed); `pdf` text keeps `[[page N]]`
    (section markers removed). DOI/arXiv ids are normalised and also read from the URL (doi.org, `/doi/10.…`, arxiv.org).
  - Dedupe: DOI / arXiv id, then the URL (and `pdf_url`) by the `/api/match` rules. An existing paper without text gets
    the text (a linked paper's text is replaced by a longer one, e.g. arXiv abstract page then its HTML version) and
    empty metadata fields are filled; one with a PDF keeps its PDF and text, only `url` is recorded. A web source note
    for the same page (made by clipping it) becomes the paper, fragments included.
  - New papers: page metadata wins; Semantic Scholar is asked only when the page gave an id but no title/authors.
    Citation links run as for other imports; DOIs/arXiv ids in `references` that match library papers become `cites`.
  - No PDF is downloaded, unless `save_mode` is `"pdf"` and `auto_open_access` is on (then only the usual
    open-access search for a free legal copy). The text is indexed like any other paper's (search, passages,
    similarity, memory map); `/api/events` sends `notes-changed`.
- `GET /api/import/linked/prefs` → `{"save_mode": "link"|"pdf"}` (the extension's primary button follows it).
- Passages of `[[section …]]` text carry `"section"` (the citation label: the heading's number such as `"3.2"`, else
  the heading shortened to 32 characters) and `"section_title"`; chat/research context headings read
  `### [[id]] §3.2` or `### [[id]] §"Results"`. Passages of a linked paper (chat, research) carry `"linked": true` and
  `"url"`: the article at `#page=N` (PDF text) or at a `#:~:text=` fragment of the passage's first words (the section
  heading when those contain math). `GET /api/graph` nodes gain `"source"` and, for linked papers, `"url"`.
- Frontend: `[[id]] §3.2`, `[[id]] (§3.2)` and `[[id]] §"Title"` render as one chip; into a linked article (no PDF) it
  and `[[id]] p. N` are links to the article (new tab, `rel=noopener noreferrer`) instead of the viewer.

## ADDED: Citation links (Semantic Scholar references)
Papers gain `"cites": ["note-id"]`: the papers **in the library** that this paper's Semantic Scholar
reference list contains, matched by S2 id, DOI, arXiv id, then exact normalised title. References outside
the library are ignored. `cites` is derived, so writing it does not change `updated`.
- On import (`/api/import/pdf`, `/api/import/lookup`, `/api/import/s2`, clip-created papers) a background
  thread fills the new paper's `cites` and adds it to the `cites` of library papers that cite it. It never
  delays the response. `RB_CITES_ON_IMPORT=0` turns this off.
- `POST /api/backfill/citations` → `{"job_id", "already_running": bool}`. Walks every paper with an s2_id,
  DOI or arXiv id. Only one job runs at a time (a second POST returns the running job). Re-running is
  idempotent; it only adds links.
- `GET /api/backfill/citations/{job_id}` → `{"id","state": "running"|"done"|"error","done","total","linked",
  "skipped","errors": [str],"current": "title","message","started","finished"}`. 404 unknown job.
  `state: "error"` with a message when Semantic Scholar can't be reached.
- Rate limiting: all citation requests share one throttle of about one request per second (`RB_S2_INTERVAL`)
  and wait 5 s, 20 s, then 60 s after HTTP 429 before giving up on a paper.
- `GET /api/graph`: `links` gain `{"source": citing, "target": cited, "kind": "cites"}` (directed). No cites edge
  where a manual link joins the pair. Cites count toward `degree` and region clustering (weight 0.6).

## ADDED: Review (active recall, spaced repetition)
Items are derived from the library on every call (nothing is generated); the item id is the source note's id.
- `kind: "summary"`: every paper/note with a non-empty `summary`. Prompt: title, authors, year. Answer: the summary.
- `kind: "fragment"`: every fragment with a `quote` and a non-empty `body`. Prompt: the quote, its paper's title, the
  locator (or `p. N`). Answer: the fragment's note.

Schedule state lives in `vault/state/review.json` (`{"items": {id: {kind, reps, ease, interval, due, history:
[{at, date, grade, recalled?}]}}, "days": {date: {new, reviewed}}}`). State for deleted notes is pruned on every
read; an item whose summary/note is emptied leaves the queue but keeps its history.
Scheduling is SM-2 (grades 0–5, below 3 is a lapse that restarts the item at 1 day) with the usual refinements:
Hard (3) grows the interval ×1.2, Good (4) ×ease, Easy (5) ×ease×1.3. Buttons: again=1, hard=3, good=4, easy=5.

- `GET /api/review/queue?limit=10` → `{"items": [Item], "due": int, "new": int, "total": int}`. Due items first,
  oldest due date first; then new (never reviewed) items, oldest created first, at most 5 new per day.
  `Item = {"id","kind","source","prompt": {...},"answer": str,"is_new": bool,"due": "YYYY-MM-DD",
  "intervals": {"again","hard","good","easy"} (days if graded now),"last": {at, grade}|null}`.
- `POST /api/review/{id}/grade` body `{"grade": 0-5 | "again"|"hard"|"good"|"easy", "recalled_text"?: str}` →
  `{"id","reps","ease","interval","due"}`. 400 bad grade, 404 if the item no longer exists.
- `GET /api/review/stats` → `{"due_today" (due + new allowed today),"due","new","reviewed_today","streak","total"}`.
  `streak` counts consecutive days with at least one review, ending today (or yesterday if today has none yet).
- `POST /api/review/{id}/check` body `{"recalled_text": str}` → `{"text": "- ...\n- ..."}` (Claude; at most 3 short
  lines naming what the recall missed or got wrong; it never restates the original). 400 if empty, 404 unknown.

## ADDED: Critique history
Every critique is kept. Notes gain `critiques: [{text, at, summary_snapshot}]` (oldest first, stored in the
frontmatter only when non-empty); `critique` stays the latest for compatibility. A note written before this has
only `critique`; it is read as a one-entry history and saved that way on the next critique. `critiques` can't be
set through `POST`/`PUT /api/notes`. The panel lists earlier critiques (date and verdict line) under the latest
one, and shows a word diff between each one's `summary_snapshot` and the current summary.

## ADDED: Graph views (frontend only)
Filter (type, year range, tag, region), saved views and 2-hop neighbourhood focus live in `static/views.js` and
use no new endpoints. Saved views and the current filter are stored in the browser's localStorage
(`rb.graphViews`, `rb.graphFilter`). Hooks on `window.__rb`: `setNodeFilter(fn|null)` (non-matching nodes fade to
the dim alpha in `targetAlpha`), `setHidden(Set|null)` (nodes and their links are not drawn), and
`S.nodeClickHook(node) → bool` (return true to swallow a click).

## ADDED: Metadata index (SQLite + full-text search)
Markdown files stay the source of truth. `vault/.index/meta.sqlite` (derived; delete it any time) mirrors every
note: a `notes` table (all frontmatter fields as JSON, plus the file's `mtime_ns` and `size`, and indexed
`type`, `url`, `doi`, `arxiv`, `s2_id`, `parent` columns) and an FTS5 table over title, summary, body, quote,
abstract, tags and authors.
- **Freshness.** Every request that reads notes first lists `vault/notes/` (one `scandir`, no file reads) and
  re-parses only files whose mtime or size changed, so edits made outside the app are picked up immediately.
  The app's own writes update their row directly.
- **Fallback.** A missing, old-schema or corrupt database is deleted and rebuilt from the files (also if it
  breaks while the app runs). If SQLite can't be used at all, the app reads the files as before.
- Served from the index: `/api/search`, `/api/match`, duplicate detection on import, the note list behind
  `/api/graph` and `GET /api/notes/{id}`. Regions (clusters) and the serialised `/api/graph` payload are cached
  per index signature, so a warm `/api/graph` is a cache hit.
- Measured at 2,000 notes + 300 fragments (tests/test_scale.py, tf-idf embedder): warm `/api/graph` 8 ms (was
  ~200 ms), `/api/search` 8–10 ms (was ~2,100 ms), `GET /api/notes/{id}` ~12 ms (was ~2,000 ms).

## ADDED: Live updates (Server-Sent Events)
- `GET /api/events` → `text/event-stream`. Sends `event: hello` on connect, a `: keep-alive` comment every 20 s,
  and `event: notes-changed` with `data: {"ids": [note-id, ...], "at": unix_time}` when files in `vault/notes/`
  are created, edited or deleted **outside the app** (Obsidian, an editor, git, a sync client). The server
  watches the folder with `watchdog`, debounced 300 ms, and resyncs the metadata index for those files first.
  Changes the app made itself are not reported (it remembers its own writes for ~2 s and compares the file's
  mtime and size). Set `RB_WATCH=0` to turn the watcher off.
- Frontend: `static/live.js` subscribes with `EventSource`, refreshes the graph, and reloads the open note if it
  changed and has no unsaved edits (and you aren't typing in one of its fields; it waits until you leave it).
- The origin guard applies as usual: the page's own origin is allowed, other websites get 403.

## ADDED: Streaming Claude answers
Same request bodies and errors-before-start (400/404) as the plain endpoints, but the response is
`text/event-stream`:
- `POST /api/notes/{id}/critique/stream`
- `POST /api/chat/stream`

Events: `event: delta` / `data: {"text": "next piece"}` as Claude writes, then exactly one of
`event: done` / `data: <the same JSON the plain endpoint returns>` or `event: error` /
`data: {"detail": "readable message"}`. Comment lines (`: keep-alive`) may appear while Claude is thinking.
A finished streaming critique is stored exactly as `POST /api/notes/{id}/critique` stores it (same helper,
so it's in `critiques` too). The CLI runs as `claude -p --output-format stream-json --verbose
--include-partial-messages` with the same no-tools/no-MCP/no-session flags as every other call. The frontend
reads it with `fetch` + `ReadableStream`, re-renders markdown and KaTeX at most every ~80 ms (an unclosed
trailing `$…` stays plain text until it closes), and falls back to the plain endpoint if streaming fails for a
reason other than Claude itself.

## ADDED: Reading status, topics and reading queues

Notes have `status` and `saved_because` (see the data model). `PUT /api/notes/{id}` accepts both; an unknown
status is a 400. `saved_because` is collapsed to one line (max 300 chars). `GET /api/graph` nodes carry
`status` (for filtering; the graph never colours by it).

- `POST /api/notes/{id}/park` body `{"saved_because"?: str}` → Note with status `"later"` ("Park for later").

Topics are files, `vault/topics/<slug>.md`: frontmatter `{name, description, created, dismissed?, snoozed?}`
and a body of `- [[note-id]]` lines (the ordered members). Dismissals and snoozes live there, not in `.index`.
- `GET /api/topics` → `[{"slug","name","description","created","count","counts": {status: n}}]` (counts key `""` = no status).
- `POST /api/topics` body `{"name", "description"?, "members"?: [id]}` → Topic (201). 400 on an empty or duplicate name.
- `GET /api/topics/{slug}` → `{"slug","name","description","created","count","counts","members": [id],
  "groups": [{"status","label","count","items": [{"id","title","type","year","authors","status","saved_because","s2_id"}]}],
  "dismissed": int}`; groups in the order To read, Reading, Later, Read, Not relevant, No status.
- `PATCH /api/topics/{slug}` body `{"name"?, "description"?}` → Topic (the slug never changes).
- `DELETE /api/topics/{slug}` → `{"ok": true}` (file moved to `.trash`; papers untouched).
- `POST /api/topics/{slug}/notes` body `{"ids": [id]}` → Topic. Fragments and unknown ids are a 400.
- `DELETE /api/topics/{slug}/notes/{id}` → Topic.
- `GET /api/topics/suggest` → `[{"region","name","label","size","members"}]`: graph regions not already covered by a
  topic (Jaccard overlap < 0.6), with the region label as the suggested name. Create one with `POST /api/topics`.

Queue (never more than 5 items per response; page with `offset`):
- `GET /api/topics/{slug}/queue?offset=0&limit=5` →
  `{"items": [Item], "total", "offset", "limit", "to_read", "recommended", "recs_state": "fresh"|"refreshing"|"offline"|"error"|"none",
    "refreshing": bool, "note": "quiet one-line status or empty", "recs_at": epoch|null}`.
  Library items first: `{"kind":"library","id","title","year","authors","status","saved_because","key":"note:<id>","reason","score"}`,
  the topic's to-read papers ranked by cosine similarity to the centroid of its read/reading papers + 0.15 per topic paper citing it.
  Then recommendations: `{"kind":"rec","key","s2_id","title","year","authors","doi","arxiv","abstract","reason","score","sources","cited_by"}`.
  Never calls Semantic Scholar inline: a missing, day-old or out-of-date cache (`.index/topic-recs.json`) starts a background refresh.
- Recommendations fuse three ranked lists with reciprocal rank fusion (`sum 1/(60 + rank)`): (a) papers cited by 2+ topic papers
  (`scholar.references`, cached 14 days in `.index/refs-cache.json`), (b) `POST https://api.semanticscholar.org/recommendations/v1/papers`
  `{"positivePaperIds": topic papers, "negativePaperIds": not-relevant + dismissed}` → `recommendedPapers`, (c) similarity of
  title + abstract to the topic centroid. Papers already in the library, dismissed or snoozed are excluded. Reason is
  "Cited by N papers in this topic", "Similar to <title>", or "Semantic Scholar recommends it for this topic".
- `POST /api/topics/{slug}/queue/refresh?wait=0` → queue (recomputes in the background; `wait=1` blocks).
- `POST /api/topics/{slug}/queue/accept` body `{"s2_id"}` → Note (201): imported (or the existing duplicate), status `to-read`,
  added to the topic. Offline, it imports the cached metadata.
- `POST /api/topics/{slug}/queue/dismiss` body `{"s2_id", "title"?}` → `{"ok": true}`; never shown again, sent as a negative.
- `POST /api/topics/{slug}/queue/snooze` body `{"key", "days"?: 14}` → `{"ok", "until"}`; key = a rec's `key` or `note:<id>`.
- `GET /api/queue?offset=0&limit=5` → `{"items": [Item + "topic": {"slug","name"}|null], "total", "offset", "limit", "topics", "note"}`:
  round-robin across topics' queues; with no topics, the library's to-read papers, newest first.

Python (for agents / the library map), `app/topics.py`: `list_topics(store)`, `topic_detail(store, slug)`,
`topics_for(store, note_id)`, `papers_by_status(store, status, topic=None)`, `local_queue(store, idx, slug)`,
`queue(store, idx, embedder, slug)`, `global_queue(store, idx, embedder)`.

## ADDED: Research mode (agent) and memory
Research lets Claude search the library itself, with research_brain's own tools and nothing else (see
SECURITY.md for the flags and the init-event tool guard).

- `POST /api/research/stream` body `{"question": str, "history"?: [{role, content}], "fast"?: bool}`
  → `text/event-stream` (400 before the stream for an empty question or library). Events:
  - `step` `{"id","label","tool","ids":[note ids],"t_ms","status":"running"|"done"|"error","note"?}`. The same
    id is sent again when its status changes. The first step (`tool: "seed"`) arrives at once: the app's own
    hybrid search, done before Claude starts. Labels read "Searching: a; b", "Reading pp. 4–5 of [[id]]",
    "Following citations of [[id]]", "Checking memory", "Saving a finding to memory", and "Time budget reached;
    writing the best answer from what was found".
  - `delta` `{"text"}` answer text; `reset` `{}` drop the text so far (it was a preamble before a tool call).
  - `done` `{"answer","sources":[{id,title,cited}],"passages":[{id,title,page,src,text,score,pdf}],"steps",
    "timing":{"first_progress_ms","seed_ms","first_text_ms","total_ms","tool_calls"},"ended":"done"|"budget"|"max_turns","mode":"research"}`
  - `error` `{"detail","guard"?: true}` (`guard` = the tool guard stopped the run).
  - `fast: true` uses `RB_AGENT_FAST_MODEL` (default `haiku`) and `--effort low`. `RB_AGENT_MODEL` sets the
    normal model (default: your CLI default). `RB_AGENT_BUDGET` (45 s) and `RB_AGENT_MAX_TURNS` (8) bound the run.
    When either runs out, a no-tools call writes the best answer from what was gathered.
  - `RB_FAKE_CLAUDE=1`: a deterministic fake trail (seed, `search` with two queries, `read_pages` or `read_note`)
    through the real tools, then a fixed answer citing the top passage.
  - **Any agent other than Claude** (or `RB_RESEARCH_LOOP=universal`): the same events and `done` shape, from the
    app-driven loop in `app/agents/research.py`. The model gets no tools; each turn it replies with one JSON
    object, which the app validates and runs with the same tool code:
    `{"action":"search","queries":[≤4]}` (or `"query"`), `{"action":"read","id","pages"?:[first,last]}` (pages →
    `read_pages`, ≤3; without → `read_note`), `{"action":"citations","id","direction"?}`, `{"action":"similar","id"}`,
    `{"action":"memory","query"}` (→ `memory_search`), `{"action":"remember","text","sources"}` (≤2 per question),
    `{"action":"answer","text"}`. JSON may sit in a code fence or among prose; one corrective retry on unreadable
    output. Bounded by `RB_RESEARCH_MAX_STEPS` (6) and `RB_RESEARCH_BUDGET` (150 s); `ended` can also be
    `"malformed"` (replies couldn't be read twice). In those cases the same agent writes the best answer from what
    was gathered, with no tools. Refused actions show as `error` steps and are reported back to the model.
- `POST /api/agent/tools/{name}` body `{"args": {...}}`, header `X-RB-Agent-Token` → `{"text","is_error"}`.
  Used only by `app/agent_mcp.py` during a run. The token is per request (403 otherwise), loopback clients
  only. Tools: `search {queries: [≤4], k?}`, `search_notes {query, limit?}`, `read_note {id}`,
  `read_pages {id, start, end?}` (≤3 pages), `citations {id, direction: out|in|both}`, `similar {id, n?}`,
  `recent {n?}`, `list_topics {}`, `topic_papers {topic}` (when topics exist), `memory_map {}`,
  `memory_search {query}`, `remember {text, sources}`.

Memory lives in `vault/memory/`: `core.md` (library map, generated and deterministic, ≤8 KB, regenerated a
few seconds after the library changes; only the app writes it), `findings.md`, and `agents/README.md`.
- `GET /api/memory` → `{"core": markdown, "findings": [{id, date, label:"Added via AI", sources:[{id, page, title,
  exists}], text, edited}] (newest first), "summary": [older findings, one line each], "updated", "label"}`
- `PUT /api/memory/findings/{entry_id}` body `{"text"?, "sources"?}` → the finding (same prose and source
  checks as `remember`; 400 with the reason, 404 for an unknown id)
- `DELETE /api/memory/findings/{entry_id}` → `{"ok": true}`

`remember` rules: text is plain prose of at most 400 characters (no code fences, backticks, code-like lines,
HTML, lists or headings, long non-prose strings); `sources` has 1–6 existing note ids, each optionally with
a page that exists (`"id"`, `"id p. 4"`, `"[[id]] p. 4"`). It appends with the date and "Added via AI".
Duplicates are rejected. Past `RB_FINDINGS_MAX_BYTES` (24 KB) the oldest entries roll into the summary.


## ADDED: Browser-extension annotations (web highlights) and PDF import by URL
Used by the Chrome extension's annotate mode (`extension/annotate.js`). No Claude is involved anywhere here.
- Notes gain `"selector": {"exact", "prefix", "suffix"} | null`: a W3C TextQuoteSelector for a web highlight
  (prefix/suffix are ~32 characters of surrounding page text, whitespace collapsed). Written to the frontmatter only
  when set; anything else normalises to `null`.
- `POST /api/clip` additionally accepts `"selector"` and stores it, plus the page `url`, on the fragment.
- `GET /api/fragments?url=<page url>` → `{"url", "paper": PaperRef|null, "papers": {id: PaperRef},
  "fragments": [{"id","title","quote","body","locator","page","parent","selector","created"}]}`: fragments clipped
  from that page (url compared without `#hash` or a trailing slash), oldest first. `paper` is the note `/api/match`
  finds for the page. `PaperRef = {"id","type","title","authors","year","venue","doi","arxiv","url","has_pdf",
  "citation"}` where `citation` is a plain author-year string, e.g. `Vaswani, A., et al. (2017). Attention Is All You
  Need. NeurIPS. arXiv:1706.03762`. 400 without `url`.
- `POST /api/import/url` body `{"url"}` → 201 Note + `"viewer": "/viewer/<id>"` (+ `"duplicate": true` when the paper
  was already in the library, `"existing": true` when it already had a PDF and nothing was downloaded). The server
  downloads the PDF itself (`app/urlfetch.py`): http/https only, ports 80/443/8080/8443, no credentials in the URL,
  every hop's host must resolve only to public addresses (loopback, private, link-local, CGNAT, reserved and
  IPv4-mapped forms are refused with 403), at most 3 redirects (502 beyond), at most 100 MB (`RB_URL_IMPORT_MAX_MB`;
  413), and the body must start with `%PDF` (415). Then it dedupes (DOI, arXiv id from the URL or text, S2 id) and
  enriches from Semantic Scholar like `/api/import/pdf`; a known paper without a PDF gets this one attached.

## ADDED: Library, profiles and storage
Logic in `app/paths.py`, `app/profiles.py`, `app/migrate.py`, `app/acquire.py`, `app/vaultzip.py`; endpoints in
`app/storage_api.py`. Background: [docs/storage-and-distribution.md](docs/storage-and-distribution.md).
- `GET /api/library` → `{"version", "schema", "app_schema", "vault", "profile", "pinned", "legacy", "source",
  "notices": [str], "data_dir", "supervised", "move_legacy_pending"}`. `profile` is `""` and `pinned` true when the
  server was started with `RB_VAULT`/`--vault`. `legacy`: the library is the pre-0.6 `./vault` in the app folder.
  `supervised`: started by `research-brain serve` (so it can restart itself).
- `GET /api/profiles` → `{"profiles": [{"name","path","exists","notes","current","selected","legacy"}], "current",
  "pinned", "vault", "data_dir", "supervised"}`. `current` = open now; `selected` = opens on the next start.
- `POST /api/profiles` body `{"name"}` → 201 `{"name","path"}`. Names: `[a-z0-9][a-z0-9_-]{0,39}` (400); 409 if it exists.
- `POST /api/profiles/switch` body `{"name"}` → `{"name","path","restarting","restart_required"}`. Writes
  `active_profile` to settings.json. Supervised: the server restarts into it (~1 s; poll `/api/library`, then reload).
  Otherwise `restart_required` is true. 404 unknown profile; 409 when pinned.
- `POST /api/library/move-legacy` → `{"restarting","restart_required","command"}`: the legacy `./vault` is moved to
  `<data dir>/profiles/default` on the next (re)start (copy, verify, rename; the old folder is kept as
  `vault.moved-<date>`). 409 when the library isn't the legacy one.
- `GET /api/settings` → `{"unpaywall_email": str, "auto_open_access": bool, "save_mode": "link" | "pdf"}`; `PUT` the
  same fields (partial). 400 for an invalid email or save_mode. `save_mode`: what the extension saves from a publisher
  page, `"link"` (link + text, the default) or `"pdf"` (link + text + PDF). Stored in `<data dir>/settings.json` (per user, all profiles).
- `GET /api/ui-state` → `{"state": {key: json}, "restore": bool}`; `PUT` body `{"state": {key: json | null}}` merges
  (null removes) → `{"state"}`. Stored in `vault/state/ui.json`; keys `[A-Za-z][A-Za-z0-9_.-]{0,63}`, total ≤ 256 KB
  (413). `static/settings.js` mirrors the app's lasting localStorage keys and `lastNote` here.
- Paper imports by DOI / arXiv id / title / S2 id (`/api/import/lookup`, `/api/import/s2`) start a background
  open-access PDF search when the paper has no PDF (`RB_ACQUIRE=0` or `auto_open_access: false` turns it off):
  arXiv, Semantic Scholar `openAccessPdf`, then Unpaywall by DOI (needs `unpaywall_email`). Downloads use
  `app/urlfetch.download_pdf` (SSRF-guarded), ≤ `RB_OA_MAX_MB` (60) each, ≤ `RB_OA_DEADLINE` (90 s) per paper. The
  response of the import never waits for it; when a PDF arrives, `/api/events` sends `notes-changed`.
- `GET /api/notes/{id}/acquire` → `{"state": "none"|"running"|"found"|"not-found"|"duplicate"|"has-pdf", "at",
  "source"?, "url"?, "sha256"?, "same_as"?, "tried"?: [{"source","url","error"}], "message"?, "has_pdf"}`
  (recorded in `vault/state/acquire.json`). `POST` → 202 `{"state","started"}` searches now (503 when disabled, 400
  for non-papers).
- `POST /api/import/pdf-bytes?source_url=&doi=&title=&note=&keep_pdf=` body: the raw PDF, `Content-Type: application/pdf` (415
  otherwise) → 201 Note + `"open"` (`/#note=<id>`), `"viewer"` when it has a PDF (+ `"duplicate"`, `"existing"`,
  `"attached"`, `"linked"`). `note=<id>`: attach to that paper (400 for a non-paper, 404 unknown; a paper that already
  has a PDF is returned with `"existing"`); a linked paper becomes `source: "pdf"` and its text is re-extracted from
  the PDF (`[[page N]]` markers replace `[[section …]]`). `keep_pdf=0`: a linked paper from a PDF tab: the text
  with its page markers is kept, the file is not. The extension's **Save PDF to
  research_brain**: the browser downloaded the PDF with the user's own access. Limits: ≤ `RB_UPLOAD_MAX_MB` (150; 413),
  must contain `%PDF` in the first 1 KB (415), not empty (400). Dedupe: sha256 of the bytes against every stored PDF,
  then the page URL (`/api/match` rules), DOI (the `doi` parameter, else from the text) and arXiv id (from an arxiv.org
  `source_url` or the text). A known paper without a PDF gets it attached; one with a PDF is returned unchanged.
  `source_url` is kept only if it is http(s) without credentials.
- `GET /api/vault/export?pdfs=true|false` → `application/zip` download of the whole library (notes, pdfs, text,
  state, memory, topics + `research_brain-export.json` manifest; no `.index/.trash/exports/backups/.git`).
- `POST /api/vault/import?profile=NAME` body: the zip, `Content-Type: application/zip` → 201 `{"profile","files",
  "notes","skipped","skipped_count","manifest","path"}`. Always into a NEW profile (409 if it exists). Refused before
  anything is written: absolute paths, drive letters, backslashes, `..`, symlinks (400), no `notes/*.md` (400), entry
  count, per-file/total size and compression-ratio caps (413). Unknown top-level folders are skipped; one wrapping
  folder is stripped. The imported library is migrated to the current schema.
- Every vault has `state/meta.json` `{"schema", "app_version", "created", "migrations": [...]}`. On start, older
  schemas are migrated after a zip backup to `vault/backups/`; a newer schema stops the server with a message.

## ADDED: Paper metadata (identifiers first; app/metadata.py)

```
POST /api/notes/{id}/refresh-metadata  {"keep_edits": true, "title_search": true}   (body optional)
  -> Note + {"metadata": {"found": bool, "source": "crossref"|..., "changed": bool, "errors": [str]}}
     400 for a fragment, 404 unknown id, 503 when RB_METADATA=0. Blocks until done (a few seconds).
```

- Every import (`/api/import/pdf`, `/lookup`, `/s2`, `/url`, `/pdf-bytes`, `/linked`, `/api/clip` creating a paper,
  topic-queue accept, Zotero import) schedules a lookup in the background; the sync endpoints wait up to
  `RB_METADATA_WAIT` (3) seconds, so their response usually carries the resolved note. When a lookup changes a
  note, `GET /api/events` sends `notes-changed` with `{"ids": [id], "reason": "metadata", "source": ...}`.
- Order: DOI → Crossref, else DOI content negotiation (DataCite & others) · arXiv id → arXiv API (its journal DOI's
  Crossref record wins; the arXiv id stays in `arxiv` and `csl.custom.arxiv`) · PMID/PMCID → E-utilities · ISBN →
  Open Library · OSTI / NTRS id → their APIs · no identifier: local translation-server (if running) for the URL,
  then a Crossref title search (near-exact match only) · Semantic Scholar · the page's own record.
- `csl` conventions: `type` CSL 1.0 (`article-journal`, `paper-conference`, `chapter`, `book`, `report`, `thesis`,
  `dataset`, preprints `article` with `genre: "Preprint"`, `archive: "arXiv"`, `number: <id>`); `DOI` lower-case;
  `container-title` + `container-title-short`; article numbers (APS/AIP/IOP `063204`) in `number` and, when the
  record has no page range, in `page`; `ISSN`/`ISBN` one comma-separated string; names `{family, given,
  non-dropping-particle, suffix}` or `{literal}` for collaborations; `custom` holds `arxiv`, `osti`, `ntrs` and
  `rb_edited` (legacy fields you edited, kept across lookups).
- Merge: a record replaces the note's only if its source ranks at least as high (zotero > crossref = datacite >
  pubmed > openlibrary = osti = ntrs > translation-server > arxiv > s2 > page). Zotero-imported papers keep Zotero's
  values; only blanks are filled. `PUT /api/notes/{id}` edits of title/authors/year/venue/abstract/url update
  `csl` too.
- `POST /api/import/linked` also accepts (all optional, from extension/extract.js): `pmid`, `isbn`, `osti`, `ntrs`,
  `volume`, `issue`, `pages`, `issn` (list), `publisher`, `journal_abbrev`, `conference`, `genre` (a CSL type),
  `date`. `POST /api/import/lookup` also accepts a PMID, PMCID, ISBN, OSTI or NTRS link; when Semantic Scholar
  finds nothing, the resolver above is asked.

## ADDED: Citing (CSL formatting, citation keys, .bib/RIS/CSL-JSON, auto-export)
Logic in `app/cite.py` (CSL records, keys, citeproc-py formatting) and `app/bibexport.py` (BibTeX, BibLaTeX, RIS,
CSL-JSON); endpoints in `app/cite_api.py`. Frontend: `static/cite.js` (picker `cite.pick` on **Alt+C**, the panel's
Cite menu, topic tools, Library settings › Citations) and `extension/cite.js` (the popup's Cite tab).
- **Record:** `cite.csl_for(note)` = the note's `csl` property (CSL-JSON, written by the metadata resolvers or by
  hand; normalised) or, without one, a CSL item built from `title`, `authors` ("First Last" / "Last, First"),
  `year`, `venue`, `doi`, `arxiv` (`number` + `archive: "arXiv"`), `url`, `abstract`.
- **Keys:** the note's `citekey` property if set (imported from Zotero 7's citation-key field, `Citation Key:` in
  Extra, or Better BibTeX's `better-bibtex.sqlite`); otherwise `<family><year><first non-stopword>`, lower-case
  ASCII, disambiguated `a`, `b`, … (oldest note first, then id). Keys handed out by `/api/cite/keys`, the exports
  and auto-exports are written to the note (`citekey`, without touching `updated`), so they never change.
- **Scopes** (every format/keys/export call): `ids` (fragments stand for their paper), `topic=<slug>` (its papers),
  `note=<id>` (papers the note links with `[[ ]]` or manual links; a paper includes itself), `all=1` (every paper;
  exports only). At most 5000 items.
- `GET /api/cite/styles` → `{"styles": [{"id", "title", "short", "kind": "builtin"|"user", "class", "format",
  "bibliography", "error"}], "default", "copy", "user_dir"}`. Built-in ids: `american-physics-society` (default),
  `american-institute-of-physics`, `ieee`, `american-institute-of-aeronautics-and-astronautics`, `nature`, `apa`,
  `chicago-author-date`. User styles: `user:<file stem>` from `<vault>/styles/*.csl`.
- `POST /api/cite/styles` body `{"filename", "content"}` (the .csl text) → 201 style entry. 400 for: not XML, a
  DOCTYPE or entity declaration, not a CSL `<style>`, a dependent style, > 1 MB, or a style citeproc-py can't read.
  `DELETE /api/cite/styles/{id}` removes a user style (404 for built-ins).
- `POST /api/cite/format` body `{ids?|topic?|note?, "style"?: id (default: settings), "mode": "citation"|"bibliography",
  "format": "html"|"text"}` → `{"html"?, "text", "entries": [{"id", "html"?, "text"}], "style", "ids"}`. Citation =
  one in-text citation for all items; bibliography = the reference list (numbered in the order given for numeric
  styles; topic/note scopes are ordered by key). HTML is reduced to `div.csl-bib-body > div.csl-entry` with `i b em
  strong sup sub span br` only. 404 unknown style; 422 `{"detail"}` when the style can't format (never a 500).
- `POST /api/cite/keys` body `{ids?|topic?|note?}` → `{"keys": {id: key}, "ids", "latex": "\\cite{a,b}", "pandoc":
  "[@a; @b]"}` (keys are saved). `PUT /api/cite/keys/{id}` body `{"citekey"}` → `{"id","citekey"}`; keys match
  `[A-Za-z0-9_:.+/-]{1,100}` (400); 409 when another note already owns it.
- `GET /api/cite/search?q=&limit=` → papers only `[{"id","title","year","authors" (≤3),"more_authors","venue","citekey"}]`
  (the library's full-text search, recent papers for an empty q; `citekey` is the key it has or would get).
- `GET /api/cite/settings` → `{"style", "copy": "formatted"|"citekey"}`; `PUT` the same (partial; 400 unknown style or
  copy). Stored in `<data dir>/settings.json` (`cite_style`, `cite_copy`).
- `GET /api/export/bibtex?ids=a,b|topic=|note=|all=1&flavor=bibtex|biblatex` (default biblatex),
  `GET /api/export/ris?…`, `GET /api/export/csl-json?…` (CSL-JSON with `id` = key) → a download (`Content-Disposition:
  attachment`, `inline=1` for inline). 400 without a scope or with a bad flavor; 404 unknown topic/note. Types:
  article-journal → `@article`, paper-conference → `@inproceedings`, book → `@book`, chapter → `@incollection`,
  thesis → `@phdthesis`/`@mastersthesis` (genre), report → `@techreport` (BibLaTeX `@report`, `type = {techreport}`),
  arXiv preprints → `@misc` with `eprint`, `archiveprefix`/`primaryclass` (BibLaTeX `eprinttype`/`eprintclass`) and,
  for BibTeX, `howpublished = {arXiv:…}`; webpages → `@misc` (BibLaTeX `@online`).
- `GET /api/cite/auto-exports` → `{"exports": [{"id", "scope": "all"|"topic:<slug>", "flavor", "path", "written"
  (ISO or ""), "error", "entries"}]}`. `POST` body `{"path", "scope", "flavor"}` → 201 (written right away; `error`
  says why not). `DELETE /api/cite/auto-exports/{id}` stops it (the file is left). Kept per library in
  `<data dir>/settings.json` (`cite_auto_exports`, keyed by the vault's path), never inside the vault. A background
  thread rewrites each file when the store changes, a topic file changes or the file watcher reports an outside
  edit (`RB_AUTO_EXPORT=0` turns the thread off); a file is only written when its text changed. Path rules (400):
  absolute, `*.bib`, no `..`, its folder exists, not a symlink, not in the app's folder or the library's internal
  folders, and new/empty or starting with `% Written by research brain`.

