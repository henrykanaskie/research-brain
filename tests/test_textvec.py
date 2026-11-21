"""Text vectors and passage search: TF-IDF and word counts in plain numpy (app/textvec.py), which replaced
scikit-learn and SciPy, and hybrid passage retrieval (app/chunks.py): BM25 exact-term matching fused with
dense ranks (RRF).

Run:  .venv/bin/python -m pytest -q tests/test_textvec.py
"""
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import textvec as tv  # noqa: E402

DOCS = ["Helium line ratios in a plasma", "machine learning for helium line emission",
        "the plasma electron density", "stochastic parrots and language models"]


def test_tfidf_and_counts_by_hand_and_row_operations():
    """Smoothed idf, rows L2-normalised, stop words gone (sklearn's list), terms in sorted order; bigrams skip
    stop words, sublinear tf, word counts; document frequency limits and empty vocabularies raise; the sparse
    rows' dot products, gram matrix, sums, slices and nnz match the dense arrays."""
    v = tv.TfidfVectorizer(stop_words="english")
    m = v.fit_transform(["plasma helium", "plasma"])
    assert v.get_feature_names_out() == ["helium", "plasma"]
    idf_h, idf_p = math.log(3 / 2) + 1, math.log(3 / 3) + 1
    row = np.array([idf_h, idf_p]) / math.hypot(idf_h, idf_p)
    assert np.allclose(m.toarray(), [row, [0, 1]])
    assert np.allclose(v.idf_, [idf_h, idf_p])
    assert len(tv.ENGLISH_STOP_WORDS) == 318 and {"the", "and", "whereupon"} <= tv.ENGLISH_STOP_WORDS

    # bigrams skip stop words, sublinear tf; plain counts
    v = tv.TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    v.fit_transform(["the helium of the plasma plasma"])
    assert v.get_feature_names_out() == ["helium", "helium plasma", "plasma", "plasma plasma"]
    c = tv.CountVectorizer(stop_words="english")
    assert c.fit_transform(["plasma plasma helium"]).toarray().tolist() == [[1, 2]]

    # document frequency limits
    v = tv.TfidfVectorizer(max_df=0.5)  # a term in more than half the documents goes
    v.fit_transform(["plasma helium", "plasma neon", "plasma argon"])
    assert "plasma" not in v.vocabulary_ and "helium" in v.vocabulary_
    with pytest.raises(ValueError):
        tv.TfidfVectorizer(stop_words="english").fit_transform(["the and of", ""])
    with pytest.raises(ValueError):
        tv.TfidfVectorizer(max_df=0.1).fit_transform(["plasma", "plasma"])

    # row operations
    v = tv.TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    m = v.fit_transform(DOCS)
    d = m.toarray()
    q = v.transform(["helium plasma"])
    assert np.allclose(m.dot_row(q), d @ q.toarray()[0])
    assert np.allclose(m[1:].dot_row(m[0:1]), d[1:] @ d[0])
    assert np.allclose(m.gram(), d @ d.T) and np.allclose(np.diag(m.gram()), 1.0)
    assert np.allclose(m.row_sums(), d.sum(axis=1))
    assert m[2:3].shape == (1, d.shape[1]) and m.nnz == int((d != 0).sum())
    assert v.transform(["zzz unknown words"]).nnz == 0


def test_same_as_scikit_learn():
    """Scores (and the thresholds tuned on them) didn't move: same numbers as scikit-learn, where it's
    installed (it no longer is a dependency)."""
    sk = pytest.importorskip("sklearn.feature_extraction.text")
    from app.chunks import BM25_TOKENS
    docs = DOCS * 3 + ["T_e = 811.3 eV and \\nabla n_e", "Ünïcödé café", ""]
    for kw in (dict(stop_words="english", sublinear_tf=True, ngram_range=(1, 2), max_df=0.9),
               dict(stop_words="english", ngram_range=(1, 2), sublinear_tf=True,
                    token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]{2,}\b")):
        a, b = sk.TfidfVectorizer(**kw), tv.TfidfVectorizer(**kw)
        assert np.allclose(a.fit_transform(docs).toarray(), b.fit_transform(docs).toarray()), kw
        assert list(a.get_feature_names_out()) == b.get_feature_names_out(), kw
    a = sk.CountVectorizer(token_pattern=BM25_TOKENS, stop_words="english")
    b = tv.CountVectorizer(token_pattern=BM25_TOKENS, stop_words="english")
    assert (a.fit_transform(docs).toarray() == b.fit_transform(docs).toarray()).all()


# ---------------------------------------------------------------- hybrid passage retrieval
def _index(root, monkeypatch, docs):
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    from app.chunks import ChunkIndex
    from app.embed import Embedder
    from app.store import Store
    s = Store(root)
    for title, body in docs:
        s.create({"type": "note", "title": title, "body": body})
    return ChunkIndex(s, Embedder(s.index_dir)), s


def test_hybrid_search_finds_exact_terms_and_latex_and_ranks_diversely(tmp_path, monkeypatch):
    """Exact numbers and subscripts win over a crowd of similar notes, LaTeX commands are searchable, and the
    fused scores come out in decreasing order with at most per_note passages from one note."""
    filler = [(f"Plasma note {i}", "Krypton emission spectroscopy of a Hall thruster plume, line ratios and "
               "electron temperature estimates from collisional radiative modelling. " * 3) for i in range(25)]
    target = ("Line choice", "We use the 811.3 nm transition because its upper level is fed by metastables; "
              "T_e enters through the excitation rate.")
    idx, s = _index(tmp_path / "numbers", monkeypatch, filler + [target])
    hits = idx.search("811.3 nm line", k=5)
    assert hits and s.get(hits[0]["id"])["title"] == "Line choice"
    hits = idx.search("how does T_e enter", k=5)
    assert s.get(hits[0]["id"])["title"] == "Line choice"

    idx, s = _index(tmp_path / "latex", monkeypatch, [
        ("Gradient note", r"The pressure term is $\nabla p$ and balances $\mathbf{j}\times\mathbf{B}$."),
        ("Other", "Ion acoustic waves propagate at the sound speed in the presheath region."),
    ])
    hits = idx.search(r"\nabla p", k=2)
    assert s.get(hits[0]["id"])["title"] == "Gradient note"

    docs = [("Long paper", ("Bohm criterion sheath entry speed. " * 40 + "\n\n") * 6), ("Short", "Bohm criterion.")]
    idx, s = _index(tmp_path / "rrf", monkeypatch, docs)
    hits = idx.search("Bohm criterion", k=10, per_note=2)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert max(Counter(h["id"] for h in hits).values()) <= 2
