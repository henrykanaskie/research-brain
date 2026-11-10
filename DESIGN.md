# research_brain — visual design (replaces the earlier navy/cyan "blueprint" look)

The user rejected the neon cyan-on-navy look: it read as "vibecoded". The new direction is
elegant and technical, like a carefully annotated printout of a paper on a drafting table.
Restraint everywhere. No glows, gradients, neon, drop shadows, or emoji.

## Reference: land-book.com (the user's own pick)
He pointed to the curated sites on land-book.com as his bar for taste. What those sites share,
and what this app should match:
- Light first. A crisp near-white canvas with white surfaces, not grey slabs. Generous
  whitespace: padding of 24–32px around panel content and 48px+ breathing room in empty states.
- Confident typography does the work. Large, tight titles (paper title 26–28px, line-height
  1.15, slight negative tracking) against small, quiet UI text. Hierarchy comes from size and
  weight, not boxes.
- Very few borders. Separate regions with whitespace and one hairline divider. No box around
  every section. Inputs are borderless until hover or focus, or use a single very light border.
- Product UI polish: precise 4px spacing grid, aligned left edges, consistent 32px control height,
  subtle hover states (background tint, no colour flips), and crisp focus rings.
- One memorable element and nothing loud. Here that element is the red-pen encoding below.

Token adjustments from the land-book pass (these override the light values listed further down):
`--ground: #F6F7F8; --sheet: #FFFFFF; --rule: #E6E8EB`.

## Core idea: red pen on graphite
Colour carries meaning. **Graphite ink = source material** (papers, abstracts, paper text).
**Carmine red = the user's own thinking** (his summaries, notes, fragments, and the links he
made). The graph should make it obvious where his understanding has been added, the way a
red pen does on a printout. Semantic (machine-found) relations are a quiet blue-grey.

## Tokens (CSS custom properties on :root, with a dark variant)
Light (default):
- `--ground: #EDEFF1` (cool drafting-film grey; the app background and graph field)
- `--sheet: #FAFAFA` (panels and paper-like surfaces)
- `--ink: #1E2226` (text and source-material nodes)
- `--ink-2: #5A6169` (secondary text: authors, years, metadata)
- `--rule: #D3D7DC` (1px borders and dividers)
- `--red: #B23A33` (the user's marks)  `--red-wash: rgba(178,58,51,.10)`
- `--machine: #7D8A98` (semantic-similarity lines, scores, AI-generated text accents)

Dark (`@media (prefers-color-scheme: dark)`, also `:root[data-theme=dark]`):
`--ground #16191D; --sheet #1D2126; --ink #E4E7EA; --ink-2 #9AA2AB; --rule #2E343B;
 --red #E06A60; --red-wash rgba(224,106,96,.14); --machine #7F8C9A`.
Add a small light/dark/system toggle in the top bar.

## Type (Google Fonts)
- **STIX Two Text** (400, 400 italic, 600): paper titles, abstracts, quotes, note bodies,
  Claude's answers. It's the scientific-publishing face and sits next to KaTeX math without
  clashing. Reading text 16px/1.6, measure under 70 characters.
- **Instrument Sans** (400, 500, 600): all interface chrome (buttons, inputs, labels, graph
  labels, metadata). 13px base for UI, 11.5px for graph labels. Use `font-variant-numeric:
  tabular-nums` for years, scores, page numbers.
- No monospace for labels. Use mono (system `ui-monospace`) only for DOIs and arXiv ids.
- Sentence case everywhere. No ALL-CAPS or letter-spaced eyebrow labels, no "01 / 02"
  numbering, no "A · B · C" metadata strings (use separate spans with spacing or commas), and
  no "→" glued onto button text.

## Shapes and structure
- Radius: 3px on inputs and buttons, 0 on panels. Borders: 1px `--rule`. No shadows, except
  one hairline `--rule` divider where the side panel meets the graph.
- Buttons: quiet text buttons with a 1px border. Primary actions (Critique, Save) are `--ink`
  filled with `--sheet` text. Anything destructive is plain text in `--red` and needs
  confirmation.
- The user's summary block and fragment quotes get a 2px `--red` left rule (red-pen margin
  mark). Paper abstracts get no rule. Claude output (critique, explanation, chat) sits on
  `--ground` with a 2px `--machine` left rule, labelled plainly, e.g. "Claude's critique,
  2 min ago".
- Graph field: `--ground` with a very faint dot grid (1px dots every 24px at ~5% ink opacity)
  that pans and zooms with the graph if feasible; otherwise keep it static and fainter.

## Graph encoding
- Paper: filled `--ink` circle, radius 4–9 by degree.
- Plain note: hollow ring, 1.5px `--ink` stroke, same size rule.
- Fragment: small 3px filled `--red` dot, close to its parent.
- Manual link (user made it): 1.2px solid `--red`.
- Part link (fragment to parent): 0.75px solid `--ink` at 35% opacity, short distance.
- Semantic similarity: 0.75px dashed `--machine`, opacity mapped to score (0.25–0.8).
- Labels: Instrument Sans 11.5px in `--ink-2`. Show them for hovered, selected, and
  high-degree nodes, and for all nodes once zoomed in. Paper labels are truncated titles;
  fragment labels only on hover.
