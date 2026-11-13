# research brain

A research library that lives on your own computer. Drop in the papers you read (PDFs, DOIs, arXiv links),
and research brain shows them as a map: papers that are about the same thing sit near each other, and lines
show how they connect. You highlight passages, write notes and short summaries in your own words, link
ideas together, and ask questions of everything you have read.

If you want, an AI you already have (or a free one) can critique your summaries, explain why two papers are
connected, and answer questions about your library with page numbers. It never needs an API key, and
research brain itself costs nothing. Your library is plain files on your computer: nothing is uploaded, and
the ideas you mark private never leave it.

- **New here?** Follow [Install it](#install-it-no-coding-needed) below, step by step. No coding needed.
- **Want the free AI?** See [Add an AI (optional, can be free)](#add-an-ai-optional-can-be-free).
- **Something not working?** See [If something goes wrong](#if-something-goes-wrong).

## Install it (no coding needed)

### What you need

| | |
| --- | --- |
| A computer | a Mac (macOS 11 or newer), a Windows 10 or 11 PC, or Linux |
| Free space | about **300 MB**, plus room for your PDFs |
| Internet | for the install (a few minutes); afterwards it works offline, except looking papers up |
| Anything else? | **No.** You don't need Python, conda, Anaconda or any coding tools: the installer brings what it needs and keeps it separate from anything else on your computer. |

### Step 1: get the research brain folder

- **From your lab** (the usual way): open the shared folder your lab keeps research brain in (a synced
  drive such as OneDrive, Box or Google Drive, or a network folder). You'll install from there.
- **From GitHub** (if you have access): open the research brain page, click the green **Code** button,
  choose **Download ZIP**, then double-click the downloaded ZIP to unpack it.

### Step 2: run the installer

**On a Mac**

1. In the folder, double-click **`Install-research-brain.command`**.
2. A Terminal window opens and shows the install. Keep it open; the first time takes a few minutes.
3. When it says **Done**, research brain opens by itself. You can close the Terminal window.

If the Mac says it *can't be opened* or *Apple could not verify* it: open **System Settings › Privacy &
Security**, scroll down, and click **Open Anyway** next to the file's name; then double-click it again.
If that doesn't appear, or it says *permission denied*, use Terminal instead:

1. Open **Terminal**: in Finder, **Applications › Utilities › Terminal**.
2. Type `bash ` (the word bash and a space; don't press Return yet).
3. Drag `Install-research-brain.command` from the folder into the Terminal window, then press Return.

**On Windows**

1. In the folder, double-click **`Install-research-brain.cmd`**.
2. If Windows says *Windows protected your PC*, click **More info**, then **Run anyway**.
3. A black window shows the install. Keep it open; the first time takes a few minutes. When it says
   **Done**, research brain opens by itself, and you can close the window.

**On Linux**

Open a terminal in the folder (right-click › *Open in Terminal*) and type `bash install.sh`, then press
Return.

### Step 3: open it

From now on, open research brain like any other app:

- **Mac:** Launchpad, or the Applications folder in Finder. To keep it in the Dock, right-click its icon
  while it's open › **Options › Keep in Dock**, then open it from there.
- **Windows:** the Start menu, under *research brain*.
- **Linux:** your app menu.

It opens in about a second. The first time, a short tour shows you around. On a Mac, research brain has its
own window with no title bar: drag the window by the empty space in its top bar, and close it with the
red button (or **⌘ Q**). Closing the window stops research brain (on Windows and Linux, a few minutes
after you close it); nothing runs in the background and nothing starts when your computer starts.

### Step 4 (optional): add the Chrome extension

The extension saves papers, highlights and quotes from web pages (arXiv, journal sites, PDFs open in Chrome)
straight into research brain. It isn't in the Chrome Web Store; it comes in the research brain folder, and you
add it once:

1. In Chrome, go to `chrome://extensions` (type it in the address bar).
2. Turn on **Developer mode** (the switch in the top right).
3. Click **Load unpacked** and choose the **`extension`** folder inside the research brain folder.
4. Click the puzzle-piece icon in Chrome's toolbar and pin **research_brain**, so its button stays visible.

research brain has to be open for the extension to work: it only talks to research brain on your computer.
To use it, on a paper's page click the toolbar button and **Save to research_brain**; to clip a passage, select
it, right-click and choose **Add to research_brain…**; to highlight a page, press **Alt+Shift+A**. After you
update research brain, press the reload arrow on the extension's card in `chrome://extensions`. More in
[Chrome extension](#chrome-extension-web-clipping).

### Step 5 (recommended): free keys for looking papers up

research brain finds papers, citations and related work through two free services, **OpenAlex** and
**Semantic Scholar**. Without a key, each one shares a small allowance with lots of other people (OpenAlex:
everyone on your network; Semantic Scholar: everyone), and when it runs out, adding or searching for papers
fails with *rate limited* or *couldn't reach*. A free key of your own fixes that. These keys are only for
looking papers up, not for the AI, and they cost nothing.

**OpenAlex** (do this one first):

1. Go to [openalex.org](https://openalex.org) and sign up (free; an email is enough).
2. Open [openalex.org/settings/api](https://openalex.org/settings/api) and copy your API key.

**Semantic Scholar:** open [semanticscholar.org/product/api](https://www.semanticscholar.org/product/api#api-key-form)
and scroll to *Request a Semantic Scholar API Key*. Each person asks for their own key. Semantic Scholar
favours academic, nonprofit and government requests and reviews each one by hand, so the key comes by email,
usually after a few days. Here is what to put in each field:

| Field | What to put |
| --- | --- |
| First name, Last name | Your name. |
| Email | Your university or work email (they ask for an academic or corporate address, not Gmail). |
| Affiliation | Your university and lab, e.g. *Example University, Department of Physics*. |
| Affiliation website URL | Your lab's or department's web page (or the university's home page). |
| Country/Region | Where you are. |
| Application use | **Private**: the key is only for your own copy of research brain on your own computer. |
| How do you plan to use Semantic Scholar API in your project? (50 words or more) | Paste the paragraph below, changing the parts in [brackets]. |
| Which endpoints do you plan to use? | Paste the endpoint list below. |
| How many requests per day do you anticipate using? | `Usually under 500; up to about 3,000 on a day I import a large library.` |
| The five checkboxes | Tick them all once you've read the license agreement they link to. research brain already makes unauthenticated requests for you, keeps background jobs to about one request a second and backs off when it's rate-limited (the third box), and you use the key every time you add or look up papers (the 60-day box). |

**The paragraph for "How do you plan to use…"** (about 140 words; edit the bracketed parts):

> I am a [PhD student in physics at Example University]. I use research brain, a free, non-commercial
> personal research library that runs only on my own computer, to organise the papers I read. When I add a
> paper by DOI, arXiv id or title, it looks up the paper's metadata (title, authors, year, venue, abstract,
> external ids, citation count, open-access PDF link) with paper lookup and title match. For papers in my
> library it fetches references and citations to link papers that cite each other, and recommendations to
> suggest related papers to read next. The key is for my own use only: one user, no web service and no
> redistribution of data. Requests are made one paper at a time as I work, with background jobs paced at
> about one request per second and exponential backoff on HTTP 429.

**The endpoint list for "Which endpoints do you plan to use?"** (exactly what research brain calls):

```
Academic Graph API:
GET /graph/v1/paper/{paper_id}
GET /graph/v1/paper/search/match
GET /graph/v1/paper/search
GET /graph/v1/paper/{paper_id}/references
GET /graph/v1/paper/{paper_id}/citations
Recommendations API:
GET /recommendations/v1/papers/forpaper/{paper_id}
POST /recommendations/v1/papers
Fields: paperId, title, year, authors, abstract, externalIds, venue, publicationVenue, journal,
publicationDate, url, citationCount, isOpenAccess, openAccessPdf
```

**Then, in research brain:** **Settings › Library settings › Paper lookup**, paste each key into its box and save.
The keys stay on your computer and are sent only to their own service. You can add one now and the other
later.

### Add your first papers

Drag PDFs onto the window, or click **Add** and paste a DOI, an arXiv link or a paper's title. research
brain fills in the authors, year and journal, and looks for a free legal copy of the PDF. Then click a
paper on the map to read it, highlight it and write about it.

## Where your things are

Everything you keep is an ordinary file on your computer, in **Documents › research brain**:

```
research brain/
  Papers/       one document per paper, named after it: its details, your summary and notes
  Highlights/   a folder per paper, one document per highlight (the passage, its page, your note)
  Notes/        your own notes (put them in subfolders here if you like; they stay there)
  Questions/    your open questions and your answers
  Topics/       your topics and the papers in each
  PDFs/         the papers' PDFs
```

The documents are Markdown, so they open in any text editor (and the folder opens in Obsidian). Each is
named after its title and follows it when you rename it. To see the folder from the app: **Settings › Library
settings › Your documents › Open folder**. (A second profile gets its own *research brain (name)* folder.)

## Add an AI (optional, can be free)

Everything works without an AI: your library and its map, PDFs and highlights, notes, links, tags and
topics, search, similar papers, citations and exporting a bibliography. An AI adds: critiques of your
summaries, explanations of why two papers are connected, summaries, and **Ask your library** (questions
answered from your papers, with page numbers).

research brain doesn't sell or include an AI. It uses one you install and sign in to yourself, so it costs
whatever that AI costs you, which can be nothing:

| Option | Cost | Good to know |
| --- | --- | --- |
| **Gemini CLI** (recommended free option) | free with a personal Google account, within a daily limit (about 1,000 requests a day per Google's docs at the time of writing) | the easiest free one; good answers |
| **Ollama** or **LM Studio** | free, and works offline | needs a fairly powerful computer and several GB per model; answers are weaker |
| **GitHub Copilot CLI** | GitHub's free Copilot plan has a small monthly allowance; paid plans more | needs version 1.0.109 or newer |
| Claude Code, Codex CLI, Cursor | your Claude, ChatGPT or Cursor plan | use them if you already pay for one |

Free plans and their limits change; check the provider's current terms.

### Setting up Gemini (free)

1. **Install Node.js:** go to [nodejs.org](https://nodejs.org), download the **LTS** installer for your
   computer and run it (click through with the defaults).
2. **Install Gemini:** open Terminal (Mac: Finder › **Applications › Utilities › Terminal**; Windows: Start menu › *Terminal*
   or *PowerShell*), type the line below and press Return:

   ```
   npm install -g @google/gemini-cli
   ```

   On a Mac, if it says *permission denied*, type `sudo ` in front of it, press Return and enter your Mac
   password (nothing shows while you type it).
3. **Sign in once:** in the same window type `gemini`, press Return, choose **Sign in with Google**, and
   follow the browser page. When Gemini says it's ready, type `/quit` and close the window.
4. **Choose it in research brain:** click the AI name in research brain's top bar (or **Settings › Library
   settings › AI agent**) and pick **Gemini CLI**. A green dot means it's ready.

To keep usage low, research brain sends an AI only the passages a question needs (the *Balanced* setting
in **Library settings › AI usage**), and nothing ever calls the AI in the background.

## Updating

When a newer version is in your lab's folder, a small **Update** chip appears next to the logo. To update,
double-click the installer again (step 2), or, if you're comfortable with Terminal, run
`research-brain update`. Updating never touches your library.

## Uninstalling

1. Mac: quit research brain. In Terminal, run `research-brain install-app --remove` (removes the app from
   Applications), then `uv tool uninstall research-brain` (removes the program, about 175 MB). Windows:
   the same two lines in PowerShell. The installer tool, uv, and its Python (about 100 MB together) stay,
   as other programs may use them; uv's own documentation says how to remove them.
2. Your library is kept, in case you reinstall. To delete it too, delete **Documents › research brain**
   (your papers, notes and PDFs) and the app's data folder:
   `~/Library/Application Support/research_brain` on a Mac (in Finder: **Go › Go to Folder…** and paste
   it), `%APPDATA%\research_brain` on Windows (paste it into File Explorer's address bar),
   `~/.local/share/research_brain` on Linux. **This deletes your notes and PDFs.**
3. Optional: `uv cache clean` frees the installer's download cache.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| The Mac won't open the installer | See the note under "On a Mac" above: **Open Anyway**, or the Terminal way. |
| *Windows protected your PC* | Click **More info**, then **Run anyway**. |
| The installer stops with an error | Keep the window open and take a screenshot; send it to whoever helps you. Running the installer again is always safe. |
| The app doesn't open, or opens and closes | Open the log: in Finder **Go › Go to Folder…** and paste `~/Library/Application Support/research_brain/app.log` (Windows: `%APPDATA%\research_brain\app.log`). The last lines say why. |
| The first start after installing is slow | Normal once: the computer checks the newly installed files the first time. Later starts take about a second. |
| The AI shows a red dot, or says it isn't signed in | Open Terminal, type the AI's name (`gemini`, `claude`, `copilot`…), sign in, then pick it again in research brain. |
| *Copilot … is too old* | In Terminal, run `copilot update`. |
| Not much disk space | research brain needs about 300 MB. After installing, `uv cache clean` in Terminal frees the download cache. Prefer Gemini over Ollama, whose models take several GB. |
| Adding or searching for papers says *rate limited*, *allowance used up* or *couldn't reach* | The free lookup services are busy (your internet is usually fine). Add your own free keys: see [Step 5](#step-5-recommended-free-keys-for-looking-papers-up). |
| Where is my library? | **Documents › research brain** (see [Where your things are](#where-your-things-are)). In the app: **Settings › Library settings › Your documents › Open folder**. |

## For developers and lab admins: more ways to install

Under the hood the installers use [uv](https://docs.astral.sh/uv/), which downloads its own Python 3.12
(about 66 MB) and installs research brain in its own environment (about 175 MB), so nothing conflicts with
Python, conda or Homebrew already on the computer.

**From a terminal**, in the lab's update folder or a checkout:

```bash
./install.sh                                            # macOS and Linux
powershell -ExecutionPolicy Bypass -File .\install.ps1  # Windows
research-brain open                                     # opens it in its own window
research-brain                                          # or: the server in this terminal + a browser tab (Ctrl+C stops it)
```

From a release folder, the installer installs the newest release there and remembers the folder, so
**`research-brain update`** brings you up to date later; **Library settings › Updates** shows what's new.
A library that a newer version has converted won't open in an older one (it stops with a clear message
instead). Publishing a release and rolling it out to the lab: [docs/releasing.md](docs/releasing.md).

**From the repository** (you have access to it on GitHub):

```bash
git clone https://github.com/henrykanaskie/research-brain.git
cd research-brain
./install.sh        # or install.ps1 on Windows
```

With uv, one line installs a release on every OS:
`uv tool install --python 3.12 "git+https://github.com/henrykanaskie/research-brain.git@v0.7.0"`.
Without installing, from a checkout: `./run.sh` (the first run builds `.venv`, a few minutes), or
double-click `research_brain.command` (Mac) or `research_brain.cmd` (Windows).

**The app entry.** The installer adds a **research brain** app: in Applications, Launchpad and the Dock
on macOS (made on your computer, so there's no developer signature or Gatekeeper prompt involved), the
Start menu on Windows, the app menu on Linux. It runs `research-brain open`, which starts research brain if
it isn't running and opens it in a window. On a Mac that is research brain's own window (WebKit, with no
title bar); `RB_APP_WINDOW=browser` uses a Chrome, Edge or Brave app window instead, which is what Windows
and Linux use (otherwise a browser tab). Closing it stops research brain (on Windows and Linux, a few
minutes after the window closes). Nothing starts when your computer starts. `research-brain install-app`
adds the app entry again, `--remove` takes it away. `research-brain --version` prints the version;
`research-brain where` shows where your library is.

**Your data.** Your papers, notes, questions, highlights, topics and PDFs are in your documents folder,
`~/Documents/research brain` (see [Where your things are](#where-your-things-are); a profile in a folder you
chose, `RB_DATA_DIR` or `RB_VAULT` keep them in the library folder itself, and `RB_DOCUMENTS_DIR` picks one).
The app's own data (search index, extracted text, settings, review history, trash) lives in a per-user
folder, separate from the app: `~/Library/Application Support/research_brain/`
on macOS (`~/.local/share/research_brain/` on Linux, `%APPDATA%\research_brain\` on Windows). Each
library ("profile") is a folder under `profiles/`. There are no accounts or passwords: your computer
login is your identity and everything autosaves. An older `./vault` with notes keeps working where it
is; **Settings → Library settings** offers to move it. Details, and how PDFs are obtained:
[docs/storage-and-distribution.md](docs/storage-and-distribution.md).

**Finding the AI tool.** For Claude Code, the app finds `claude` on your PATH, `~/.local/bin`
(`%USERPROFILE%\.local\bin\claude.exe` on Windows), Homebrew, npm (`%APPDATA%\npm` on Windows), WinGet,
Scoop, snap or nvm; if it lives somewhere else, set `RB_CLAUDE_BIN` to its full path. On Windows, agents
installed with npm run through Node directly, never through `cmd.exe`, so a prompt can't be misread as a
command.

**Using your plan sparingly.** Every request uses your AI plan's limits, so research brain sends as little
as each job needs. **Library settings › AI usage** sets how much: *Light*, *Balanced* (the default) or
*Thorough*. At *Balanced*, a 12-page paper is answered from its best passages (about 2,600 tokens per
question) instead of being sent whole (about 11,000); a critique of a long paper sends its opening, its end
and the passages nearest your summary. Small jobs (explaining a link, checking a review answer,
suggesting idea cards) use the provider's small model (Claude Haiku, Gemini Flash) unless you turn that
off. Critiquing an unchanged summary shows the last critique instead of asking again. The panel shows an
estimate of the last 7 days. Nothing calls the AI in the background.

## Choosing an AI agent

**Settings → Library settings → AI agent** (or the command palette: "Choose AI agent") lists every AI tool
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
| Citation links | Papers that cite each other are joined by thin ink lines with an arrowhead at the cited paper (from Semantic Scholar references; only papers already in your library). New imports are linked automatically in the background; for papers you already have, run the backfill below once. Toggle them under **Settings → Show citation links**. |

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

**One exception you can turn on: web search in General chats.** The chat has three modes: **Library** (your
papers and notes), **Topics…** (every paper in the topics you pick, plus the passages that best match your
question) and **General** (ask anything). In General, if you allow it under *Settings › Library settings ›
AI and privacy › Let the agent search the web*, tick **Search the web** and Claude Code can look things up
and link its sources. Only your question and that General conversation go along, never your library, and the
session gets exactly two tools (web search and fetching a page). It's off by default, and works with Claude
Code.

**Conversations are remembered.** Each one (the library, each paper, each set of topics, General) is kept in
your library (`state/chats/`) and comes back next time. To save space, the last 40 messages are kept in full
and older ones are folded into a short summary, which still goes along with your questions; the least
recently used conversations are dropped first when they all add up to more than about 3 MB. **New
conversation** forgets the current one.

## Chrome extension (web clipping)

The extension isn't in the Chrome Web Store: load it from this folder.

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick the `extension/` folder. Pin it from the puzzle-piece menu so its button stays in the toolbar. research_brain must be running for it to work.
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
| PDF attachment | Copied into `PDFs/` in your documents folder, with text extracted for the AI |
| Highlights and comments | Fragments under the paper: the highlighted text is the quote, your comment is the note, and the page is filled in (`p. <page label>`) |

- **Zotero is never modified.** The app copies `zotero.sqlite` (and its `-wal` file) to a temp folder and reads the copy, so Zotero can stay open.
- **Safe to run again.** Each note remembers its Zotero key (`zotero_key`). Papers you already have, matched by DOI, arXiv id or exact title, are not duplicated. They get a PDF if they had none, highlights they don't have yet (matched by quoted text), and any blank metadata filled in. Your own summary and notes are never overwritten.
- **Highlights stay in sync.** On a re-import, a highlight whose text, comment or page you changed in Zotero updates its fragment, and a highlight you deleted in Zotero moves its fragment to `vault/.trash/`. If you've edited a fragment in research_brain since it was imported, it is never changed or removed; the summary lists it as "kept (edited)".
- It doesn't call Semantic Scholar, so a few hundred papers import in seconds. Items in Zotero's trash and standalone notes are skipped.
- The library is found at `~/Zotero`, or at a custom data directory set in Zotero's preferences. If yours lives somewhere else, start the app with `export RB_ZOTERO_DIR=/path/to/Zotero`.

## Where things live

Your documents folder (`~/Documents/research brain`; see [Where your things are](#where-your-things-are)):

```
Papers/*.md            one per paper, named "<title> (<year>).md": YAML frontmatter + your notes
Highlights/<paper>/    a paper's highlights (fragments), one file each
Notes/**/*.md          your notes, in any subfolders you make
Questions/*.md         your open questions
Topics/*.md            your topics: name, description, ordered members, dismissed and snoozed recommendations
PDFs/                  the PDFs (or your chosen papers folder)
```

Each file's `id:` property is what links, topics and citations refer to, so renaming or moving a file
by hand (inside its type's folder) breaks nothing; a file added without one gets an id from its name.
Properties that are empty are left out of the file.

The library folder (`research-brain where` prints it; below it's called `vault/`) keeps the app's own data:

```
vault/text/        extracted text
vault/state/       your Review schedule and history (review.json), open counts (engagement.json),
                   UI state (ui.json), open-access results (acquire.json), library format (meta.json),
                   where the documents folder is (library.json), privacy settings (privacy.json) and
                   the log of what was sent to AI (ai_log.jsonl), evidence you triaged for your open
                   questions (questions/<id>.json); included in the nightly backup and the zip export
vault/.index/      embeddings, chat passage index, search index (meta.sqlite), cached explanations
                   (derived only; safe to delete, rebuilt automatically)
vault/memory/      Research memory: core.md (library map, generated), findings.md (Added via AI, editable)
vault/cards/       idea cards, one JSON file per paper (claims, equations, quantities, methods; your review)
vault/.trash/      deleted notes (nothing is permanently deleted by the app)
vault/exports/     read-only HTML sites made with Settings → Export
vault/backups/     automatic zips made before a library format upgrade (format 2 moved notes/ into the
                   folders above)
```

Next to the profiles: `settings.json` (open profile, Unpaywall email) and `storage.log` (moves,
migrations, imports, exports).

**Bringing in what you already have.** *Add › Import a folder of PDFs* opens your computer's folder picker:
every PDF in the folder (and its subfolders) is added, and each subfolder can become a topic, the way bookmark
folders work (or everything in one topic, or none). The same file twice is stored once, and titles, authors and
DOIs fill in over the next minutes. *Add › Import browser bookmarks* takes the bookmarks file any browser exports
(Chrome or Edge: Bookmarks › Export bookmarks; Firefox: Bookmarks › Manage › Import and Backup › Export; Safari:
File › Export Bookmarks): each bookmark folder becomes a topic; links that look like papers are ticked; a DOI
or arXiv link is added from its record, a PDF link is downloaded. Zotero libraries have their own importer.

**Your PDFs, one copy each.** Every PDF is one file named after its paper (`<paper id>.pdf`) in one folder:
`PDFs/` in your documents folder until you pick another in **Library settings › Papers folder** (for example
`~/Documents/Research papers` or a synced folder; *Move here* moves the files, *Open folder* shows them).
Adding a file the library already has, by any route (drag and drop, the file picker, a URL, the extension),
opens that paper instead of storing a second copy. After you add a file from Downloads, the app offers to send
the Downloads copy to the Trash. Deleting a paper asks about its PDF: **Keep the PDF** (it moves to
*Removed from library* inside the papers folder) or **Delete the PDF too** (it goes to your computer's Trash,
restorable from there). **Papers already on this computer** checks a folder you pick, only when you ask:
copies of PDFs you already have can go to the Trash in one click, and the rest can be added, moving them
into the papers folder (or copying, if you'd rather leave the originals).

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
  thinking, in Markdown with LaTeX and [[links]], like any note. A question is a note (`type: question`, a
  file in `Questions/`), so it has backlinks, tags, topics, search and a place on the graph.
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
- **Reach.** Each question says how far it may go, in its header: **Private** (the default: only this
  computer; no AI agent and no outside service sees it), **Library** (the same local matching, and AI features
  such as the evidence summary may read it), **Scout** or **Agent**. A scouting question also searches *published*
  papers about once a day while the app is open: its key words (the title plus the body's main terms, shown
  under the evidence; never your answer or notes) go to OpenAlex and arXiv, the results are ranked on this
  computer by the same similarity as library evidence, and close matches arrive under *To triage* as
  **Published paper**, with author, year, venue, whether it's free to read, and **Add to library** (linked to the
  question). The first search looks back three years; later ones only at what's new. No AI is involved, so
  scouting costs nothing from your AI plan. **Agent** adds an AI search about once a week (or **Run agent
  search**): your AI agent gets the question's title and description, rewords it into the terms papers use,
  searches OpenAlex and arXiv, reads abstracts, follows reference lists and proposes up to five papers, each
  with a one-line reason and a suggested stance (supports, against, related) shown on the row. The verdict
  stays yours. A run is at most the Research step limit plus two turns (7 at *Balanced*, about one Research
  question of your plan), and the panel shows the last run's searches and tokens. The agent has no tools of
  its own: it can only ask the app to search, read an abstract, list citations or propose. Don't scout an
  unpublished idea: its key words leave your computer. Marking a question private with the lock always wins.
- **Sensitivity.** *Library settings › Open questions* sets the match threshold (default 0.12 for TF-IDF, 0.40 for
  sentence-transformers) and the daily cap. With TF-IDF a match must also share three of the question's words.
- Files: your triage decisions are in `vault/state/questions/<question-id>.json` (backed up with the library);
  deleting a question moves them to `.trash/`.

## Profiles, backups as a zip, and moving to a new laptop

- **Settings → Library settings** (or the command palette: *Library settings*, *Switch profile*): the open
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

## Backups and starting at login

On any OS, `research-brain backup` snapshots the library into its own local git history, and
`research-brain backup --schedule` runs it every night: a Task Scheduler task on Windows, a systemd user
timer on Linux (or a crontab line it prints), and on macOS the launchd agents below. Git must be installed
(`winget install Git.Git` on Windows). The rest of this section is the macOS-specific setup.

`scripts/vault_backup.sh` snapshots your notes into git repositories inside the vault and your documents
folder (by default the library the app opens; pass a folder to choose another). The first run
`git init`s each with a `.gitignore` for `PDFs/`, `pdfs/`, `.index/`, `.trash/`, `exports/` and `backups/`, then each run
commits whatever changed as `vault snapshot <date>` (author `research_brain <research_brain@localhost>`;
your own git identity is never used). PDFs are left out to keep the repo small, so back up `PDFs/`
some other way (Time Machine, iCloud). Nothing is pushed; add a private remote yourself if you want an
off-machine copy.

```bash
scripts/vault_backup.sh                 # snapshot now
git -C ~/Documents/"research brain" log --oneline   # history of your papers, notes and questions
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

**Settings → Export** writes a self-contained, read-only website to `vault/exports/<date_time>/`. With a
search active it exports the matching notes; otherwise it asks "Export all N notes?". Each note gets a
page with its metadata, abstract, your summary, notes and fragments, with math rendered by the bundled
KaTeX. Links between exported notes work; links to notes you didn't export show as plain text, so
nothing outside the selection leaks. Claude's critiques are left out. Tick **Include PDFs** to copy the
papers' PDFs too (only if you're allowed to share them). The folder works opened straight from disk:
zip it and send it, or drop it on any static host. Everything happens locally. API:
`POST /api/export {"ids": [...] | null, "include_pdfs": false, "title": "Reading list"}`.

## Reading for what matters, directions and writing

- **Focus** (in the PDF viewer, **F**): pick an open question or topic, or type what you're after, and the
  passages of this paper that bear on it are listed best first and marked on the pages. Local search only, no
  AI, so it works for private questions too.
- **Suggest directions…** (on a question's AI section, and in a topic's drawer): your AI agent reads the evidence
  you kept (or the topic's papers) and suggests gaps, hypotheses, how to test them and what would change the
  picture, each cited to the passage it rests on. Before anything is sent you see every source that would go,
  every one that won't and why (private, no summary yet, over your usage level's limit), and about how many
  tokens; untick any. Your current answer goes only if you tick it. Results are kept with the question or
  topic; *Save as a note* makes a private note.
- **Writing help** (on any note): *Cite papers…* (no AI), and an AI helper that reads only the sources you tick
  (by default the papers and notes your draft links to). Private sources can't be ticked, and your draft goes
  only if you tick it *and* it isn't private. *Add below my draft* appends the answer; nothing is replaced.
- **PDF with my highlights** (paper panel) / **Download with highlights** (viewer): a copy of the PDF with your
  highlights and notes written in as real PDF annotations, which Preview, Acrobat, Zotero and browsers show. The
  library's copy is unchanged.

Every AI feature follows the same rules: private notes and papers never go; a question's reach decides whether
it may; the privacy guard checks the outgoing text; each send is in *What was sent to AI*; your usage level caps
how much goes.

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
  styles from [zotero.org/styles](https://www.zotero.org/styles): download its `.csl` file and add it in **Settings ›
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

Open **Documents › research brain** as an Obsidian vault and your notes, tags and math work there too.
Files are named after their titles, so a `[[Title]]` link resolves in Obsidian as well (a paper's file
also carries its year: `[[Title (1996)]]`); links the app writes as `[[note-id|Title]]` show as unresolved
there. Editing properties, and renaming or moving files within their folder, are safe: the app finds each
note by the `id:` inside it. Summary mentions, manual links and highlight nesting are app-only.
[docs/obsidian.md](docs/obsidian.md) lists what works and what doesn't.

## Settings (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `RB_VAULT` | — | Use this library folder (pins it: profile switching is off). Default: the open profile in the data folder |
| `RB_PROFILE` | `default` | Open this profile (overrides the one picked in the app) |
| `RB_DATA_DIR` | per-user data folder | Move the whole data folder (portable installs, tests) |
| `RB_DOCUMENTS_DIR` | `~/Documents/research brain` | Where your papers, notes, questions, highlights, topics and PDFs go (a library records its choice) |
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
| `RB_WATCH` | `1` | `0` turns off watching your documents folder for outside edits (live updates) |
| `S2_API_KEY` | — | Optional free Semantic Scholar key (avoids occasional rate limiting) |
| `OPENALEX_API_KEY` | — | Free OpenAlex key (without one, OpenAlex shares a small daily allowance with your whole network). Same as the box in Library settings › Paper lookup |
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
- `app/live.py`: watches your documents folder and tells open pages (Server-Sent Events) when a note changes outside the app.
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
