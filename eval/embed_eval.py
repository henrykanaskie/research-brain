"""Compare embedding models on your own library.

    python eval/embed_eval.py                     # evaluate eval/pairs.csv against the vault
    python eval/embed_eval.py --suggest           # print 40 candidate pairs to label
    python eval/embed_eval.py --models tfidf all-MiniLM-L6-v2 --vault ~/research_brain/vault

pairs.csv columns: a_id,b_id,related  (related is 1 or 0). Ids are note ids (the file names in
vault/notes without .md).

For every model it embeds each note's text the same way the app does (title, tags, quote, abstract,
summary, notes, else the first 2,000 characters of the paper text; long text is chunked and
mean-pooled) and reports:
- recall@5: for each related pair (a, b), is b among a's 5 nearest notes (and a among b's)? Each
  direction counts once. A note's own fragments/parent are excluded from its neighbours, as in the app.
- mean cosine of related pairs and of unrelated pairs, and the gap between them. A good model scores
  unrelated pairs low and related pairs high; the gap matters more than either number alone, because
  models differ in their baseline similarity.
The table is printed and written to eval/results.md.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.embed import _chunks, note_text  # noqa: E402
from app.store import Store  # noqa: E402

DEFAULT_MODELS = ["tfidf", "sentence-transformers/all-MiniLM-L6-v2", "allenai/specter2_base"]


# ---------------------------------------------------------------- data
def load_docs(vault: Path) -> tuple[dict[str, dict], dict[str, str]]:
    store = Store(vault)
    notes = {n["id"]: n for n in store.all()}
    docs = {}
    for nid, n in notes.items():
        full = ""
        if n["type"] == "paper":
            p = store.text_path(nid)
            if p.exists():
                full = p.read_text(encoding="utf-8", errors="ignore")
        docs[nid] = note_text(n, full)
    return notes, docs


def load_pairs(path: Path, notes: dict) -> tuple[list[tuple[str, str, int]], list[str]]:
    pairs, problems = [], []
    with path.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            a, b = (row.get("a_id") or "").strip(), (row.get("b_id") or "").strip()
            rel = (row.get("related") or "").strip()
            if not a or not b or a.startswith("#"):
                continue
            if rel not in ("0", "1"):
                problems.append(f"line {i}: related must be 1 or 0 (got {rel!r}); skipped")
                continue
            missing = [x for x in (a, b) if x not in notes]
            if missing:
                problems.append(f"line {i}: no note with id {', '.join(missing)}; skipped")
                continue
            pairs.append((a, b, int(rel)))
    return pairs, problems


# ---------------------------------------------------------------- models
def embed_tfidf(ids: list[str], docs: dict[str, str]) -> np.ndarray:
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, ngram_range=(1, 2), min_df=1,
                          max_df=0.9 if len(ids) > 5 else 1.0)
    return vec.fit_transform([docs[i] or i for i in ids]).toarray().astype(np.float32)


def embed_st(name: str, ids: list[str], docs: dict[str, str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(name)
    rows = []
    for i in ids:
        v = np.asarray(model.encode(_chunks(docs[i] or i), normalize_embeddings=True, show_progress_bar=False),
                       dtype=np.float32).mean(axis=0)
        n = np.linalg.norm(v)
        rows.append(v / n if n > 0 else v)
    return np.vstack(rows)


def embed(name: str, ids: list[str], docs: dict[str, str]) -> np.ndarray:
    if name == "tfidf":
        return embed_tfidf(ids, docs)
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        raise RuntimeError("sentence-transformers isn't installed (pip install sentence-transformers)")
    return embed_st(name, ids, docs)


# ---------------------------------------------------------------- metrics
def _root(n: dict) -> str:
    return n["parent"] if n["type"] == "fragment" and n["parent"] else n["id"]


def evaluate(ids: list[str], m: np.ndarray, notes: dict, pairs: list[tuple[str, str, int]], k: int = 5) -> dict:
    pos = {i: r for r, i in enumerate(ids)}
    sims = m @ m.T
    roots = np.array([_root(notes[i]) for i in ids])
    same = roots[:, None] == roots[None, :]
    ranked = np.where(same, -np.inf, sims)
    hits = total = 0
    rel_cos, unrel_cos = [], []
    for a, b, rel in pairs:
        ra, rb = pos[a], pos[b]
        c = float(sims[ra, rb])
        if rel:
            rel_cos.append(c)
            for x, y in ((ra, rb), (rb, ra)):
                topk = np.argsort(-ranked[x])[:k]
                hits += int(y in topk)
                total += 1
        else:
            unrel_cos.append(c)
    mean = lambda xs: float(np.mean(xs)) if xs else float("nan")
    return {"recall": hits / total if total else float("nan"), "rel_cos": mean(rel_cos),
            "unrel_cos": mean(unrel_cos), "n_rel": len(rel_cos), "n_unrel": len(unrel_cos)}


# ---------------------------------------------------------------- output
def fmt(x: float) -> str:
    return "n/a" if x != x else f"{x:.3f}"


def table(rows: list[tuple[str, dict | str]], k: int) -> str:
    out = [f"| Model | Recall@{k} (related) | Mean cosine, related | Mean cosine, unrelated | Gap |",
           "| --- | ---: | ---: | ---: | ---: |"]
    for name, r in rows:
        if isinstance(r, str):
            out.append(f"| {name} | skipped: {r} | | | |")
        else:
            out.append(f"| {name} | {fmt(r['recall'])} | {fmt(r['rel_cos'])} | {fmt(r['unrel_cos'])} | "
                       f"{fmt(r['rel_cos'] - r['unrel_cos'])} |")
    return "\n".join(out)


# ---------------------------------------------------------------- suggest
def suggest(ids: list[str], m: np.ndarray, notes: dict, n: int = 40, seed: int = 0) -> list[tuple[str, str, str]]:
    """Half the most similar cross-paper pairs (likely related, but check), half random pairs."""
    tops = [i for i, x in enumerate(ids) if notes[x]["type"] != "fragment"]
    if len(tops) < 2:
        return []
    sims = m @ m.T
    cand = []
    for a_i, a in enumerate(tops):
        for b in tops[a_i + 1:]:
            cand.append((float(sims[a, b]), ids[a], ids[b]))
    cand.sort(reverse=True)
    out, seen = [], set()
    for s, a, b in cand[: n // 2]:
        out.append((a, b, f"top similar, cosine {s:.2f}"))
        seen.add((a, b))
    rng = random.Random(seed)
    rest = [c for c in cand if (c[1], c[2]) not in seen]
    rng.shuffle(rest)
    for s, a, b in rest[: n - len(out)]:
        out.append((a, b, f"random, cosine {s:.2f}"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vault", default=os.environ.get("RB_VAULT", str(ROOT / "vault")))
    ap.add_argument("--pairs", default=str(ROOT / "eval" / "pairs.csv"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "results.md"))
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                    help="'tfidf' and/or sentence-transformers model names")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--suggest", action="store_true", help="print 40 candidate pairs to label, as CSV")
    ap.add_argument("-n", type=int, default=40, help="how many pairs --suggest prints")
    args = ap.parse_args(argv)

    vault = Path(args.vault).expanduser()
    if not (vault / "notes").is_dir():
        print(f"No vault at {vault} (expected a notes/ folder). Pass --vault.", file=sys.stderr)
        return 2
    notes, docs = load_docs(vault)
    ids = sorted(docs)
    if len(ids) < 2:
        print("The vault needs at least two notes.", file=sys.stderr)
        return 2

    if args.suggest:
        m = embed("tfidf", ids, docs)
        w = csv.writer(sys.stdout)
        print("Fill in `related` with 1 or 0 for each row and save as eval/pairs.csv "
              "(extra columns are ignored).", file=sys.stderr)
        w.writerow(["a_id", "b_id", "related", "a_title", "b_title", "why_suggested"])
        for a, b, why in suggest(ids, m, notes, args.n):
            w.writerow([a, b, "", notes[a]["title"][:80], notes[b]["title"][:80], why])
        return 0

    pairs_path = Path(args.pairs)
    if not pairs_path.exists():
        print(f"No pairs file at {pairs_path}. Run with --suggest to get candidates, label them, and save "
              f"as {pairs_path} (see eval/pairs.example.csv).", file=sys.stderr)
        return 2
    pairs, problems = load_pairs(pairs_path, notes)
    for p in problems:
        print(f"warning: {p}", file=sys.stderr)
    if not pairs:
        print("No usable pairs in the pairs file.", file=sys.stderr)
        return 2

    rows: list[tuple[str, dict | str]] = []
    for name in args.models:
        print(f"embedding with {name}…", file=sys.stderr)
        try:
            m = embed(name, ids, docs)
        except Exception as e:
            msg = str(e).splitlines()[0][:160] if str(e) else e.__class__.__name__
            print(f"  skipped {name}: {msg}", file=sys.stderr)
            rows.append((name, msg.replace("|", "/")))
            continue
        rows.append((name, evaluate(ids, m, notes, pairs, args.k)))

    n_rel = sum(1 for p in pairs if p[2])
    tab = table(rows, args.k)
    print(tab)
    md = (f"# Embedding evaluation\n\n{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}, vault `{vault}`, "
          f"{len(ids)} notes, {n_rel} related and {len(pairs) - n_rel} unrelated labelled pairs.\n\n"
          f"{tab}\n\nRecall@{args.k}: share of related pairs where each note finds the other among its "
          f"{args.k} nearest notes (both directions counted). Gap = related minus unrelated mean cosine.\n")
    Path(args.out).write_text(md, encoding="utf-8")
    print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