- Selection: 2px `--red` ring around the node, with its neighbours at full opacity and
  everything else dimmed to 25%. No glow, no pulsing.
- Motion: only physics settling, the panel opening (150ms ease-out), and centring on click.
  Respect `prefers-reduced-motion`.

## Copy
Plain, specific, sentence case. Empty graph: "Drop a PDF here, or add a paper by DOI, arXiv id
or title." Errors say what happened and what to do, e.g. "Claude CLI isn't logged in. Run
`claude` in Terminal and sign in." Buttons name the action: "Critique my summary", "Add
fragment", "Find related papers", "Ask your library".

## PDF viewer (viewer.html) and extension popup
Use the same tokens and fonts. In the viewer, pages are `--sheet` white with a 1px `--rule`
border on the `--ground` field. Saved fragments are highlighted with `--red-wash` plus a 1px
`--red` underline, not yellow. The selection "+ Note" control is a small `--ink` button.
The sidebar matches the app's side panel. In the extension popup, the quote gets the red
margin rule, inputs use the app styles, and Save is the primary ink button.

## Graph rendering (current; supersedes "Graph encoding" and the earlier dendrite/dot-lattice passes)
A computer brain: a circuit board grown like nervous tissue. No curves anywhere in the graph.
- Traces: every link is routed octilinearly (straight runs joined by one 45-degree diagonal), hairline
  width fixed in screen pixels, with a tiny via at each bend. Where the straight run splits varies per
  link so parallel traces separate. Your links are carmine, citations ink, machine similarity cobalt
  at low opacity. Each node shows only its 2 strongest similarity links.
- Somas: small solid ink dot for a paper, hairline ring for a note, tiny red point for a fragment.
- No decoration around somas. Size shows engagement (words written, opens, links); a small red core
  means you have written about the note.
- Field: plain `--ground`, no pattern. Fine measuring ticks run along the top and left edges of the
  plate and move with pan and zoom.
- Regions: Bodoni Moda italic names with the member count in small Archivo, placed above the region,
  no outlines or fills.
- Selection: a hairline reticle (circle and four ticks); the label switches to Bodoni italic.
- Motion: one signal per click along the traces, optional quiet ambient signals; off under
  prefers-reduced-motion.

## Type (current; supersedes "Type" above)
- Bodoni Moda (variable, optical size): wordmark, paper titles, region names, drawer and dialog titles.
- Archivo (variable width): all interface text; labels and controls at 88% width.
- STIX Two Text: reading text (abstracts, summaries, notes, Claude's answers), because it matches KaTeX.
- Tokens: `--ground #EEEFEB` vellum, `--sheet #F7F7F4`, `--ink #15181B`, `--ink-2 #61676C`, `--ink-3 #9A9EA0`,
  `--rule #D5D7D1`, `--red #BD3A2C`, `--machine #4A6487` (dark values in style.css).

## Commands and shortcuts
Every user-facing action is a command in `static/commands.js` (loaded first, also by the viewer).
Register it with `RB.commands.register({id, title, group, keys, description, aliases, when, run})`,
guarded by `window.RB && RB.commands`. The registry owns the only global keydown dispatcher, the
command palette (Mod+K), the "?" shortcuts sheet and the chord hint, so nothing else needs its own
listener.
- Every new action must be registered, with a sentence-case title that names the action and a one-line
  description. Give it a key only if it is frequent; add aliases for the words people will search for.
- Groups: General, Navigate, Library, Note, Graph, Review, Research, Viewer, Extension.
- Keys use Mac-first notation: "Mod+K" (⌘ on Mac, Ctrl elsewhere), "Shift+L", "Alt+R", "G then T"
  (chords, 800 ms). Plain letters never fire inside a text field; `allowInInput` only lets modifier,
  arrow, Enter and Esc keys through. Editor popups (LaTeX, [[links]]) keep Tab, Enter, arrows and Esc.
- Esc: don't listen for it. Register a layer with `RB.commands.layer({id, order, isOpen, close})`;
  Esc closes the open layer with the highest order (dialog 100, menus 90, explanation 70, chat 50,
  panel 40).
- No two commands may share keys in the same scope; `tests/e2e/test_shortcuts.py` checks
  `RB.commands.conflicts()`.
- Show the key where the action lives: `RB.commands.tooltip(button, id)` adds it to the title.
- Keycaps: small bordered rounded boxes in Instrument Sans (not mono), 11.5px, `--rule` border.
- The palette, sheet, coach marks and chord hint are white sheets with hairline rules; the subtle
  menu shadow is the only shadow.
