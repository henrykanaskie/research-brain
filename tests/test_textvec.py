"""TF-IDF and word counts in plain numpy (app/textvec.py), which replaced scikit-learn and SciPy.

Run:  .venv/bin/python -m pytest -q tests/test_textvec.py
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import textvec as tv  # noqa: E402

DOCS = ["Helium line ratios in a plasma", "machine learning for helium line emission",
        "the plasma electron density", "stochastic parrots and language models"]


def test_tfidf_by_hand():
    """Smoothed idf, rows L2-normalised, stop words gone, terms in sorted order."""
    v = tv.TfidfVectorizer(stop_words="english")
    m = v.fit_transform(["plasma helium", "plasma"])
    assert v.get_feature_names_out() == ["helium", "plasma"]
    idf_h, idf_p = math.log(3 / 2) + 1, math.log(3 / 3) + 1
    row = np.array([idf_h, idf_p]) / math.hypot(idf_h, idf_p)
    assert np.allclose(m.toarray(), [row, [0, 1]])
    assert np.allclose(v.idf_, [idf_h, idf_p])


def test_bigrams_skip_stop_words_and_sublinear_tf():
    v = tv.TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    v.fit_transform(["the helium of the plasma plasma"])
    assert v.get_feature_names_out() == ["helium", "helium plasma", "plasma", "plasma plasma"]
    c = tv.CountVectorizer(stop_words="english")
    assert c.fit_transform(["plasma plasma helium"]).toarray().tolist() == [[1, 2]]


def test_document_frequency_limits():
    v = tv.TfidfVectorizer(max_df=0.5)  # a term in more than half the documents goes
    v.fit_transform(["plasma helium", "plasma neon", "plasma argon"])
    assert "plasma" not in v.vocabulary_ and "helium" in v.vocabulary_
    with pytest.raises(ValueError):
        tv.TfidfVectorizer(stop_words="english").fit_transform(["the and of", ""])
    with pytest.raises(ValueError):
        tv.TfidfVectorizer(max_df=0.1).fit_transform(["plasma", "plasma"])


def test_rows_operations():
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


def test_stop_words_are_sklearns():
    assert len(tv.ENGLISH_STOP_WORDS) == 318 and {"the", "and", "whereupon"} <= tv.ENGLISH_STOP_WORDS


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
        assert np.allclose(a.fit_transform(docs).toarray(), b.fit_transform(docs).toarray())
        assert list(a.get_feature_names_out()) == b.get_feature_names_out()
    a = sk.CountVectorizer(token_pattern=BM25_TOKENS, stop_words="english")
    b = tv.CountVectorizer(token_pattern=BM25_TOKENS, stop_words="english")
    assert (a.fit_transform(docs).toarray() == b.fit_transform(docs).toarray()).all()
