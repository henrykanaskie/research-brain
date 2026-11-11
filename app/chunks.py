"""Passage-level retrieval for "Ask your library".

Whole-note vectors are good for "which papers are about X" but miss "what does eq. 12 on page 9
say". This module splits every paper's extracted text (vault/text/<id>.txt) into ~800-character
passages on paragraph boundaries, each tagged with its PDF page from the `[[page N]]` markers, and
also chunks each note's own writing: abstract, summary, notes, and a fragment's quote + note (page
from the fragment). Passages are embedded with the app's Embedder: the sentence-transformers model
when installed, else a TF-IDF vectorizer fitted over the passages themselves (separate from the
whole-note one).

Cache (derived, safe to delete): vault/.index/chunks-<embedder>.json holds each note's key (a hash
of its text fields + the text file's size/mtime) and its passages; chunks-<embedder>.npy holds the
passage vectors (sentence-transformers only; TF-IDF is refitted in memory, which takes well under a
second even for a large library). Only notes whose key changed are re-chunked and re-embedded.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

import numpy as np

CHUNK_CHARS = 800
MAX_CHUNK_CHARS = 1100        # a single paragraph longer than this is split at sentence ends
MIN_CHUNK_CHARS = 40          # drop page furniture like a lone running header or page number
PAGE_RE = re.compile(r"\[\[page (\d+)\]\]")
# Linked HTML articles (saved by the browser extension without a PDF) mark headings instead of pages:
# [[section 3.2 Results]]. Passages under one carry its label, so citations read "§3.2".
SECTION_RE = re.compile(r"\[\[section ([^\[\]\n]{1,200})\]\]")
MARKER_RE = re.compile(r"\[\[(page \d+|section [^\[\]\n]{1,200})\]\]")
SECTION_NUM_RES = (re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)\.?(?=\s|$)"),              # 3.2 Results, 4. Discussion
                   re.compile(r"^([A-Z](?:\.\d{1,3})+)\.?(?=\s|$)"),                  # A.1 Proofs
                   re.compile(r"^(?:Appendix|Section|Sec\.)\s+([A-Z0-9](?:\.\d{1,3})*)\b"),  # Appendix B
                   re.compile(r"^([IVX]{1,6})\.\s"))                                   # III. Results (IEEE)
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[$])")


# ---------------------------------------------------------------- splitting
def _split_long(p: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split one over-long paragraph at sentence boundaries (hard-cut only as a last resort)."""
    out, cur = [], ""
    for s in SENT_RE.split(p):
        while len(s) > MAX_CHUNK_CHARS:
            cut = s.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > size:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    if cur.strip():
        out.append(cur)
    return out


