# Using the vault in Obsidian

research_brain stores every note as a plain Markdown file with YAML frontmatter, so `vault/` opens
directly as an Obsidian vault. You can read, search and edit your library in Obsidian while the app is
running. The app notices changed files and picks them up on the next request.

## Open it

1. In Obsidian: **Open folder as vault** and choose `research_brain/vault` (or wherever `RB_VAULT` points).
2. Optional but recommended, in **Settings → Files and links**:
   - **Excluded files**: add `.index`, `.trash`, `text`, `exports`. These are derived or archived data
     (embeddings, deleted notes, extracted PDF text, export sites) and only add noise to search and the
     graph.
   - **Default location for new notes**: `notes`. The app only reads notes from `vault/notes/`.
   - **New link format**: *Shortest path when possible* (the default) is right.
3. Obsidian creates `vault/.obsidian/` for its own settings. The app ignores it. If you use
   `scripts/vault_backup.sh`, it gets committed with your notes, which is usually what you want.

## What a note looks like

```markdown
---
type: paper
title: Attention Is All You Need
authors:
- Ashish Vaswani
- Noam Shazeer
year: 2017
doi: ''
arxiv: '1706.03762'
summary: Replaces recurrence with self-attention; see [[debye-shielding|Debye shielding]].
tags:
- ml
links:
- bert-pre-training-2019
created: '2026-11-09T18:48:14-08:00'
...
---
Your free-form notes (the "Notes" box in the app), with $inline$ and $$display$$ math.
```

The file name is the note's id: `vault/notes/attention-is-all-you-need-2017.md`.

## What works

- **Links resolve.** The app writes `[[note-id]]`, `[[note-id|Title]]` or `[[Title]]`. Because the id is
  the file name, Obsidian resolves `[[note-id|Title]]` to the right file and shows "Title". `[[Title]]`
  resolves in Obsidian only when it matches a file name, so the app's autocomplete inserts the
  `[[id|Title]]` form whenever a plain title would be ambiguous.
- **Body links, backlinks and the graph view** work for anything written in the note body.
- **Math.** `$...$` and `$$...$$` render in Obsidian with MathJax, so the same LaTeX displays in both
  apps (a few KaTeX-only commands may differ).
- **Properties.** Obsidian shows the frontmatter as properties: `title`, `authors`, `year`, `tags`,
  `doi`, `summary` and so on. **Tags** in the `tags` property appear in Obsidian's tag pane.
- **Editing properties in Obsidian is safe.** Obsidian rewrites the frontmatter in its own style
  (block lists, different quoting, empty values for nulls, unquoted dates). The app reads all of these
  identically; `tests/test_obsidian.py` checks this, including Obsidian's unquoted date-time values.
- **Extra properties you add** (`aliases`, `cssclasses`, your own fields) are ignored by the app but
  preserved: when the app saves a note it writes them back unchanged (dates come back as ISO strings,
  which Obsidian still reads as dates). `tests/test_obsidian.py` checks this.
- **Creating notes in Obsidian** works: a new file in `notes/` shows up in the app as a note. The app
  fills in missing fields with defaults (`type: note`, title = file name).
- **PDFs** live in `vault/pdfs/<id>.pdf`; Obsidian's built-in PDF viewer opens them.

## What doesn't (or works differently)

- **Links in `summary` and fragment quotes aren't links in Obsidian.** Obsidian only treats `[[...]]` in
  the body (and in list-type link properties) as links. The app's summary lives in frontmatter, so
  Obsidian shows it as text, and those mentions don't appear in Obsidian's backlinks or graph. The app's
  own "Mentioned in" list covers all fields.
- **Manual links (`links:`) are ids, not wikilinks.** Obsidian shows them as plain text in the
  properties panel and they don't draw edges in Obsidian's graph.
- **Fragments are separate files.** Each fragment is its own note in `notes/` with `type: fragment` and
  a `parent:` id. Obsidian lists them like any other note; it doesn't nest them under the paper.
- **Renaming a file in Obsidian changes the note's id.** The app treats the renamed file as a new note:
  manual links, fragments' `parent`, and `[[old-id]]` links still point at the old id and stop
  resolving. Change the **title** property instead (the app then rewrites `[[Old title]]` links for you
  when the title is edited in the app; Obsidian won't do that for titles).
- **File names must stay lowercase `a-z`, `0-9` and `-`.** Files named any other way (e.g.
  `My Idea.md` created in Obsidian) are skipped by the app. Name new notes like `my-idea.md` and set
  the `title` property for the display name.
- **Critiques, embeddings and explanations** are app features. Critiques are stored in the note's
  frontmatter (`critique:`), so they're visible but not rendered nicely in Obsidian.
- **Don't edit the same note in both apps at once.** The app autosaves about a second after you stop
  typing; whichever app saves last wins.
