"""Synthetic library generator for the scale tests (tests/test_scale.py).

Writes notes straight to vault/notes/*.md in the Store's own format, deterministically, so the
benchmark measures reading/indexing rather than 2,300 individual API round trips.
"""
from __future__ import annotations

import random
from pathlib import Path

import yaml

TOPICS = {
    "plasma": "plasma sheath electron ion density hall thruster discharge oscillation breathing mode xenon krypton",
    "ml": "neural network transformer attention gradient training loss embedding representation dataset",
    "robotics": "robot manipulator control trajectory planning grasp kinematics actuator sensor feedback",
    "optics": "laser emission spectroscopy line intensity collisional radiative model excitation population",
    "cv": "image camera segmentation detection convolution pixel video tracking monitoring",
    "rl": "reinforcement learning policy reward agent value function exploration environment",
}


def _sentence(rng: random.Random, words: list[str], n: int) -> str:
    return " ".join(rng.choice(words) for _ in range(n)).capitalize() + "."


def write_library(vault: Path, n_notes: int = 2000, n_fragments: int = 300, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    notes_dir = vault / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    topic_names = list(TOPICS)
    ids: list[str] = []
    titles: list[str] = []
    for i in range(n_notes):
        topic = topic_names[i % len(topic_names)]
        words = TOPICS[topic].split()
        other = TOPICS[rng.choice(topic_names)].split()
        title = f"{_sentence(rng, words, 4)[:-1]} study {i}"
        nid = f"syn-{i:05d}"
        typ = "paper" if i % 3 else "note"
        links = [f"syn-{j:05d}" for j in rng.sample(range(n_notes), 2)] if i % 17 == 0 else []
        body = " ".join(_sentence(rng, words + other, 12) for _ in range(rng.randint(1, 4)))
        if i % 11 == 0 and titles:
            body += f" See [[{rng.choice(titles)}]] and [[syn-{rng.randrange(max(1, i)):05d}]]."
        meta = {
            "type": typ, "title": title, "authors": [f"Author {rng.randint(1, 400)}", f"Coauthor {rng.randint(1, 400)}"],
            "year": 1995 + i % 30, "venue": "", "doi": f"10.1000/syn.{i}" if typ == "paper" else "",
            "arxiv": f"2{i % 10}01.{i:05d}" if typ == "paper" and i % 2 else "", "s2_id": "",
            "url": f"https://example.org/paper/{i}" if i % 5 == 0 else "",
            "abstract": " ".join(_sentence(rng, words, 14) for _ in range(3)) if typ == "paper" else "",
            "summary": _sentence(rng, words + other, 20) if i % 2 else "",
            "tags": [topic, rng.choice(words)], "links": links,
            "created": f"2025-01-01T00:00:{i % 60:02d}+00:00", "updated": f"2025-06-{1 + i % 28:02d}T00:00:00+00:00",
            "parent": "", "quote": "", "locator": "", "page": None, "zotero_key": "",
        }
        _write(notes_dir / f"{nid}.md", meta, body)
        ids.append(nid)
        titles.append(title)
    # manual links here are one-sided; the graph dedupes by pair, so that's fine for benchmarking
    for k in range(n_fragments):
        parent = ids[(k * 7) % n_notes]
        topic = topic_names[((k * 7) % n_notes) % len(topic_names)]
        words = TOPICS[topic].split()
        fid = f"{parent}-frag-{k}"
        meta = {
            "type": "fragment", "title": f"Fragment {k}", "authors": [], "year": None, "venue": "", "doi": "",
            "arxiv": "", "s2_id": "", "url": "", "abstract": "", "summary": "", "tags": [], "links": [],
            "created": f"2025-02-01T00:00:{k % 60:02d}+00:00", "updated": "2025-07-01T00:00:00+00:00",
            "parent": parent, "quote": _sentence(rng, words, 15), "locator": f"p. {k % 12 + 1}",
            "page": k % 12 + 1, "zotero_key": "",
        }
        _write(notes_dir / f"{fid}.md", meta, _sentence(rng, words, 10))
        ids.append(fid)
    return ids


def _write(path: Path, meta: dict, body: str) -> None:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    path.write_text(f"---\n{front}---\n{body}\n", encoding="utf-8")