def split_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Greedy-pack paragraphs ("\\n\\n"-separated) into chunks of about `size` characters."""
    paras = [re.sub(r"[ \t]*\n[ \t]*", " ", p).strip() for p in re.split(r"\n\s*\n", text or "")]
    out, cur = [], ""
    for p in paras:
        if not p:
            continue
        pieces = _split_long(p, size) if len(p) > MAX_CHUNK_CHARS else [p]
        for piece in pieces:
            if cur and len(cur) + 2 + len(piece) > size:
                out.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur.strip():
        out.append(cur)
    return [c for c in out if len(c.strip()) >= MIN_CHUNK_CHARS or len(out) == 1]


def split_pages(fulltext: str) -> list[tuple[int | None, str]]:
    """[(page, page_text)] from text with `[[page N]]` markers. Text before the first marker (or text
    with no markers at all) gets page None."""
    parts = PAGE_RE.split(fulltext or "")
    out: list[tuple[int | None, str]] = []
    if parts[0].strip():
        out.append((None, parts[0]))
    for i in range(1, len(parts) - 1, 2):
        out.append((int(parts[i]), parts[i + 1]))
    return out


def section_label(title: str, max_len: int = 32) -> str:
    """How a section is cited: its number when the heading has one ("3.2 Results" -> "3.2", "Appendix A" stays
    a title), else the heading itself, shortened ("Materials and methods")."""
    t = " ".join((title or "").split())
    for rx in SECTION_NUM_RES:
        m = rx.match(t)
        if m:
            return m.group(1)
    if len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0].rstrip(",;:.-") + "…"
    return t


def split_sections(fulltext: str) -> list[tuple[int | None, str | None, str]]:
    """[(page, section title, text)] from text with `[[page N]]` and/or `[[section Title]]` markers.
    A section heading carries over a page break (PDF text has pages only; HTML text has sections only)."""
    parts = MARKER_RE.split(fulltext or "")
    out: list[tuple[int | None, str | None, str]] = []
    page: int | None = None
    sec: str | None = None
    if parts[0].strip():
        out.append((None, None, parts[0]))
    for i in range(1, len(parts) - 1, 2):
        m = parts[i]
        if m.startswith("page "):
            page = int(m[5:])
        else:
            sec = " ".join(m[8:].split()) or None
        out.append((page, sec, parts[i + 1]))
    return out


def passage_where(p: dict) -> str:
    """How a passage is cited after its [[id]]: ' p. 4', ' §3.2', ' §"Results"', or ''."""
    if p.get("page"):
        return f" p. {p['page']}"
    sec = p.get("section") or ""
    if not sec:
        return ""
    return f" §{sec}" if re.fullmatch(r"[A-Z0-9]+(?:\.\d+)*", sec) else f' §"{sec}"'


def strip_markers(text: str) -> str:
    """The text without [[page N]] / [[section …]] markers."""
    return MARKER_RE.sub("", text or "")


def chunk_note(note: dict, fulltext: str = "") -> list[dict]:
    """Passages for one note: [{"page": int|None, "src": "text"|"abstract"|"summary"|"notes"|"fragment",
    "text": str, "section"?: str, "section_title"?: str}]. Paper text chunks never cross a page or section
    boundary, so every passage has one page (PDF text) or one section (a linked HTML article: `section` is
    the citation label such as "3.2", `section_title` the full heading)."""
    out: list[dict] = []
    title = note.get("title") or ""
    if note.get("type") == "fragment":
        parts = []
        if note.get("quote"):
            parts.append(f"Quoted passage: {note['quote'].strip()}")
        if note.get("body"):
            parts.append(f"Student's note: {note['body'].strip()}")
        text = "\n\n".join(parts) or title
        for c in split_text(text):
            out.append({"page": note.get("page"), "src": "fragment", "text": c})
        return out
    for key, src, label in (("abstract", "abstract", "Abstract"), ("summary", "summary", "Student's summary"),
                            ("body", "notes", "Student's notes")):
        val = (note.get(key) or "").strip()
        if val:
            for c in split_text(val):
                out.append({"page": None, "src": src, "text": f"{label}: {c}"})
    if "[[section " not in (fulltext or ""):
        for page, ptext in split_pages(fulltext):
            for c in split_text(ptext):
                out.append({"page": page, "src": "text", "text": c})
        return out
    for page, sec, ptext in split_sections(fulltext):
        for c in split_text(ptext):
            row = {"page": page, "src": "text", "text": c}
            if sec:
                row["section"] = section_label(sec)
                row["section_title"] = sec[:200]
            out.append(row)
    return out


def _note_key(note: dict, text_path: Path | None) -> str:
    h = hashlib.sha1()
    for k in ("type", "title", "abstract", "summary", "body", "quote", "page"):
        h.update(f"{k}\x00{note.get(k) or ''}\x01".encode())
    if text_path is not None:
        try:
            st = text_path.stat()
            h.update(f"text\x00{st.st_size}:{st.st_mtime_ns}".encode())
        except OSError:
            pass
    return h.hexdigest()


# ---------------------------------------------------------------- index
RRF_K = 60          # standard reciprocal-rank-fusion constant
RRF_DEPTH = 100     # how deep each ranking contributes
BM25_TOKENS = r"(?u)\\?[A-Za-z0-9][A-Za-z0-9_.\-]*[A-Za-z0-9]|\\?[A-Za-z0-9]"


class ChunkIndex:
    """Passage vectors for the whole library, refreshed incrementally on each search."""

    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder
        self.lock = threading.Lock()
        self._kind = None
        self._notes: dict[str, dict] = {}        # nid -> {"h": key, "chunks": [...]}
        self._rows: list[tuple[str, int]] = []   # (nid, chunk index) per matrix row
        self._m: np.ndarray | None = None
        self._tfidf = None
        self._sig = None
        self._bm25 = None   # (CountVectorizer, sparse doc×term BM25 weight matrix)

    # ---- cache files
    def _files(self, kind: str) -> tuple[Path, Path]:
        safe = kind.replace(":", "_").replace("/", "_")
        d = Path(self.store.index_dir)
        return d / f"chunks-{safe}.json", d / f"chunks-{safe}.npy"

    def _load(self, kind: str) -> None:
        self._kind, self._notes, self._rows, self._m, self._tfidf, self._sig = kind, {}, [], None, None, None
        jf, nf = self._files(kind)
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            notes, rows = data.get("notes") or {}, [tuple(r) for r in data.get("rows") or []]
            m = np.load(nf) if kind != "tfidf" and nf.exists() else None
            if kind != "tfidf" and (m is None or m.shape[0] != len(rows)):
                return  # inconsistent cache: rebuild
            self._notes, self._rows, self._m = notes, rows, m
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        jf, nf = self._files(self._kind)
        tmp = jf.with_suffix(".tmp")
        tmp.write_text(json.dumps({"notes": self._notes, "rows": self._rows}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(jf)
        if self._kind != "tfidf" and self._m is not None:
            tmpn = nf.with_name(nf.stem + ".tmp.npy")
            np.save(tmpn, self._m.astype(np.float32))
            tmpn.replace(nf)

    # ---- embedding
    def _encode(self, texts: list[str]) -> np.ndarray:
        model = self.embedder._load_model()
        v = model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32)
        return np.asarray(v, dtype=np.float32)

    def _fit_tfidf(self, texts: list[str]):
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, ngram_range=(1, 2), min_df=1,
                              max_df=0.9 if len(texts) > 20 else 1.0)
        try:
            m = vec.fit_transform(texts)
        except ValueError:
            return None, None
        return vec, m  # sparse, rows L2-normalised

    # ---- refresh
    def refresh(self, notes: list[dict] | None = None) -> None:
        notes = self.store.all() if notes is None else notes
        kind = self.embedder.kind
        with self.lock:
            if kind != self._kind:
                self._load(kind)
            want: dict[str, tuple[str, dict, Path | None]] = {}
            for n in notes:
                if n.get("type") == "question":
                    continue  # open questions are the user's private ideas, not sources (app/questions.py)
                tp = None
                if n["type"] != "fragment":
                    try:
                        tp = self.store.text_path(n["id"])
                    except KeyError:
                        tp = None
                want[n["id"]] = (_note_key(n, tp), n, tp)
            sig = tuple(sorted((i, k) for i, (k, _, _) in want.items()))
            if sig == self._sig and (self._m is not None or self._tfidf is not None or not self._rows):
                return
            old_rows = {r: i for i, r in enumerate(self._rows)}
            rechunked: set[str] = set()
            for nid, (key, n, tp) in want.items():
                cur = self._notes.get(nid)
                if cur and cur.get("h") == key:
                    continue
                full = ""
                if tp is not None and tp.exists():
                    full = tp.read_text(encoding="utf-8", errors="ignore")
                self._notes[nid] = {"h": key, "chunks": chunk_note(n, full)}
                rechunked.add(nid)
            removed = set(self._notes) - set(want)
            for stale in removed:
                del self._notes[stale]
            changed = bool(rechunked or removed)
            rows = [(nid, k) for nid in sorted(self._notes) for k in range(len(self._notes[nid]["chunks"]))]
            if kind == "tfidf":
                texts = [self._notes[nid]["chunks"][k]["text"] for nid, k in rows]
                self._tfidf, self._m = (self._fit_tfidf(texts) if texts else (None, None))
            else:
                # reuse cached vectors of untouched notes; embed only passages of re-chunked notes
                vecs: list[np.ndarray | None] = []
                todo: list[int] = []
                for i, (nid, k) in enumerate(rows):
                    j = old_rows.get((nid, k)) if nid not in rechunked and self._m is not None else None
                    vecs.append(self._m[j] if j is not None else None)
                    if j is None:
                        todo.append(i)
                dim = self._m.shape[1] if self._m is not None and self._m.ndim == 2 else 1
                if todo:
                    enc = self._encode([self._notes[rows[i][0]]["chunks"][rows[i][1]]["text"] for i in todo])
                    for i, v in zip(todo, enc):
                        vecs[i] = v
                    dim = enc.shape[1]
                self._m = np.vstack(vecs).astype(np.float32) if vecs else np.zeros((0, dim), np.float32)
            self._rows = rows
            self._sig = sig
            self._bm25 = self._build_bm25([self._notes[nid]["chunks"][k]["text"] for nid, k in rows]) if rows else None
            if changed:
                try:
                    self._save()
                except OSError as e:
                    print(f"[research_brain] couldn't save passage index: {e!r}")

    # ---- lexical side of hybrid retrieval
    @staticmethod
    def _build_bm25(texts: list[str], k1: float = 1.5, b: float = 0.75):
        """BM25 term weights per chunk. Tokens keep numbers, decimals, subscripts and LaTeX commands
        ("811.3", "kr", "t_e", "\\nabla"), which embeddings blur and researchers search by."""
        from sklearn.feature_extraction.text import CountVectorizer
        vec = CountVectorizer(lowercase=True, token_pattern=BM25_TOKENS, stop_words="english")
        try:
            tf = vec.fit_transform(texts).tocsr().astype(np.float32)
        except ValueError:
            return None
        n = tf.shape[0]
        dl = np.asarray(tf.sum(axis=1)).ravel()
        avgdl = float(dl.mean()) or 1.0
        df = np.bincount(tf.indices, minlength=tf.shape[1])
        idf = np.log(1 + (n - df + 0.5) / (df + 0.5)).astype(np.float32)
        rows = np.repeat(np.arange(n), np.diff(tf.indptr))
        f = tf.data
        tf.data = idf[tf.indices] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl[rows] / avgdl))
        return vec, tf

    def _bm25_scores(self, query: str) -> np.ndarray | None:
        if not self._bm25:
            return None
        vec, w = self._bm25
        q = vec.transform([query])
        if q.nnz == 0:
            return None
        q.data[:] = 1.0
        return np.asarray((w @ q.T).todense()).ravel()

    def search(self, query: str, k: int = 12, per_note: int = 3, notes: list[dict] | None = None) -> list[dict]:
        """Top passages for `query`, at most `per_note` from any one note (diversity).
        Hybrid: dense (embedding) ranks and BM25 (exact terms) ranks fused with reciprocal rank fusion,
        RRF(d) = sum over rankings of 1 / (60 + rank). Returns [{"id","page","src","text","score","sim"}], best
        first. `notes` limits the results to those notes. The index itself always covers the whole library when
        the store can list it, so callers that hide some notes (private ones from an AI agent) and callers that
        don't (open-question matching) don't make it drop and re-embed passages in turn."""
        allowed = None
        universe = notes
        if notes is not None:
            allowed = {n["id"] for n in notes}
            try:
                full = self.store.all()
                if allowed <= {n["id"] for n in full}:
                    universe = full
            except Exception:
                universe = notes
        try:
            self._refresh_safe(universe)
        except Exception as e:  # e.g. embedding model unavailable offline
            if self.embedder.kind == "tfidf":
                raise
            print(f"[research_brain] passage embedding failed ({e!r}); falling back to TF-IDF")
            self.embedder.kind = "tfidf"
            self._refresh_safe(universe)
        with self.lock:
            if not self._rows or self._m is None:
                return []
            if self._kind == "tfidf":
                if self._tfidf is None:
                    return []
                qv = self._tfidf.transform([query])
                sims = np.asarray((self._m @ qv.T).todense()).ravel()
            else:
                qv = self._encode([query])[0]
                sims = self._m @ qv
            mask = None
            if allowed is not None:
                mask = np.fromiter((nid in allowed for nid, _ in self._rows), dtype=bool, count=len(self._rows))
                sims = np.where(mask, sims, 0.0)
            dense = np.where(sims > 0)[0]
            dense = dense[np.argsort(-sims[dense])][:RRF_DEPTH]
            fused = np.zeros(len(self._rows), dtype=np.float32)
            fused[dense] += 1.0 / (RRF_K + 1 + np.arange(len(dense)))
            lex = self._bm25_scores(query)
            if lex is not None and mask is not None:
                lex = np.where(mask, lex, 0.0)
            if lex is not None:
                hit = np.where(lex > 0)[0]
                hit = hit[np.argsort(-lex[hit])][:RRF_DEPTH]
                fused[hit] += 1.0 / (RRF_K + 1 + np.arange(len(hit)))
            order = np.argsort(-fused)
            out, per = [], {}
            for r in order:
                s = float(fused[r])
                if s <= 0 or len(out) >= k:
                    break
                nid, ci = self._rows[r]
                if per.get(nid, 0) >= per_note:
                    continue
                per[nid] = per.get(nid, 0) + 1
                c = self._notes[nid]["chunks"][ci]
                row = {"id": nid, "page": c["page"], "src": c["src"], "text": c["text"], "score": round(s, 4),
                       "sim": round(float(max(0.0, sims[r])), 4)}  # absolute cosine (the fused score is rank-based)
                if c.get("section"):
                    row["section"], row["section_title"] = c["section"], c.get("section_title") or c["section"]
                out.append(row)
            return out

    def similarity(self, query: str, texts: list[str]) -> list[float]:
        """Cosine similarity of `query` to each of `texts` in the same space as search()'s "sim" (the passage
        TF-IDF vocabulary, or the sentence-transformers model), so one threshold fits both. Local only."""
        if not texts:
            return []
        with self.lock:
            if self._kind == "tfidf" or self.embedder.kind == "tfidf":
                if self._tfidf is None:
                    return [0.0] * len(texts)
                m = self._tfidf.transform([query] + list(texts))
                sims = np.asarray((m[1:] @ m[0].T).todense()).ravel()
            else:
                v = self._encode([query] + list(texts))
                sims = v[1:] @ v[0]
        return [float(max(0.0, min(1.0, s))) for s in sims]

    def _refresh_safe(self, notes):
        self.refresh(notes)
