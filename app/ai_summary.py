"""AI summary of a paper: a thorough, structured explainer written by the active AI agent, with diagrams
(```mermaid / ```svg blocks, drawn by static/app.js) when the agent can make them.

It is the AI's reading of the paper, kept apart from the student's own summary (which stays theirs to write
and is what the critique checks). Stored in the library folder, not in the note's file, so a long summary with
diagrams doesn't bloat the Markdown note you read in Obsidian:
    state/ai-summaries/<id>.json   {"text", "at", "agent", "paper_chars", "source": "full"|"digest"|"abstract"}
Backed up with state/. Asking again for an unchanged paper shows the stored one (nothing is sent) unless
`refresh` is passed.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import fsio
from .store import now_iso

DIR = "ai-summaries"

INSTRUCTION = (
    "Write an excellent, thorough summary of the PAPER (on stdin) for a graduate student who wants to understand "
    "it deeply without reading it end to end. Be accurate and specific: real numbers, names of methods, datasets, "
    "equations and conditions, never vague praise. Cite pages as (p. N) from the [[page N]] markers. Use exactly "
    "these Markdown sections (## headings), skipping one only if the paper truly has nothing for it:\n"
    "## In one paragraph\nThe problem, what they did, what they found and why it matters, in 4 to 6 sentences.\n"
    "## Background and motivation\nWhat was known, what gap or puzzle this paper addresses, and why it is hard.\n"
    "## Approach\nThe method or argument step by step: the key idea first, then how it works. Give the central "
    "equations in LaTeX and explain every symbol.\n"
    "## Setup\nExperiments, simulations, data, theory assumptions or apparatus: what exactly was done.\n"
    "## Key results\nThe main findings as a list, each with its numbers and where it appears (figure, table, "
    "page). A Markdown table when comparing results.\n"
    "## Contributions\nWhat is genuinely new here, compared with earlier work.\n"
    "## Limitations and open questions\nAssumptions, weaknesses, what the authors admit and what they don't, "
    "and natural next steps.\n"
    "## Key terms\nA short glossary of the terms and symbols a reader must know.\n"
    "## Check your understanding\nThree pointed questions a reader who understood the paper could answer.\n"
)

DIAGRAMS = (
    "DIAGRAMS: include 1 to 3 diagrams where they genuinely help understanding, placed in the section they "
    "belong to: for example the method's pipeline or architecture as a flowchart, the causal chain or physical "
    "mechanism, the experimental setup, a timeline, or a chart of the main quantitative results. Write each as a "
    "```mermaid block (flowchart, sequenceDiagram, timeline, mindmap, pie or xychart-beta; keep labels short and "
    "plain, put each label in double quotes, no Markdown or math inside labels; it is shown in a narrow sidebar, "
    "so lay flowcharts out top-down (flowchart TD) with at most 4 nodes side by side) or, for a custom figure Mermaid "
    "can't draw, a ```svg block (self-contained, viewBox set, no scripts, links or external images). Every "
    "diagram must be valid syntax and must reflect the paper's actual content, never a generic illustration. "
    "Follow each with one italic line saying what it shows.")

NO_DIAGRAMS = "Do not draw diagrams; use lists and tables instead."


def instruction(diagrams: bool, abstract_only: bool) -> str:
    out = INSTRUCTION + (DIAGRAMS if diagrams else NO_DIAGRAMS)
    if abstract_only:
        out += (" ONLY THE ABSTRACT IS AVAILABLE (no PDF attached): summarise what it supports, keep the sections "
                "short, and say plainly which parts can't be covered without the full text. Never invent details.")
    return out


def digest(text: str, cap: int) -> str:
    """A paper longer than `cap` characters, cut to fit: its opening (abstract, introduction, method), evenly
    spaced passages through the middle (results), and its end (discussion, conclusions)."""
    head, tail = text[: int(cap * 0.4)], text[-int(cap * 0.2):]
    middle = text[len(head): len(text) - len(tail)]
    room = cap - len(head) - len(tail)
    parts, n = [], 6
    if room > 0 and middle:
        size = room // n
        step = max(size, len(middle) // n)
        for i in range(n):
            piece = middle[i * step: i * step + size]
            if piece.strip():
                parts.append(piece)
    return ("THE PAPER IS LONG, SO THIS IS ITS OPENING, EVENLY SPACED PASSAGES FROM THE MIDDLE, AND ITS END "
            "(page markers [[page N]] kept). Say so where a point needs a part you can't see.\n\nOPENING:\n" + head
            + "\n\n[…]\n\n" + "\n\n[…]\n\n".join(parts) + f"\n\n[…]\n\nEND:\n{tail}")


def _path(root: Path, nid: str) -> Path:
    return Path(root) / "state" / DIR / f"{nid}.json"


def load(root: Path, nid: str) -> dict | None:
    p = _path(root, nid)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("text"), str) and d["text"].strip() else None


def save(root: Path, nid: str, text: str, *, agent: str, paper_chars: int, source: str) -> dict:
    rec = {"text": text, "at": now_iso(), "agent": agent, "paper_chars": paper_chars, "source": source}
    p = _path(root, nid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        fsio.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return rec


def delete(root: Path, nid: str) -> bool:
    p = _path(root, nid)
    if p.exists():
        p.unlink()
        return True
    return False
