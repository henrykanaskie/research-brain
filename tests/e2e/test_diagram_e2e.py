"""Diagrams in answers (static/app.js drawVisuals): mermaid reads labels as Markdown, so a label that starts like
a list or heading ("1. Fit", "- check", "# done?") used to come out as "Unsupported markdown: list". They draw
with their own text now."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_e2e import api, browser, clear_vault, ctx, open_app, server  # noqa: E402,F401  (fixtures are used by name)

DIAGRAMS = [
    'flowchart TD\n  A["1. Collect spectra"] --> B["2. Fit the CRM"]\n  B --> C["- check residuals"]',
    'flowchart LR\n  A[1. Measure] -->|2. compare| B(* model)\n  B --> C{# done?}',
    'mindmap\n  root((Cross sections))\n    1. Theory\n    2. Experiment',
]


def test_list_like_labels_draw(ctx, server):
    clear_vault(server)
    ans = "\n\n".join(f"```mermaid\n{d}\n```" for d in DIAGRAMS)
    api(server, "PUT", "/api/chats/library", {"messages": [{"role": "user", "content": "Draw it"},
                                                              {"role": "assistant", "content": ans}], "title": "Ask your library"})
    page = open_app(ctx, server)
    page.click("#btn-chat")
    page.wait_for_function("document.querySelectorAll('#chat .md-visual-mermaid svg').length === 3", timeout=20000)
    texts = page.evaluate("""() => [...document.querySelectorAll('#chat .md-visual-mermaid')].map((v) =>
      [...v.querySelectorAll('svg .nodeLabel, svg .edgeLabel, svg text')].map((t) => t.textContent).join(' ').replace(/\\u00a0/g, ' '))""")
    joined = " ".join(texts)
    assert "Unsupported markdown" not in joined
    for label in ["1. Collect spectra", "- check residuals", "2. compare", "* model", "# done?", "1. Theory"]:
        assert label in joined, label
    assert not page.errors
