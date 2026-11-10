# research_brain annotator (Chrome extension)

Highlight passages and write notes on research pages (arXiv abstract and HTML versions, journal
sites, blogs) and save them to research_brain, each one filed under its paper with the paper's
citation. PDFs open in research_brain's own viewer, where the same highlight and note tools work.

## No AI, and nothing leaves this computer

- **There is no AI in this extension.** It never calls Claude or any other model, and it has no code
  that talks to research_brain's AI endpoints (chat, research, critique, explain).
- **It sends data only to research_brain on this computer** (`http://127.0.0.1:8765` by default).
  The server address setting accepts only `127.0.0.1` or `localhost`. All requests go through
  `background.js`, which allows only these paths: `/api/status`, `/api/match`, `/api/fragments`,
  `/api/clip` (the popup also uses `/api/search`, `/api/import/url`, `/api/import/pdf-bytes`,
  `/api/import/linked` and `/api/import/linked/prefs`).
- **It loads nothing from the internet.** KaTeX, the LaTeX editor, marked and the fonts are bundled
  in `vendor/`.
- **PDF import downloads on your machine, not in the extension.** "Annotate this PDF in
  research_brain" asks your local server to fetch the PDF from the page you're on (http/https only,
  public addresses only, up to 100 MB, at most 3 redirects).
- **"Save to research_brain (link + text)" reads the page you are looking at, and saves no file.** On a publisher
  article page (one with `citation_*`, Dublin Core or JSON-LD article metadata, or an arXiv page) this is the main
  button. It injects `extract.js` into that tab only when you click. The script fetches nothing: it reads the
  page's citation metadata (title, authors, DOI, journal, year, abstract, PDF link) and the article's main text as
  your browser shows it, turns headings into section markers, keeps math as TeX where the page has it, and keeps
  the reference list apart. The popup posts that JSON to `/api/import/linked` on this computer. On a PDF tab the
  same button reads the PDF in your browser (as "Save PDF" below) and research_brain keeps only its text, page by
  page, not the file. Your paper is then a *linked paper*: link + full text + metadata. Add the PDF later with
  **Save PDF too** (shown after saving, and on the page), or set *Library settings › When saving from a publisher
  page › Link + text + PDF* to make the main button save both.
- **"Save PDF to research_brain" / "Save PDF too" downloads in your browser, with your access.** On a PDF tab, or a
  publisher page that names its PDF (`citation_pdf_url`), the popup injects `grab_pdf.js` into that tab
  only when you click the button. It fetches that one PDF as the page itself would, so your
  subscription or university login applies, checks it starts with `%PDF`, and hands the bytes to the
  popup, which posts them to `/api/import/pdf-bytes` on this computer (up to 150 MB). research_brain
  never sees your cookies or passwords and never downloads anything on your behalf from a paywalled
  site. It is one paper per click: there is no bulk download. Respect each publisher's terms.
- `tests/test_annotate.py` checks that the extension never mentions those AI endpoints or any host
  other than this computer, and that the manifest's permissions stay this narrow.

### Permissions

| Permission | Why |
| --- | --- |
| `activeTab` | Lets the extension touch **only the tab you're on, and only after you click its button or press its shortcut**. There are no broad host permissions and no always-on content script. |
| `scripting` | Injects the annotate script (`annotate.js`) into that tab when you turn annotate mode on, and reads the current selection for a quick clip. |
| `storage` | Remembers the server address, whether the notes list is open, and an unsaved quick-clip draft. Stored in Chrome on this computer. |
| `contextMenus` | The right-click "Add to research_brain…" item, the only way to clip from Chrome's PDF viewer. |
| Host `http://127.0.0.1:8765/*`, `http://localhost:8765/*` | Talk to research_brain on this computer. Nothing else. |

`note.html` (the note popover) is the only web-accessible file, with `use_dynamic_url`, so pages can't
fingerprint the extension by its id.

## Install

1. Start research_brain (`research-brain`, or `./run.sh` in a checkout) so it is listening on `http://127.0.0.1:8765`.
2. Open `chrome://extensions` and turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and pick this `extension/` folder. With an installed app, `research-brain where`
   prints the extension folder (it ships inside the package, `.../site-packages/research_brain/extension`).
   Copy it somewhere stable first (for example `~/research_brain-extension`), because an update replaces
   the package folder.
4. Optional: pin the extension (puzzle-piece icon, then the pin) so its icon sits in the toolbar.

The extension's version (`manifest.json`) is released together with the app. When the app updates,
reload the extension (or copy the new folder over the old one and reload). An unlisted Chrome Web Store
listing would update it automatically; see docs/storage-and-distribution.md.

No build step. After editing any file here, click the reload icon on the extension's card, then
reload the pages you want to annotate.

## Annotate a web page

1. Click the toolbar icon and choose **Annotate this page**, or press **Alt+Shift+A**
   (⌥⇧A on a Mac).
