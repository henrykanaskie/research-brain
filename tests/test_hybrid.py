"""Hybrid passage retrieval: BM25 exact-term matching fused with dense ranks (RRF)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _index(tmp_path, monkeypatch, docs):
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    from app.chunks import ChunkIndex
    from app.embed import Embedder
    from app.store import Store
    s = Store(tmp_path)
    for title, body in docs:
        s.create({"type": "note", "title": title, "body": body})
    return ChunkIndex(s, Embedder(s.index_dir)), s


def test_exact_numbers_and_subscripts_win(tmp_path, monkeypatch):
    filler = [(f"Plasma note {i}", "Krypton emission spectroscopy of a Hall thruster plume, line ratios and "
               "electron temperature estimates from collisional radiative modelling. " * 3) for i in range(25)]
    target = ("Line choice", "We use the 811.3 nm transition because its upper level is fed by metastables; "
              "T_e enters through the excitation rate.")
    idx, s = _index(tmp_path, monkeypatch, filler + [target])
    hits = idx.search("811.3 nm line", k=5)
    assert hits and s.get(hits[0]["id"])["title"] == "Line choice"
    hits = idx.search("how does T_e enter", k=5)
    assert s.get(hits[0]["id"])["title"] == "Line choice"


def test_latex_commands_are_searchable(tmp_path, monkeypatch):
    idx, s = _index(tmp_path, monkeypatch, [
        ("Gradient note", r"The pressure term is $\nabla p$ and balances $\mathbf{j}\times\mathbf{B}$."),
        ("Other", "Ion acoustic waves propagate at the sound speed in the presheath region."),
    ])
    hits = idx.search(r"\nabla p", k=2)
    assert s.get(hits[0]["id"])["title"] == "Gradient note"


def test_rrf_scores_are_monotonic_and_diverse(tmp_path, monkeypatch):
    docs = [("Long paper", ("Bohm criterion sheath entry speed. " * 40 + "\n\n") * 6), ("Short", "Bohm criterion.")]
    idx, s = _index(tmp_path, monkeypatch, docs)
    hits = idx.search("Bohm criterion", k=10, per_note=2)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    from collections import Counter
    assert max(Counter(h["id"] for h in hits).values()) <= 2
