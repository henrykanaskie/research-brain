# Using your library in Obsidian

research brain stores every paper, note, question and highlight as a plain Markdown file with YAML
frontmatter, in your documents folder (**Documents › research brain** for a normal install), so that
folder opens directly as an Obsidian vault. You can read, search and edit your library in Obsidian while
the app is running. The app notices changed files and picks them up on the next request.

## Open it

1. In Obsidian: **Open folder as vault** and choose **Documents › research brain** (in the app:
   **Settings › Library settings › Your documents** shows where it is).
2. Optional, in **Settings → Files and links**:
   - **Default location for new notes**: `Notes`. A new file in `Notes/` (or any subfolder of it) becomes a
     note in the app; `Papers/`, `Questions/` and `Highlights/` hold those kinds.
   - **Excluded files**: `PDFs` if you don't want the PDFs in search.
3. Obsidian creates `.obsidian/` for its own settings. The app ignores it.

The app's own data (search index, extracted text, review history, trash) is in a separate folder and
isn't part of the vault.

## What a note looks like

`Papers/Attention Is All You Need (2017).md`:

```markdown
---
id: attention-is-all-you-need-2017
type: paper
title: Attention Is All You Need
authors: [Ashish Vaswani, Noam Shazeer]
year: 2017
arxiv: '1706.03762'
summary: Replaces recurrence with self-attention; see [[debye-shielding|Debye shielding]].
tags: [ml]
links: [bert-pre-training-2019]
created: '2026-11-09T18:48:14-08:00'
updated: '2026-11-10T09:12:01-08:00'
status: read
---
Your free-form notes (the "Notes" box in the app), with $inline$ and $$display$$ math.
```

Files are named after their titles (a paper's also carries its year). The `id:` is what links, topics and
citations refer to; properties that are empty are left out.

## What works

- **Renaming and moving files.** The app finds each note by the `id:` inside it, so renaming a file, or
  moving a note into a subfolder of `Notes/`, breaks nothing. When you change a note's title in the app,
  its file is renamed to match (and a paper's highlights folder follows).
- **Links by title or file name.** `[[Title]]` resolves in both apps when it matches a file name; for a
  paper, Obsidian needs the year too, `[[Attention Is All You Need (2017)]]`, which the app also resolves.
- **Body links, backlinks and the graph view** work for anything written in the note body.
- **Math.** `$...$` and `$$...$$` render in Obsidian with MathJax, so the same LaTeX displays in both
  apps (a few KaTeX-only commands may differ).
- **Properties.** Obsidian shows the frontmatter as properties: `title`, `authors`, `year`, `tags`,
  `doi`, `summary` and so on. **Tags** in the `tags` property appear in Obsidian's tag pane.
- **Editing properties in Obsidian is safe.** Obsidian rewrites the frontmatter in its own style
  (block lists, different quoting, empty values for nulls, unquoted dates). The app reads all of these
  identically; `tests/test_obsidian.py` checks this, including Obsidian's unquoted date-time values.
- **Extra properties you add** (`aliases`, `cssclasses`, your own fields) are ignored by the app but
  preserved: when the app saves a note it writes them back unchanged.
- **Creating notes in Obsidian** works, with any file name: a new file in `Notes/` shows up in the app as a
  note (its id comes from the file name, and is written into the file the next time the app saves it).
  Missing fields get defaults (`type: note`, title = file name).
- **PDFs** are in `PDFs/<id>.pdf`; Obsidian's built-in PDF viewer opens them.

## What doesn't (or works differently)

- **The app's `[[note-id|Title]]` links don't resolve in Obsidian**, since file names are titles now.
  They still work in the app. The app's autocomplete writes `[[Title]]` when the title is unique.
- **Links in `summary` and highlight quotes aren't links in Obsidian.** Obsidian only treats `[[...]]` in
  the body (and in list-type link properties) as links. The app's summary lives in frontmatter, so
  Obsidian shows it as text, and those mentions don't appear in Obsidian's backlinks or graph. The app's
  own "Mentioned in" list covers all fields.
- **Manual links (`links:`) are ids, not wikilinks.** Obsidian shows them as plain text in the
  properties panel and they don't draw edges in Obsidian's graph.
- **Highlights are separate files**, in `Highlights/<paper>/`, each with a `parent:` id. Obsidian lists
  them like any other note.
- **Copying a file copies its id.** Two files with the same `id:` are one note to the app (the first one
  found is used). To make a copy a note of its own, delete the `id:` line from the copy.
- **Critiques, embeddings and explanations** are app features. Critiques are stored in the note's
  frontmatter (`critiques:`), so they're visible but not rendered nicely in Obsidian.
- **Don't edit the same note in both apps at once.** The app autosaves about a second after you stop
  typing; whichever app saves last wins.