2. Select text. A small toolbar appears: **Highlight** (H) or **Highlight + note** (N).
3. **Highlight + note** opens a compact note box under the selection. Type `\` for LaTeX
   autocomplete, **⌘M** / **Ctrl+M** wraps the selection in `$…$`, and **Symbols** opens a palette;
   a KaTeX preview appears once there is math. **⌘↵** / **Ctrl+Enter** saves, **Esc** cancels.
4. Saved passages are drawn as a red wash with a 1px red underline. Click one to see your note, then
   **Copy citation** (copies the quote, its locator and a formatted citation) or **Open in
   research_brain**.
5. **Notes** (bottom right) opens a quiet list of this page's notes. Click one to scroll to it.

Where it's filed: the paper matched to the page (`/api/match`: arXiv abs/pdf/html URL, doi.org link
or exact URL). If none matches but the URL has an **arXiv id or DOI**, the paper is imported first
(title, authors, year, venue, DOI/arXiv from Semantic Scholar, via the server), so the citation is
always there. Otherwise a source note titled with the page title is created, and later notes on the
same page go under it.

**Coming back to a page:** turn annotate mode on again and your highlights reappear. Each one is
stored with a text-quote selector (the exact text plus about 32 characters before and after). The
script finds it again by exact text, then case-insensitively, then by matching its beginning and end
if the middle changed slightly, using the surrounding text to pick between repeats. A highlight whose
text is no longer on the page stays in the list, marked "Not found on this version of the page".

## Save a paper (link + text)

Click the toolbar icon on the article's page and choose **Save to research_brain (link + text)**. What the
extraction handles well and less well:

- **Built for:** pages with Highwire `citation_*` tags (most journals; Google Scholar indexes them), Dublin Core or
  JSON-LD `ScholarlyArticle` metadata, and arXiv HTML (LaTeXML) pages. It knows the article containers of
  Springer Nature, Wiley, Taylor & Francis, ACS, AIP, IOP, APS, Elsevier ScienceDirect, MDPI, PubMed Central and
  IEEE's full-text view, and elsewhere falls back to the block with the most paragraph text (readability-style).
  The tests use fixture pages (a `citation_*` publisher page, arXiv abstract and HTML pages, a JSON-LD-only page),
  not the live sites, whose markup changes; if a site comes out wrong, the text is still editable in the vault.
  Headings become sections (`3.2 Results` is cited as §3.2), MathML, KaTeX and MathJax 2 math is kept as
  `$TeX$`, figure and table captions are kept, the reference list goes apart.
- **Abstract only:** landing pages that show just the abstract (an arXiv `abs` page, or a journal page when you
  have no access). The popup says so; open the HTML or PDF version and save again, and the full text replaces
  the abstract on the same paper.
- **Less good:** pages that load the article text later behind "Show full text" buttons (save after it shows),
  articles split over several pages (only the one you are on is saved), MathJax 3 pages (math kept as plain text),
  and sites without any citation metadata (the title falls back to the page's heading; fill in the rest in the
  app). Tables are left out except their captions.

## Annotate a PDF

Chrome's built-in PDF viewer can't be scripted by extensions (a Chrome limitation). When the tab is a
PDF (URL ending `.pdf`, `arxiv.org/pdf/…`, or a page served as `application/pdf`), the popup offers
**Annotate this PDF in research_brain**. Your local server downloads it, dedupes it against your library
(DOI, arXiv id, Semantic Scholar id), fills in metadata, and opens `/viewer/<id>` in a new tab, with
the same **Highlight** / **Highlight + note** toolbar. If you already have that paper with its PDF,
the viewer just opens.

## Quick clip (still there)

- **Right-click a selection, then "Add to research_brain…"** opens the clip form prefilled with the
  selection, URL and title (also works in Chrome's PDF viewer).
- **Alt+Shift+S** with text selected opens the same form. **Quick note** in the popup opens it empty.
- In the form you can edit the quote, set a locator (`p.4, Eq. 7`), write a note, and choose where
  it's filed (*Automatic*, the matched paper, or any paper you search for).

## Keyboard shortcuts

| Where | Keys | What it does |
| --- | --- | --- |
| Any page | **Alt+Shift+A** (⌥⇧A) | Turn annotate mode on or off for this page |
| Any page | **Alt+Shift+S** (⌥⇧S) | Open the popup; with text selected, the quick-clip form |
| Selection toolbar | **H** | Highlight the selection |
| Selection toolbar | **N** | Highlight and write a note |
| Selection toolbar | **Esc** | Dismiss the toolbar and the selection |
| Note box | **⌘↵** / **Ctrl+Enter** | Save the note |
| Note box | **Esc** | Cancel |
| Note box | **\\**, **Tab**, **⌘M** / **Ctrl+M** | LaTeX autocomplete, next snippet slot, wrap in `$…$` |
| Highlight card | **Esc** | Close |
| Quick-clip form | **⌘↵** / **Ctrl+Enter** | Save |

Change the Chrome-level shortcuts (Alt+Shift+A and Alt+Shift+S) at `chrome://extensions/shortcuts`.

## Settings

Click **Settings** in the popup to change the server port. Only `http://127.0.0.1:PORT` or
`http://localhost:PORT` is accepted. If you change the port, also change it in `host_permissions` in
`manifest.json` and reload the extension.

## Troubleshooting

- **"research_brain isn't running"**: start it with `./run.sh`, then press *Try again*.
- **"Chrome doesn't allow extensions on this page"**: Chrome blocks extensions on `chrome://` pages, the
  Web Store and some other built-in pages.
- **Highlights don't reappear on a page that loads its text late**: annotate mode retries for a few
  seconds as the page renders. If the text changed, the note is listed as not found.
- **"The extension was reloaded. Refresh this page."**: after reloading the extension, reload the tab.
- The server only accepts requests from this extension and its own pages (other websites get 403).
