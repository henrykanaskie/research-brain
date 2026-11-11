# Security

research_brain runs on your own computer, for you alone. Each lab member installs their own copy and puts
in their own papers. The notes are not secret, but three things must hold:

1. **Nothing leaves your computer** except the lookups listed under [What goes over the network](#what-goes-over-the-network).
2. **Nothing but you can change your library**: not websites you visit, not other people on the same
   computer or network.
3. **Claude never acts outside the app.** It can read your library and write one short note to its memory.
   It can't browse, run code, open files or reach the internet.

This page explains what we defend against, how, and what is still up to you.

## What we defend against

| Threat | Example | Main defence |
| --- | --- | --- |
| A web page in your browser | A site posts a hidden form to `http://127.0.0.1:8765/api/notes`, or loads `/api/graph` in a `<script>` tag | Origin and fetch-metadata checks, JSON-only bodies, CORS, same-origin resource policy |
| DNS rebinding | `evil.example` points its DNS at 127.0.0.1, so the browser treats our server as that site | Host-header allowlist on every request |
| Other people or programs on the same computer | Another account reads `~/research_brain/vault`, or plants a `CLAUDE.md` in `/tmp` | Private file permissions, a private working folder for Claude |
| Other devices on the network | Someone on café Wi-Fi opens your library | The server listens on 127.0.0.1 only (unless you set `RB_HOST`) |
| Malicious files you import | A PDF built to hang or exhaust the parser; a note file with a YAML "billion laughs"; a Zotero attachment path pointing at `~/.ssh` | Parser in a limited child process, size and time limits, path checks |
| Malicious URLs you import | "Annotate this PDF" on a link that redirects to your router or a cloud metadata address | SSRF guard with a pinned, checked IP address for every hop |
| Prompt injection in paper text | A paper says "ignore your instructions and run `curl …`" | Claude has no tools that could do it; the tool guard kills the run if it does |
| Rendered content (XSS) | A note or Claude's answer contains `[x](javascript:…)` or `<img onerror=…>` | Markdown renderer escapes HTML and checks link schemes; Content-Security-Policy |
| A malicious browser extension or web page talking to ours | Another extension, or a page, messages the research_brain extension | No `externally_connectable`; sender checks; the server accepts only our extension's id |
| Supply chain | A compromised new release of a Python package | Pinned, tested versions (`constraints.txt`); vendored front-end code; no CDNs |

## The local server (app/security.py)

The app has no password. It trusts whoever can reach `127.0.0.1:8765`, so the browser is the main way in.
Every request passes these checks before any endpoint runs:

- **Host allowlist (DNS rebinding).** The `Host` header must be `127.0.0.1`, `localhost` or `[::1]`
  (any port), or the address in `RB_HOST`. Anything else gets `421`. This applies to every path,
  including the static files and the PDFs. Extra names: `RB_ALLOWED_HOSTS=name1,name2`.
- **Origin.** If a request carries an `Origin`, it must be this app's own address and port, or the
  research_brain Chrome extension (its id is fixed by the `key` in `extension/manifest.json`; add others
  with `RB_EXTENSION_IDS`). Other sites, other ports on localhost (other local apps), sandboxed frames
  (`Origin: null`) and other extensions get `403`, also for reads and CORS preflights.
- **Fetch metadata.** A browser request without an Origin that says `Sec-Fetch-Site: cross-site` or
  `same-site` is refused unless it is a top-level navigation (you clicking a link to the app). This stops
  `<img>`, `<script>` and `no-cors` reads, which carry no Origin.
- **Body type.** Anything that changes data (POST, PUT, PATCH, DELETE with a body) must be
  `application/json`, or `multipart/form-data` on the two PDF upload endpoints, or the one fixed type of
  a raw-body endpoint (`application/pdf`, `application/zip`). `text/plain` and form-encoded bodies,
  which a web page can send without a CORS preflight, get `415`.
- **Body size.** JSON at most 16 MB (`RB_MAX_JSON_MB`), uploads at most 300 MB (`RB_MAX_UPLOAD_MB`).
  `Content-Length` is checked first; bodies without one are counted as they arrive.
- **Response headers.** `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Resource-Policy: same-origin` (other sites can't embed your PDFs or JSON),
  `X-Frame-Options: SAMEORIGIN`, and on HTML pages a Content-Security-Policy: scripts only from the app
  (its two inline scripts are allowed by hash), no plugins, no connections, images or fonts from the
  internet. A remote image written into a note is not loaded.
- Tools without a browser (the iOS Shortcut, `curl`, Research mode's tool bridge) send no `Origin` and
  no `Sec-Fetch-*` headers, so they work as before. They still need a correct Host and a JSON body.
- The Swagger page (`/api/docs`) is off: it loads scripts from a CDN. `API.md` documents every endpoint.

## Files on disk

- The server sets its umask to `077`, so every file it writes is readable only by you (`0600`, folders
  `0700`), and it makes the vault folder `0700` at startup. Set `RB_KEEP_UMASK=1` to keep your own umask.
- Every id that becomes a file name (notes, PDFs, extracted text, topics, memory findings, export
  selections) must match `[a-z0-9][a-z0-9-]*`, so `../` can't reach outside the vault. Memory writes
  only go to fixed file names inside `vault/memory/`.
- Zotero: stored attachments must resolve inside Zotero's `storage/<key>/` folder; linked files must be
  real `.pdf` files that start with `%PDF`. Zotero itself is never modified (the app reads a copy of its
  database).
- No note content is logged. The server log (`~/Library/Logs/research_brain.log` with the login agent)
  holds only start-up messages and errors. There is no telemetry and no analytics.

## Imported files and URLs

- **Metadata API requests** go through `app/urlfetch.fetch`: https only; the host must be in the resolver's fixed
  list (every redirect hop is checked against it, so a redirect elsewhere is refused, not followed); the name
  must resolve to public addresses only and the connection is pinned to a checked address (no DNS rebinding);
  `Accept-Encoding: identity` (compressed answers refused), at most 4 MB and 25 s per answer. XML (arXiv) with a
  DOCTYPE or ENTITY declaration is refused. `urlfetch.fetch_local` reaches only `127.0.0.1:<port>` with a fixed
  path (the translation-server); a page URL is handed to it only if it passes the same public-address check.
  Answers are cached as data in `.index/metadata_cache.json`; fields are stripped of markup before they are
  stored, and the interface renders them as text.

- **PDFs** are parsed in a separate, short-lived process with a time limit (`RB_PDF_TIMEOUT`, 120 s), a
  CPU limit and, on Linux, a memory limit (`RB_PDF_MAX_MEM_MB`, 2048). A PDF that hangs the parser or
  inflates into gigabytes only kills that process, and the import fails with a message. At most 80 pages
  and 4 million characters of text are kept.
- **Note and topic files** (Markdown with YAML properties, maybe edited elsewhere): YAML is loaded with
  the safe loader, at most 1 MB, and refused if its aliases expand into too much data or refer to
  themselves. A refused file shows a warning and is never overwritten.
- **Import by URL** (`/api/import/url`, the extension's "Annotate this PDF"; app/urlfetch.py):
  - only `http`/`https` on ports 80, 443, 8080 or 8443; no user names or passwords in the URL;
  - the host name is resolved once per hop and every address must be public (not loopback, private,
    link-local, CGNAT, multicast or reserved). The connection then goes to one of exactly those
    addresses, with the real host name kept for `Host`, TLS SNI and certificate checking. A DNS answer
    that changes between the check and the connection can't redirect it, and the connected peer is
    checked again;
  - redirects are followed by hand (at most 3), each one checked the same way;
  - no proxy is used (a proxy would look up the name itself, behind our check);
  - 10 s to connect, 60 s per read, 180 s in total (`RB_URL_IMPORT_TIMEOUT`), 100 MB at most
    (`RB_URL_IMPORT_MAX_MB`); compressed responses and non-PDF content types are refused; the file must
    start with `%PDF`.
- **Linked papers** (`POST /api/import/linked`, the extension's "Save to research_brain (link + text)"): JSON only
  (the body-type rule above), from this app or the pinned extension id (the Origin rule); the URL must be http(s)
  without credentials, the text at most 5 million characters (`RB_LINKED_MAX_CHARS`), control and invisible
  characters are stripped and every field is capped. The server downloads nothing for it (no PDF, no page): the
  text comes from the page the user is looking at, read in their own browser session; their credentials never
  reach research_brain.
- **Vault zip import** (app/vaultzip.py) refuses absolute paths, `..`, symlinks and zip bombs (entry
  count, sizes and compression ratio are capped).
- **Citation styles** (`.csl` files you add in Library settings › Citations, or drop into `<vault>/styles/`;
  app/cite.py): parsed with `defusedxml` with DTDs, entity declarations and external references forbidden (so no
  XXE file reads and no "billion laughs"), at most 1 MB, and only a CSL `<style>` root is accepted; citeproc-py's
  own parser then runs with network access off. A style file is data, never code: it can only change how
  citations look. Formatted output is reduced to a few inline tags (`i b em strong sup sub span br` inside
  `div.csl-entry`) before it reaches the page or the clipboard. Style ids are file stems from a fixed folder, so a
  style name can't point elsewhere.
- **Auto-updating .bib files** (app/cite_api.py): each auto-export writes exactly one file, chosen by you: an
  absolute `*.bib` path without `..` whose folder already exists, outside the app's program folder and the
  library's internal folders. Symbolic links are refused (checked when added and again before every write), and
  an existing file is only replaced if research brain wrote it (it starts with `% Written by research brain`), so
  a `.bib` you wrote by hand is never overwritten. Writes go to a temporary file in the same folder, then an atomic
  rename; the file keeps its permissions. The list of auto-exports lives in the per-user `settings.json`, not in the
  library, so a library zip from someone else can't make your computer write anywhere.

## The web pages (XSS)

Markdown is rendered by `static/latex-editor.js` (also used by the viewer, the extension and exported
sites). Raw HTML in a note is shown as text, never run. Links and images may only be `http`, `https`,
`mailto` or relative; the scheme is judged after removing the tabs, newlines and control characters a
browser would ignore, so `java⇥script:` is refused too. KaTeX runs with `trust: false`, so `\href`,
`\url` and `\html…` can't create links or HTML. Exported sites escape every field and only link `http(s)`
source URLs. The Content-Security-Policy is the second layer: even if a bug let markup through, inline
scripts and event handlers would not run.

## AI agents (app/agents/)

research_brain uses whichever AI tool the user already has: an agent CLI they are signed in to, or a
model server on this computer. It never uses an API key and has no model access of its own. The same
rule applies to every backend: **the model gets no tools at all**. Answers are text only; no shell,
files, web, browser or MCP. Each adapter (`app/agents/<name>.py`) follows the rules in
`app/agents/base.py`:

- **Empty private working folder** per call (the same `0700` cache folder as Claude below, checked for
  group/world-writable parents), so no `AGENTS.md`, `GEMINI.md`, `CLAUDE.md` or project config can be
  planted next to it.
- **Clean environment**: `NODE_OPTIONS`, `NODE_PATH`, `LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`,
  `PYTHONPATH`, `BASH_ENV` and the app's `RB_AGENT_*` values are dropped; telemetry and update checks are
  switched off where a variable exists. Adapters may also drop API-key variables so the CLI uses the
  user's own sign-in.
- **No user or project config** that could add tools, MCP servers, hooks or instructions, wherever the
  CLI allows switching it off (flags, an empty config folder). Each adapter's docstring says what its CLI
  allows and what it doesn't.
- **Hard timeout**, and the whole process group is killed on timeout, on an error, or when the page goes
  away mid-answer.
- **Stream guard**: output is parsed as it arrives; any tool-use event the CLI reports kills the process
  (`ToolGuardError`). Local servers are sent no `tools` field, and a reply with tool calls is refused.
- **Local model servers** (Ollama, LM Studio, any OpenAI-compatible server) are reached only on
  `127.0.0.1`, `localhost` (connected as `127.0.0.1`) or `::1`; any other address is refused when it is
  saved and again when it is used. These requests ignore `HTTP(S)_PROXY`, follow no redirects, and send
  no Authorization header.

**Research mode without tools.** Claude keeps its MCP-based Research path (below). Every other agent
uses the app-driven loop in `app/agents/research.py`: the model replies with one small JSON object
(`search`, `read`, `citations`, `similar`, `memory`, `remember` or `answer`); the app parses it (it is
never executed), checks it with the same argument validation as the MCP tools (`agent.validate_args`),
runs the same read-only tool code, and sends the result back in the next prompt. Unknown actions or
fields are refused, at most 6 lookups and 150 s are allowed, and at most 2 findings may be saved per
question (still subject to the memory rules below). So no agent ever gets tools, not even ours.

**What each agent sends to its provider.** The prompt research_brain builds: the instruction, the
paper text, summaries, notes, passages or lookup results needed for that one request, and (for chat)
the recent conversation. A CLI agent sends that to its own provider under the user's account (Claude
Code to Anthropic, Codex CLI to OpenAI, Gemini CLI to Google, Copilot CLI to GitHub, OpenCode and
Cursor to the provider the user set up), plus whatever the CLI itself reports (see each tool's own
privacy terms). Ollama and local OpenAI-compatible servers keep everything on this computer.

## Claude (app/claude_cli.py, app/agent.py, app/agent_mcp.py)

research_brain reaches Claude only through your own `claude` CLI, on your subscription. There is no API
key and no other model access.

Every call (critique, Quick answers, explanations, recall checks and Research) runs `claude -p` with:

| Flag | Effect |
| --- | --- |
| `--tools ""` | no built-in tools (no Bash, Read, Write, Edit, WebFetch, WebSearch, …) |
| `--strict-mcp-config` | no MCP servers from your user or project settings |
| `--no-chrome` | the Claude in Chrome integration stays off |
| `--disallowedTools mcp__claude-in-chrome__* …` | integrations that attach themselves are denied by name |
| `--permission-mode dontAsk` | any tool that isn't pre-approved is denied, never prompted |
| `--setting-sources ""` | no user, project or local settings: no hooks, no plugins from settings, **no CLAUDE.md** |
| `--disable-slash-commands` | no skills or custom commands |
| `--no-session-persistence` | nothing is saved as a resumable session |

and from a **private, empty working folder** (`~/Library/Caches/research_brain/` on macOS,
`~/.cache/research_brain/` on Linux, `0700`). The CLI reads `CLAUDE.md` from its working folder and
every folder above it, so a working folder under a shared folder such as `/tmp` would let another user on
the computer plant `/tmp/CLAUDE.md` and steer Claude. The app checks that no folder above its working
folder is writable by others (otherwise it uses a private temp folder), and `--setting-sources ""`
switches CLAUDE.md loading off anyway. Both were verified against Claude Code 2.1.

The CLI's environment drops `ANTHROPIC_API_KEY` (so your subscription is used), `NODE_OPTIONS` and
`NODE_PATH` (which could load code into the CLI) and the app's own `RB_AGENT_*` values, and sets
`ENABLE_CLAUDEAI_MCP_SERVERS=false` (no claude.ai connectors) and
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`.

**Research mode** additionally gets exactly one MCP server, research_brain's own (`--mcp-config`, a
generated file readable only by you), with only its tool names pre-approved (`--allowedTools mcp__rb__…`,
no wildcards), `--max-turns 8` and a 45 s budget.

**The tool guard (fail closed).** All calls use `--output-format stream-json`, whose first event lists
the tools the session really has. Before reading anything else, the app compares that list with what the
call may have (nothing for Quick and critiques; only the `mcp__rb__*` names for Research). Anything else,
output before that list, or a call to a tool outside the set, and the process (and its process group, so
the MCP server too) is killed at once. Nothing is run.

**The tool bridge.** `agent_mcp.py` (standard library only, started with `python -I`) forwards each tool
call to `POST /api/agent/tools/<name>` with a random per-request token. The endpoint answers only while
that Research request runs, only to this computer, and calls back on the port the server's own socket is
bound to (never a port taken from a request's Host header). Every argument is checked first: only the
tool's known parameters, of the right type, strings at most 500 characters, at most 4 queries and 6
sources.

**Tools** are read-only except `remember`: `search`, `search_notes`, `read_note`, `read_pages` (at most 3
pages), `citations`, `similar`, `recent`, `list_topics`, `topic_papers`, `memory_map`, `memory_search`.

**Memory (vault/memory/).** `core.md`, the library map, is written only by the app. `remember` appends
to `findings.md` and accepts only plain prose of at most 400 characters (no code, backticks, HTML, lists,
headings, long tokens or symbol-heavy text) with 1–6 sources that exist in your library. Entries are
labelled "Added via AI"; you can edit or delete them in the Memory view. Past a size cap the oldest are
folded into a summary.

## The Chrome extension (extension/)

- No AI. It talks only to research_brain on this computer (`127.0.0.1` / `localhost`; the settings field
  refuses anything else), and its own pages' CSP allows connections nowhere else.
- Permissions: `activeTab`, `scripting`, `storage`, `contextMenus`, and host access to
  `127.0.0.1:8765` / `localhost:8765` only. No content scripts: `annotate.js` is injected only when you
  click the toolbar button or press the shortcut.
- Saving an article as link + text injects `extract.js` into the tab you are on, only when you click the button. It
  reads that page's metadata and text and fetches nothing; the popup sends the result only to this computer.
- The popup's Cite tab only reads the current tab's URL (to pick `\cite` on Overleaf, formatted text on Google
  Docs); it injects nothing and sends the URL nowhere. Copies go to your clipboard only.
- No `externally_connectable`, so web pages can't message it; every message handler checks that the
  sender is the extension itself; the background relays only four API paths for the injected script.
- The annotation UI lives in a closed shadow root, so the page's scripts can't read the notes it shows.
- The id is pinned by the public `key` in the manifest, so the server can accept exactly this extension.
  (Reloading after this change gives the extension a new id once; its server setting resets to the default.)

## What goes over the network

- **Semantic Scholar, arXiv, Crossref**: DOIs, arXiv ids and titles, when you add a paper, open
  Discover, or the topic queue refreshes. Set `S2_API_KEY` if you have one.
- **Metadata lookups** (`app/metadata.py`, when a paper is added or you click Refresh metadata): only these
  hosts, over https: `api.crossref.org`, `doi.org` (and the registration agencies it redirects to:
  `data.crossref.org`, `api.datacite.org`, `data.datacite.org`), `export.arxiv.org`, `eutils.ncbi.nlm.nih.gov`,
  `openlibrary.org`, `www.osti.gov`, `ntrs.nasa.gov`, `api.semanticscholar.org`. What is sent: the identifier
  (DOI, arXiv id, PMID/PMCID, ISBN, OSTI or NTRS id) or, for a paper without one, its title (Crossref and
  Semantic Scholar title search). Never your notes, the paper text or the page you were on. The User-Agent names
  the app and, if you set a contact email in Library settings, includes it (`mailto:`; NCBI also gets it as its
  `email` parameter). `RB_METADATA=0` turns all of this off.
- **A Zotero translation-server on this computer**, only if one answers on `127.0.0.1:1969`
  (`RB_TRANSLATION_SERVER_PORT`): the URL of a page that had no identifier. That server then fetches the page
  itself.
- **Your AI agent's provider**, through the CLI you picked (Anthropic for Claude Code, OpenAI for Codex,
  Google for Gemini, GitHub for Copilot, ...): the paper text and notes needed for the question you asked.
  Nothing, if you use Ollama or another local model server.
- **Hugging Face**, once: the embedding model download (`sentence-transformers`), unless `RB_EMBEDDER=tfidf`.
- **The URL you import**, when you use "Annotate this PDF" / import by URL.
- Nothing else. The interface loads no CDN, font or analytics URL (`tests/test_offline.py` and the
  browser tests fail if it tries).

## Dependencies

`requirements.txt` gives the ranges; `constraints.txt` pins the exact, tested version of every runtime
package, and `run.sh` installs with it (falling back to the ranges only if a pin has no build for your
Python or OS). All front-end code (KaTeX, marked, force-graph, PDF.js, fonts) is vendored in
`static/vendor/` at fixed versions. The `mcp` SDK is only used with `RB_AGENT_MCP_SDK=1`. Citation formatting uses
`citeproc-py` (with `lxml`) and `defusedxml`; the CSL styles and the en-US locale are vendored in `app/csl/`
(CC BY-SA 3.0), so formatting never downloads anything.

## What is still up to you (limitations)

- **Anyone who can run programs as you can read and change your library.** Local file permissions protect
  you from *other* accounts, not from software you run yourself (including other browser extensions with
  broad host permissions, which can read any local page).
- **`RB_HOST` exposes the app, with no password, to that network.** Only use it on a network you trust
  (e.g. Tailscale). The Host and Origin checks still apply, and the Research tool endpoint still needs a
  live token from a loopback client.
- **Prompt injection can still steer what Claude writes**, in an answer or a memory finding. It can't
  give Claude new abilities. Findings are labelled, sourced and editable.
- **The tool guard trusts the CLI's own report of its tools.** If a future CLI attached tools without
  reporting them, the deny flags and `dontAsk` would still apply. Managed (admin) policy settings for
  Claude Code still apply, by design.
- **Exported sites** have no CSP of their own (they are plain files you share). Their text is escaped and
  links are checked, but a remote image written in a note will load when someone opens the export.
- **The extension's note popup** (`note.html`) is web-accessible so it can sit on the page; its URL is
  per-session and the data it reads is the selection on that same tab.
- **URL import doesn't use your proxy.** Behind a proxy that blocks direct connections, use the PDF's
  "Download" and drop the file instead.
- **PDF memory limits** are enforced on Linux; on macOS only the time and CPU limits apply.

## Tests

`tests/test_security.py` covers the Host allowlist, Origin and fetch-metadata checks, JSON-only bodies,
size limits, headers and CSP hashes, path traversal, YAML bombs, the PDF parser's time and memory limits,
URL import pinning and content checks, the Claude CLI's flags, working folder and environment, agent tool
arguments, renderer XSS (both copies of the renderer, under Node), exported-site escaping and file
permissions. `tests/e2e/test_csp_e2e.py` runs the app and the PDF viewer in Chromium with the CSP enforced
and fails on any violation. `tests/test_agent.py` covers the tool guard against a fake `claude`.
`tests/test_cite.py` covers style-file validation (XXE, entity expansion, size, dependent styles), HTML
sanitising of formatted citations, and auto-export path rules (relative paths, `..`, missing folders, the app
folder, symlinks, hand-written .bib files).


The exact command line, lockdown method, version verified and remaining risk for each command-line agent (Codex, Gemini, Copilot, OpenCode, Qwen Code, Cursor) are in [docs/agents.md](docs/agents.md).
