"""Regions with a neural embedding model: a library in one field must still split into its sub-areas.

Neural cosine similarities in a single-field library are nearly all high, so the plain threshold chained
every paper into one region (while TF-IDF, which discounts shared words, found three). The region code
compares papers by what sets them apart: embeddings minus the library's average.
"""
import importlib
import sys

import numpy as np
import pytest


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    return importlib.import_module("app.server")


def _one_field_library(n_per_group=5, groups=3, seed=0):
    """Unit vectors sharing a strong common direction (the field) plus a weaker group direction and noise:
    every pair has a plain cosine well above 0.42, but each group is distinct once the field is removed."""
    rng = np.random.default_rng(seed)
    dim = 64
    field = rng.normal(size=dim)
    field /= np.linalg.norm(field)
    centres = [rng.normal(size=dim) for _ in range(groups)]
    ids, rows, truth = [], [], {}
    for g, c in enumerate(centres):
        c = c / np.linalg.norm(c)
        for k in range(n_per_group):
            v = 3.0 * field + 1.0 * c + 0.06 * rng.normal(size=dim)
            rows.append(v / np.linalg.norm(v))
            nid = f"p{g}-{k}"
            ids.append(nid)
            truth[nid] = g
    m = np.vstack(rows)
    by_id = {i: {"id": i, "type": "paper", "title": i} for i in ids}
    return ids, m, by_id, truth


def _neighbours(m, ids):
    sims = m @ m.T
    np.fill_diagonal(sims, -1.0)
    return {ids[r]: [(ids[c], float(sims[r, c])) for c in np.argsort(-sims[r])[:8]] for r in range(len(ids))}


def test_plain_cosine_is_all_high():
    ids, m, _, _ = _one_field_library()
    sims = m @ m.T
    off = sims[~np.eye(len(ids), dtype=bool)]
    assert off.min() > 0.42  # the old neural threshold keeps every pair: one region


def test_centred_neighbours_separate_the_groups(server, monkeypatch):
    ids, m, by_id, truth = _one_field_library()
    monkeypatch.setattr(server.embedder, "kind", "sentence-transformers:test")
    idx = {"ids": ids, "m": m, "by_id": by_id, "neighbours": _neighbours(m, ids)}
    out = server._centred_neighbours(idx, sorted(ids))
    assert out is not None
    neigh, mc = out
    assert mc.shape == m.shape
    for i, ns in neigh.items():
        strong = [j for j, s in ns if s >= 0.15]
        assert strong, i
        assert all(truth[j] == truth[i] for j in strong), (i, strong)


def test_regions_split_a_one_field_library(server, monkeypatch):
    ids, m, by_id, truth = _one_field_library()
    monkeypatch.setattr(server.embedder, "kind", "sentence-transformers:test")
    monkeypatch.setattr(server, "_question_ties", lambda idx, thr: {})
    monkeypatch.setattr(server, "_cluster_names", lambda groups: [f"region {k}" for k in range(len(groups))])
    idx = {"ids": ids, "m": m, "by_id": by_id, "neighbours": _neighbours(m, ids)}
    cluster_of, clusters = server._clusters(idx, [])
    assert len(clusters) == 3
    for g in range(3):
        assert len({cluster_of[i] for i in ids if truth[i] == g}) == 1


def test_tfidf_and_small_libraries_keep_plain_neighbours(server, monkeypatch):
    ids, m, by_id, _ = _one_field_library()
    idx = {"ids": ids, "m": m, "by_id": by_id, "neighbours": _neighbours(m, ids)}
    monkeypatch.setattr(server.embedder, "kind", "tfidf")
    assert server._centred_neighbours(idx, sorted(ids)) is None
    monkeypatch.setattr(server.embedder, "kind", "sentence-transformers:test")
    assert server._centred_neighbours(idx, sorted(ids)[:5]) is None
