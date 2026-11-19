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

Download **research-brain-installer-….zip**, then follow the steps for your computer.

<details>
<summary>What you need</summary>

Windows 10 or 11, macOS 11 or newer, or Linux; about 700 MB of free space; and internet for the install. No
Python or coding tools: the installer brings its own Python and keeps it separate from anything else.
</details>

### Windows

1. Right-click the zip › **Extract All…** › **Extract**.
2. In the folder that opens, double-click **`Install-research-brain.cmd`**.
3. Wait for **Done**, and research brain opens.
4. Later, open it from the Start menu.

<details>
<summary>Windows blocked it or it won't run</summary>

- *Windows protected your PC*: click **More info**, then **Run anyway**.
- *Can't find its other files*: you ran it from inside the zip. Use **Extract All…** first; double-clicking a zip
  only shows what's inside.
- On a university-managed PC, antivirus or IT policy can block installers. Take a screenshot and see
  [Reporting a problem](#reporting-a-problem).
</details>

<details>
<summary>Add the Chrome extension (optional)</summary>

1. Right-click **`research-brain-extension-….zip`** › **Extract All…**, into a folder outside OneDrive such as
   `C:\research brain extension`.
2. In Chrome, go to `chrome://extensions` and turn on **Developer mode**.
3. Click **Load unpacked** and choose that folder.

Keep the folder: Chrome loads it from there every time, and a synced folder may offload it. A university-managed
Chrome may not allow Developer mode.
</details>

### Mac

1. Double-click the zip, then open the folder it makes.
2. Double-click **`Install-research-brain.command`**.
3. Wait for **Done**, and research brain opens.
4. Later, open it from Launchpad or Applications.

<details>
<summary>The Mac blocked it or it won't run</summary>

- *Can't be opened* or *Apple could not verify*: open **System Settings › Privacy & Security**, scroll down, click
  **Open Anyway**, then double-click the installer again.
- Still blocked, or *permission denied* (common from a shared drive): open **Terminal** (Applications ›
  Utilities), type `bash ` with a space, drag the installer into the window and press Return.
</details>

<details>
<summary>Add the Chrome extension (optional)</summary>

1. Double-click **`research-brain-extension-….zip`** and move the folder it makes into your home folder.
2. In Chrome, go to `chrome://extensions` and turn on **Developer mode**.
3. Click **Load unpacked** and choose that folder.

Keep the folder: Chrome loads it from there every time. A university-managed Chrome may not allow Developer mode.
</details>

<details>
<summary>Using research brain on a Mac</summary>

- Keep it in the Dock: while it's open, right-click its icon › **Options › Keep in Dock**.
- The window has no title bar: drag it by the empty space in its top bar, and quit with **⌘ Q**.
</details>

### Linux

1. Unzip it and open a terminal in the folder it makes.
2. Run `bash install.sh`.
3. Later, open it from your app menu.

<details>
<summary>Add the Chrome extension (optional)</summary>

1. Unzip **`research-brain-extension-….zip`** into your home folder.
2. In Chrome, go to `chrome://extensions` and turn on **Developer mode**.
3. Click **Load unpacked** and choose that folder.
</details>

### After installing

<details>
<summary>Using the Chrome extension</summary>

- research brain has to be open for it to work.
- Pin **research_brain** from Chrome's puzzle-piece menu.
- Save a paper: on its page, click the button › **Save to research_brain**.
- Clip a passage: select it, right-click › **Add to research_brain…**.
- Highlight a page: **Alt+Shift+A**.
- After updating research brain, unzip the new extension over the old folder and click reload on its card in
  `chrome://extensions`.

More in [Chrome extension](#chrome-extension-web-clipping).
</details>

<details>
<summary>Installing from GitHub instead</summary>

Click **Code › Download ZIP**, unzip it, and follow the same steps. The extension is the `extension` folder inside.
</details>

<details>
<summary>Does it run in the background?</summary>

No. Closing the window stops research brain (on Windows and Linux, a few minutes later), and nothing starts when
your computer starts.
</details>

### Free keys for looking papers up (recommended)

Free keys stop *rate limited* errors when adding or searching for papers.

1. **OpenAlex:** sign up at [openalex.org](https://openalex.org), then copy your key from [openalex.org/settings/api](https://openalex.org/settings/api).
2. **Semantic Scholar:** request a key at [semanticscholar.org/product/api](https://www.semanticscholar.org/product/api#api-key-form) (it arrives by email in a few days).
3. In research brain, paste each key into **Settings › Library settings › Paper lookup** and save.

<details>
<summary>Why these keys help</summary>

research brain finds papers, citations and related work through OpenAlex and Semantic Scholar. Without a key,
each one shares a small allowance with lots of other people (OpenAlex: everyone on your network; Semantic
Scholar: everyone), and when it runs out, adding or searching fails with *rate limited* or *couldn't reach*.
These keys are only for looking papers up, not for the AI, and they cost nothing. They stay on your computer
and are sent only to their own service. You can add one now and the other later.
</details>

<details>
<summary>What to put in the Semantic Scholar form</summary>

Scroll to *Request a Semantic Scholar API Key*. Each person asks for their own key. Semantic Scholar favours
academic, nonprofit and government requests and reviews each one by hand.

| Field | What to put |
| --- | --- |
| First name, Last name | Your name. |
| Email | Your university or work email (they ask for an academic or corporate address, not Gmail). |
| Affiliation | Your university or organization, e.g. *Example University, Department of Physics*. |
| Affiliation website URL | Your group's or department's web page (or your organization's home page). |
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

</details>

### Add your first papers

1. Drag PDFs onto the window, or click **Add** and paste a DOI, arXiv link or title.
2. Click a paper on the map to read, highlight and write about it.

<details>
<summary>What happens when you add a paper</summary>

research brain fills in the authors, year and journal, and looks for a free legal copy of the PDF.
</details>

### Your first ten minutes

1. **Add three or four papers you know.** Papers about the same thing gather into a named region.
2. **Click a dot** and highlight a passage in its PDF (**Open in viewer**).
3. **Write a short summary**, then click **Critique my summary** (needs an AI).
4. **Link two papers** in the panel's **Links** box, or right-click a dot.
5. **Make a topic** from **Topics** at the top, and add papers to it.
6. **Ask** (top right) a question about your papers.
7. **Cite** with **Alt+C**.

<details>
<summary>More on each step</summary>

- The app's own tour is *Help: quick tour* in the command palette (⌘K).
- A highlight becomes a red *fragment* on the paper.
- A critique checks your summary against the paper.
- Drag a dot where you want it; linked dots come along.
- A topic keeps a *Next to read* list for you.
- **Ask › Library** answers from all your papers, **Topics…** from the topics you pick, and **General** is the agent
  on its own (attach papers or notes if you like). Answers cite pages you can click.
- **Cite:** pick papers, then *Copy \cite{…}* or *Copy BibTeX* for LaTeX, or *Copy bibliography* for Word.
- The gear (top right) has Library settings in tabs with a search box, and **?** lists every shortcut.
</details>

### Reporting a problem

Send what you did, what happened, a screenshot, your version and the end of the log.

<details>
<summary>Where to find your version and the log</summary>

- **Version:** Settings (the gear) › Library settings › Library tab, or `research-brain --version` in Terminal.
- **Log** (send the last 50 lines or so):
  - Windows: paste `%APPDATA%\research_brain` into File Explorer's address bar, open `app.log`.
  - Mac: Finder › Go › Go to Folder…, paste `~/Library/Application Support/research_brain/app.log`.
  - Linux: `~/.local/share/research_brain/app.log`.
- Also say Windows, Mac or Linux, and which AI you chose, if any.
- Don't send your library or your API keys: no one needs them to fix a bug.
</details>

## Where your things are

Your library is ordinary files in **Documents › research brain**. To open it from the app: **Settings › Library
settings › Your documents › Open folder**.

<details>
<summary>What's in the folder</summary>

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
named after its title and follows it when you rename it. A second profile gets its own *research brain (name)*
folder.
</details>

### Folders of papers you already have (kept where they are)

1. Click **Add › Add folders of papers** (or **Library settings › Folders of papers**).
2. Choose your folders, check the topic names, and confirm.

research brain links to each PDF where it is, and never moves, renames, changes or deletes a file there.

<details>
<summary>Topics from folders</summary>

Each folder becomes a topic named after it (*Research/Plasma/Sheaths/x.pdf* goes in *Sheaths*). Before anything
is added you see every folder with its PDF count and topic name: change a name, clear it to leave that folder out
of topics, or **Suggest names with AI** (it sends your AI the folder and file names only). Two folders given the
same name share a topic, and a name you already use joins that topic.
</details>

<details>
<summary>Keeping up with new and moved PDFs</summary>

The folders are checked when the app starts and every 10 minutes (or **Check now**). A PDF you drop in is added
to its folder's topic, and a new subfolder becomes a new topic. A PDF you move to another folder keeps its paper,
notes and highlights and moves to that folder's topic.
</details>

<details>
<summary>Deleting papers or PDFs</summary>

Deleting a paper in research brain leaves its PDF in your folder and doesn't add it back. A PDF you delete from
the folder leaves its paper in the library, without the PDF. **Stop following** a folder keeps its papers,
without their PDFs.
</details>

<details>
<summary>OneDrive online-only files and disk space</summary>

Each PDF is read once when it's added, for its text and its DOI or arXiv id, so search, Ask and Research see the
whole paper. A PDF that is online only in OneDrive is downloaded, read and sent straight back to online only
before the next one: only one PDF is on your computer at a time, and what stays is its text, about 2% of its size
(5 GB of PDFs leaves about 100 MB). Expect 2 to 5 seconds a paper the first time; after that, nothing is
downloaded until you open a PDF.

If a Mac can't send them back, reading pauses once 500 MB is held and asks you to right-click the folder ›
*Free up space*; it goes on by itself. With no room even for one PDF, a paper waits (named from its file name)
and is read when there's room, or as soon as you open it or ask about it. The same PDF in two folders is one
paper in both topics.

A large import shows a gauge in the bottom-right corner: how many are done, the time left, the file being read,
and Stop. Make it smaller with the minus button; it keeps going while you work.
</details>

### Short on disk space? Keep your PDFs in OneDrive

1. Sign in to the OneDrive app (built into Windows; free from the App Store on a Mac).
2. In **Library settings › Papers folder**, move it into OneDrive, such as *OneDrive › research brain PDFs*.
3. Right-click that folder › **Free up space**.
4. Windows only: tick **Keep new PDFs online only** under the papers folder.

<details>
<summary>How it works</summary>

PDFs are what fill a disk; your notes take very little. A cloud icon means a file is online only. A PDF downloads
when you open it in the viewer or highlight in it, and needs the internet the first time. Everything else (the
map, the panel, search, Ask) works from your notes and from the text read out of each PDF when you added it:
research brain never reads an online-only file on its own. iCloud Drive and Dropbox's online-only files work the
same way.

- **Windows:** with **Keep new PDFs online only** on, every PDF you add (dropped in, by DOI, from the extension,
  from Zotero) is freed from the PC as soon as OneDrive has uploaded it.
- **Mac:** check that OneDrive keeps files online-only until you open them (recent macOS versions always do).
  There's no switch for new PDFs: one stays on the Mac until macOS frees it when the disk runs low, or until you
  use Free up space again.
- OneDrive often backs up Documents on Windows, so *Documents › research brain* may already be in OneDrive. That's
  fine; free up space on the PDFs folder only, not on your notes.
</details>

<details>
<summary>When the disk is nearly full</summary>

research brain always leaves 300 MB free: it refuses to add a PDF that would go past that, and says how much space
is left. If the disk fills up anyway, saving answers with a clear message, and nothing is half-written. Below 1 GB
free, the app warns you, with a button to the papers folder setting.
</details>

## Add an AI (optional, can be free)

An AI adds summary critiques, explanations of links, and **Ask your library**. Everything else works without one.

| Option | Cost |
| --- | --- |
| **Gemini CLI** (recommended) | free with a Google account, within a daily limit |
| **Ollama** or **LM Studio** | free and offline, but needs a powerful computer |
| **GitHub Copilot CLI** | small free monthly allowance (version 1.0.109 or newer) |
| Claude Code, Codex CLI, Cursor | your existing Claude, ChatGPT or Cursor plan |

<details>
<summary>More about the options</summary>

- Without an AI you still get your library and its map, PDFs and highlights, notes, links, tags and topics,
  search, similar papers, citations and bibliographies.
- research brain doesn't sell or include an AI: it uses one you install and sign in to yourself.
- Gemini's free limit was about 1,000 requests a day per Google's docs at the time of writing. It's the easiest
  free option, with good answers.
- Ollama and LM Studio models take several GB each, and their answers are weaker.
- Free plans and their limits change; check the provider's current terms.
- To keep usage low, research brain sends only the passages a question needs (*Balanced* in **Library settings ›
  AI usage**), and never calls the AI in the background.
</details>

### Setting up Gemini (free)

1. Install the **LTS** version of Node.js from [nodejs.org](https://nodejs.org).
2. In a terminal, run `npm install -g @google/gemini-cli`.
3. Run `gemini`, choose **Sign in with Google**, then type `/quit` when it's ready.
4. In research brain, click the AI name in the top bar and pick **Gemini CLI**.

<details>
<summary>Help with these steps</summary>

- **Opening a terminal:** Windows: Start menu › *Terminal* or *PowerShell*. Mac: Finder › Applications › Utilities ›
  Terminal.
- **Mac, *permission denied*:** put `sudo ` in front of the npm line and enter your Mac password (nothing shows
  while you type it).
- Signing in opens a browser page; follow it.
- The AI choice is also in **Settings › Library settings › AI agent**. A green dot means it's ready.
</details>

## Updating

Download the newer installer zip, unzip it, and run its installer the same way as the first time.

<details>
<summary>More about updating</summary>

It replaces the app and never touches your library. If research brain is open, the installer closes it first.
research brain doesn't check for updates by itself.
</details>

## Uninstalling

1. Quit research brain.
2. In Terminal (PowerShell on Windows), run `research-brain install-app --remove`.
3. Then run `uv tool uninstall research-brain`.

Your library is kept, in case you reinstall.

<details>
<summary>Delete your library too</summary>

**This deletes your notes and PDFs.** Delete **Documents › research brain** and the app's data folder:

- Windows: `%APPDATA%\research_brain` (paste it into File Explorer's address bar).
- Mac: `~/Library/Application Support/research_brain` (Finder › Go › Go to Folder…).
- Linux: `~/.local/share/research_brain`.
</details>

<details>
<summary>What stays behind</summary>

The first command removes the app from your Applications or Start menu; the second removes the program (about
175 MB). The installer tool, uv, and its Python (about 100 MB together) stay, as other programs may use them; uv's
own documentation says how to remove them. `uv cache clean` frees the installer's download cache.
</details>

## If something goes wrong

| What you see | What to do |
| --- | --- |
| *Windows protected your PC* | Click **More info**, then **Run anyway**. |
| Windows: *can't find its other files* | Right-click the zip › **Extract All…**, and run it from that folder. |
| The Mac won't open the installer | Use **Open Anyway** (see [Mac](#mac)). |
| The installer stops with an error | Take a screenshot and run it again; that's always safe. |
| Antivirus or IT blocks the installer | Ask IT to allow uv, or [install uv yourself](https://docs.astral.sh/uv/getting-started/installation/) and run it again. |
| The update *didn't take* | Close research brain, wait a minute, and run the installer again. |
| The app doesn't open, or closes at once | Read the end of the log (see [Reporting a problem](#reporting-a-problem)). |
| The first start is slow | Normal once; later starts take about a second. |
| The AI shows a red dot | In Terminal, run the AI's name (`gemini`, `claude`…), sign in, and pick it again. |
| *Copilot … is too old* | Run `copilot update`. |
| Not much disk space | Run `uv cache clean`, and prefer Gemini over Ollama. |
| *Rate limited* or *couldn't reach* when adding papers | Add [free keys](#free-keys-for-looking-papers-up-recommended). |
| Where is my library? | **Documents › research brain**. |

<details>
<summary>More detail on these</summary>

- **Antivirus:** the installer downloads uv, Astral's Python installer, with a PowerShell command, which some
  antivirus tools flag. This is common on university-managed PCs.
- **Update didn't take:** research brain was still running. The installer also tries to close it for you.
- **Slow first start:** the computer checks the newly installed files the first time.
- **Disk space:** research brain needs about 700 MB. Ollama's models take several GB.
- **Rate limited:** the free lookup services are busy; your internet is usually fine.
- **Library:** also in the app at **Settings › Library settings › Your documents › Open folder**. See
  [Where your things are](#where-your-things-are).
</details>

## Sharing research brain with others

1. Download **`research-brain-installer-<version>.zip`** from the release page.
2. Upload the zip, not unzipped, to a shared folder (OneDrive, SharePoint, Google Drive, Box…).
3. Tell people to download it and follow [Install it](#install-it-no-coding-needed).
4. For new versions, upload the newer zip and tell people to run its installer the same way.

<details>
<summary>Why the zip, and a message you can send</summary>

The zip keeps the Mac installer double-clickable, which single files downloaded in a browser don't. It holds the
installers, the app, the Chrome extension, this README and *READ ME FIRST.txt*. With the GitHub command line:
`gh release download v1.0.33 --pattern "research-brain-installer-*"`.

A message you can send: *"In the shared folder, download research-brain-installer-….zip. Unzip it (Windows:
right-click › Extract All; Mac: double-click it), open the folder it makes, and double-click
Install-research-brain.cmd (Windows) or Install-research-brain.command (Mac). READ ME FIRST.txt in there covers
anything that gets in the way."*
</details>

<details>
<summary>What each person gets</summary>

Each person gets their own library on their own computer: nothing is shared unless someone exports something (a
reading list, a `.bib`, a library zip). Each person adds their own [AI](#add-an-ai-optional-can-be-free) and their
own [free keys](#free-keys-for-looking-papers-up-recommended). Updating never changes their library. More detail,
including how a library-format change is handled: [docs/releasing.md](docs/releasing.md).
</details>

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
| Add a paper | Drag a PDF onto the window, or **Add** › paste a DOI, arXiv id, PMID, ISBN or title (⌘⇧A). |
| Add on the map | Right-click an empty spot to add a paper, note or question there. |
| Highlight | Open the PDF (**Open in viewer**) and select text. Each highlight is a *fragment* on its paper. |
| Summaries | Write your own, then **Critique my summary**. The AI points out errors and gaps; it never rewrites it. |
| Review | **Review** in the top bar quizzes you on your own summaries and notes, spaced over days and weeks. |
| Link papers | Use the panel's **Links** box or right-click a dot. Click any line to have the AI explain the link. |
| Move dots | Drag a dot; it stays there. Right-click › **Let it move freely** to undo. |
| Regions | Related papers group into named regions. Click a region's name to focus or **Rename** it. |
| Topics | Add a paper to a topic from its panel; the topic keeps a *Next to read* list. |
| Read and unread | Mark it in the panel (**Alt+3**) or right-click its dot. |
| Filter the map | **Filter** (top left) by type, year, tag or region; save it as a view. |
| Find more papers | **Discover papers** on a paper, or **Find papers** (Shift+P) to have the AI search a topic. |
| Ask | **Ask** (top right): **Library**, **Topics…** or **General**. Answers cite pages you can click. |
| Research mode | Switch **Quick** to **Research** for a deeper, multi-step search of your library. |
| Idea cards | **Ideas** on a paper: claims, equations, values and methods, checked against the paper's text. |
| Open questions | **Q** writes down a question; matching evidence from your library collects under it. |
| Cite | **Alt+C**: copy `\cite{}`, BibTeX or a formatted bibliography in any CSL style. |

Math works everywhere: `$...$` inline and `$$...$$` display.

## Keyboard shortcuts and help

Press **?** for every shortcut, grouped, with a search field ("highlight" finds the viewer's
"Add note from selection"; "g" lists the G-chords). **⌘K** (Ctrl+K) is the command palette: type
any action and press Enter; your recent actions come first and each row shows its shortcut. The
palette also has **Help** (a one-page guide) and **Help: quick tour** (six coach marks, shown once on
first run). The ones you'll use most: **/** search, **N** new note, **⌘⇧A** add a paper by id,
**⌘O** import a PDF, **A** ask your library, **G then R** review, **G then T** topics, **G then I** ideas, **S** and **B**
write the summary or notes, **C** critique, **Alt+C** cite papers, **Shift+L** link to…, **O** open the PDF, **F** focus,
**⌘[ / ⌘]** back and forward, **Esc** closes the innermost thing. In the viewer: **j/k** pages,
**N** add a note from the selection, **[ / ]** fragments, **G then G** back to the graph.

## What the AI can and can't do

- It reads only the text research brain sends it. It can't browse, open files, run code or reach the internet.
- Private notes are never sent (see [Private notes](#private-notes-your-own-ideas-stay-on-your-computer)).
- **Web search** is the one exception: off by default, allowed in **Library settings › AI and privacy**, and only
  in a General chat with Claude Code.
- **Attach…** in a General chat adds papers, notes, questions or topics for it to read. Nothing else goes along.
- Conversations are kept in your library and come back next time; **New conversation** starts over.

<details>
<summary>How it's locked down</summary>

Agent CLIs run from an empty private folder, with their tools, extensions and project instructions switched off
wherever the CLI allows it, and are stopped if they try to use a tool anyway. Research mode gets exactly one MCP
server, research brain's own (read-only except for saving a short sourced finding), and the run is killed if any
other tool shows up. With web search on, your own writing goes along only after you confirm once. Details:
[SECURITY.md](SECURITY.md).
</details>

## Chrome extension (web clipping)

1. In Chrome, go to `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick the `extension/` folder.
3. Pin it from the puzzle-piece menu.

research brain must be open for it to work.

| To | Do |
| --- | --- |
| Save a paper | Click its button › **Save to research_brain (link + text)**. |
| Save the PDF too | Click **Save PDF too** instead. |
| Clip a passage | Select it, right-click › **Add to research_brain…**. |
| Highlight a page | Press **Alt+Shift+A**, select text, then **Highlight** or **Highlight + note**. |
| Annotate a PDF tab | Click its button › **Annotate this PDF in research_brain**. |

<details>
<summary>More about the extension</summary>

- It works on any page, including arXiv, journal sites and Chrome's PDF viewer. Keyboard shortcuts are in
  `extension/README.md`.
- **Save to research_brain** keeps the paper as a *linked paper*: its link, its full text and its citation details
  (title, authors, DOI, journal, year, abstract), read from the page in your own browser session. No PDF file is
  kept. One paper per click.
- **Save PDF too** downloads the PDF in your browser, with your own access to the site (subscription, university
  login). research_brain never sees your login and never downloads from a paywalled site itself.
- Highlights reappear when you come back to a page. Annotating a PDF tab imports it and opens the built-in viewer.
- arXiv and DOI pages are imported as papers automatically. Other pages become a source note.
- The extension has no AI and talks only to research_brain on this computer; see `extension/README.md`.
</details>

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
`meta_checked`; title, authors, year, venue, DOI and abstract follow it. When no source has an abstract (Crossref's
records for APS journals carry none, for instance), it is read from the PDF itself: the paragraph under an
"Abstract" heading, or the unlabelled one between the authors and the first section. Titles keep the publisher's
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

1. Click **Add › Import from Zotero**.
2. Pick the library (if you're in groups) and **Entire library** or a collection.
3. Leave **Copy PDFs** and **Import highlights as fragments** on, then click **Import**.

It brings in your papers, their notes and your Zotero highlights. You can close the dialog while it runs.

<details>
<summary>What each Zotero item becomes</summary>

Sub-collections are included, and the counts show how many papers each one holds. When the import finishes it
shows a summary and the graph refreshes.

| From Zotero | Becomes |
| --- | --- |
| Regular item (article, preprint, thesis…) | A paper: title, authors in order, year, DOI, arXiv id (from the URL, Extra or a `10.48550/arXiv.*` DOI), URL, abstract, venue, tags |
| Child notes | The paper's notes (HTML converted to Markdown) |
| PDF attachment | Copied into `PDFs/` in your documents folder, with text extracted for the AI |
| Highlights and comments | Fragments under the paper: the highlighted text is the quote, your comment is the note, and the page is filled in (`p. <page label>`) |
</details>

<details>
<summary>Re-importing, sync and where Zotero is found</summary>

- **Zotero is never modified.** The app copies `zotero.sqlite` (and its `-wal` file) to a temp folder and reads the copy, so Zotero can stay open.
- **Safe to run again.** Each note remembers its Zotero key (`zotero_key`). Papers you already have, matched by DOI, arXiv id or exact title, are not duplicated. They get a PDF if they had none, highlights they don't have yet (matched by quoted text), and any blank metadata filled in. Your own summary and notes are never overwritten.
- **Highlights stay in sync.** On a re-import, a highlight whose text, comment or page you changed in Zotero updates its fragment, and a highlight you deleted in Zotero moves its fragment to `vault/.trash/`. If you've edited a fragment in research_brain since it was imported, it is never changed or removed; the summary lists it as "kept (edited)".
- It doesn't call Semantic Scholar, so a few hundred papers import in seconds. Items in Zotero's trash and standalone notes are skipped.
- The library is found at `~/Zotero`, or at a custom data directory set in Zotero's preferences. If yours lives somewhere else, start the app with `export RB_ZOTERO_DIR=/path/to/Zotero`.
</details>

## Private notes (your own ideas stay on your computer)

Everything you write is **private** by default: notes, open questions and highlights. Papers themselves are
shareable.

- Private items never go to an AI and are left out of exports; search and the map still use them.
- Click the lock in a note's header to switch one item, or change the defaults in **Library settings › Privacy**.
- **What was sent to AI** in the same place logs every request (never the content).

Details: [SECURITY.md](SECURITY.md#privacy-of-your-own-ideas-appprivacypy).

## Idea cards

1. Open a paper's **Ideas** section and click **Suggest ideas**.
2. **Confirm** (Y), **Edit** (E) or **Reject** (X) each card.
3. **G then I** opens every card as a table you can sort, filter and export as CSV.

Each card must quote the paper word for word, or it's dropped. Values are compared across papers, and a
disagreeing one is flagged.

## Open questions

1. **Add › New question** (or **Q**) and write it down.
2. As evidence turns up in your library, it collects under the question.
3. Sort each piece: **S** supports, **A** against, **R** related, **D** dismiss.

Questions are private by default. Set a question to **Scout** to also search published papers, or **Agent** to
have the AI look too.

## Profiles, backups and moving to a new laptop

- **Library settings** has profiles (separate libraries), **Export library (.zip)** and **Import a library zip**.
- To move to a new laptop, export the zip, install research brain there, and import it.
- `research-brain backup --schedule` takes a nightly snapshot of your notes (needs Git).

## Reading status and topics

- Set a paper's status above its title: To read, Reading, Read, Later or Not relevant (**Alt+1…5**).
- **T** opens Topics: each topic shows up to 5 papers to read next, including ones you don't have yet.
- **L** parks a paper for later.

## Sharing a reading list

**Settings › Export** makes a read-only website of your notes (or the ones matching your search) that you can zip
and send. Private items are left out.

## Citing and bibliographies

- **Alt+C**: pick papers, then copy `\cite{}`, BibTeX, an in-text citation or a bibliography.
- Pasting into Word or Google Docs keeps the formatting, but doesn't update later; paste again after changes.
- Styles: APS (default), AIP, IEEE, AIAA, Nature, APA and Chicago. Add others from
  [zotero.org/styles](https://www.zotero.org/styles) in **Library settings › Citations**.
- **Auto-updating .bib files** (same place) keep a `.bib` in your thesis folder up to date.

## On your phone

Open research brain on a phone or tablet over Tailscale, after pairing it in **Library settings › Phones and
tablets**. Setup: [docs/mobile-capture.md](docs/mobile-capture.md).

## Obsidian

Open **Documents › research brain** as an Obsidian vault. What works there: [docs/obsidian.md](docs/obsidian.md).

## Works offline

Everything works offline except looking papers up and the AI.
