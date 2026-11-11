# CSL styles and locale (vendored)

The files in `styles/` and `locales/` are unmodified copies from the official Citation Style Language
repositories, downloaded on 2026-11-10:

- styles: https://github.com/citation-style-language/styles (branch `master`)
  - `american-physics-society.csl` (American Physical Society)
  - `american-institute-of-physics.csl` (AIP Style Manual 4th edition)
  - `ieee.csl` (IEEE Reference Guide version 11.29.2023)
  - `american-institute-of-aeronautics-and-astronautics.csl` (American Institute of Aeronautics and Astronautics)
  - `nature.csl` (Nature)
  - `apa.csl` (APA Style 7th edition)
  - `chicago-author-date.csl` (Chicago Manual of Style 18th edition, author-date)
- locale: https://github.com/citation-style-language/locales (branch `master`): `locales-en-US.xml`

Each file names its authors and contributors in its `<info>` block. They are licensed under the
**Creative Commons Attribution-ShareAlike 3.0 Unported License** (CC BY-SA 3.0),
https://creativecommons.org/licenses/by-sa/3.0/ , as each file's `<rights>` element states. If you change
one, keep the attribution and share your version under the same licence.

research brain itself is not covered by this licence; it only reads these files to format citations
(with citeproc-py, BSD 2-Clause).

To refresh: download the same file names from the repositories above and run the citation tests
(`python3 -m pytest -q tests/test_cite.py`).
