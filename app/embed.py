"""Semantic similarity between notes.

Uses a local sentence-transformers model when installed (default all-MiniLM-L6-v2,
runs on Apple-silicon GPU via MPS), otherwise falls back to TF-IDF from scikit-learn.
Vectors are cached in vault/.index keyed by a hash of each note's text, so only
changed notes are re-embedded.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np

MODEL_NAME = os.environ.get("RB_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
CHUNK_CHARS = 900       # MiniLM truncates at 256 tokens (~1000 chars); embed chunks and mean-pool
MAX_CHUNKS = 8


def note_text(note: dict, fulltext: str = "") -> str:
    parts = [note.get("title", "")]
    if note.get("tags"):
        parts.append("Tags: " + ", ".join(note["tags"]))
    for k in ("quote", "abstract", "summary", "body"):
        if note.get(k):
            parts.append(note[k])
    if not note.get("abstract") and fulltext:
        parts.append(fulltext[:2000])
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _chunks(text: str) -> list[str]:
    paras, out, cur = text.split("\n"), [], ""
    for p in paras:
        if len(cur) + len(p) > CHUNK_CHARS and cur:
            out.append(cur)
            cur = ""
        while len(p) > CHUNK_CHARS:
            out.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS:]
        cur = f"{cur}\n{p}" if cur else p
    if cur.strip():
        out.append(cur)
    return out[:MAX_CHUNKS] or [text[:CHUNK_CHARS]]


class Embedder:
    def __init__(self, index_dir: Path):
        self.index_dir = Path(index_dir)
        self.lock = threading.Lock()
        self.model = None
        self.kind = "tfidf"
        self._tfidf = None
        if os.environ.get("RB_EMBEDDER", "auto") != "tfidf":
            try:
                from sentence_transformers import SentenceTransformer  # noqa: F401
                self.kind = f"sentence-transformers:{MODEL_NAME.split('/')[-1]}"
            except Exception:
                self.kind = "tfidf"
        self._cache: dict[str, dict] = {}
        self._cache_file = self.index_dir / f"vectors-{self.kind.replace(':', '_').replace('/', '_')}.json"
        if self.kind != "tfidf" and self._cache_file.exists():
            try:
                self._cache = json.loads(self._cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}

    # ---- sentence-transformers path ----
    def _load_model(self):
        if self.model is None:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(MODEL_NAME)
        return self.model

    def _embed_text(self, text: str) -> np.ndarray:
        model = self._load_model()
        vecs = model.encode(_chunks(text), normalize_embeddings=True, show_progress_bar=False)
        v = np.asarray(vecs, dtype=np.float32).mean(axis=0)
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def _persist(self):
        tmp = self._cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache), encoding="utf-8", newline="\n")
        tmp.replace(self._cache_file)

    # ---- public ----
    def matrix(self, docs: dict[str, str]) -> tuple[list[str], np.ndarray | None]:
        """docs: id -> text. Returns (ids, L2-normalised matrix)."""
        try:
            return self._matrix(docs)
        except Exception as e:  # e.g. model download failed offline: degrade instead of breaking the app
            if self.kind == "tfidf":
                raise
            print(f"[research_brain] embedding model unavailable ({e!r}); falling back to TF-IDF")
            self.kind = "tfidf"
            return self._matrix(docs)

    def _matrix(self, docs: dict[str, str]) -> tuple[list[str], np.ndarray | None]:
        ids = sorted(docs)
        if not ids:
            return [], None
        with self.lock:
            if self.kind == "tfidf":
                from .textvec import TfidfVectorizer
                texts = [docs[i] or i for i in ids]
                vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, ngram_range=(1, 2),
                                      min_df=1, max_df=0.9 if len(ids) > 5 else 1.0)
                try:
                    m = vec.fit_transform(texts)
                except ValueError:  # empty vocabulary
                    return ids, np.zeros((len(ids), 1), dtype=np.float32)
                self._tfidf = vec
                return ids, m.toarray().astype(np.float32)  # rows already L2-normalised
            changed = False
            rows = []
            for i in ids:
                h = hashlib.sha1(docs[i].encode()).hexdigest()
                entry = self._cache.get(i)
                if not entry or entry.get("h") != h:
                    entry = {"h": h, "v": self._embed_text(docs[i]).round(5).tolist()}
                    self._cache[i] = entry
                    changed = True
                rows.append(entry["v"])
            for stale in set(self._cache) - set(ids):
                del self._cache[stale]
                changed = True
            if changed:
                self._persist()
            return ids, np.asarray(rows, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray | None:
        with self.lock:
            if self.kind == "tfidf":
                if self._tfidf is None:
                    return None
                v = self._tfidf.transform([text]).toarray()[0].astype(np.float32)
                n = np.linalg.norm(v)
                return v / n if n > 0 else v
            return self._embed_text(text)


def top_neighbours(ids: list[str], m: np.ndarray | None, k: int = 5) -> dict[str, list[tuple[str, float]]]:
    if m is None or len(ids) < 2:
        return {i: [] for i in ids}
    sims = m @ m.T
    np.fill_diagonal(sims, -1.0)
    out = {}
    for r, i in enumerate(ids):
        order = np.argsort(-sims[r])[:k]
        out[i] = [(ids[c], float(max(0.0, min(1.0, sims[r, c])))) for c in order if sims[r, c] > 0]
    return out
