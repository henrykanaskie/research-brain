# Embedding evaluation

Which embedding model links your notes best? This harness scores models on pairs you label yourself,
from your own library.

## 1. Label some pairs

```bash
.venv/bin/python eval/embed_eval.py --suggest > eval/pairs.csv
```

That prints 40 candidate pairs: 20 of the most similar paper/note pairs (by TF-IDF) and 20 random
ones. Open `eval/pairs.csv` and fill in `related` with `1` (you'd want these connected) or `0` (not
related). Delete rows you can't judge. The extra title columns are only there to help you; they're
ignored. The format is three columns, as in `pairs.example.csv`:

```
a_id,b_id,related
attention-is-all-you-need-2017,bert-pre-training-of-deep-bidirectional-transformers-2019,1
```

Ids are file names in `vault/notes/` without `.md`. Aim for at least 15 related and 15 unrelated
pairs; a handful of pairs gives noisy numbers.

## 2. Run it

```bash
.venv/bin/python eval/embed_eval.py
```

By default it compares TF-IDF, `all-MiniLM-L6-v2` (the app's default) and `allenai/specter2_base`
(trained on scientific papers and their citations). Models that can't load, for example because
`sentence-transformers` isn't installed or you're offline the first time a model is needed, are
listed as skipped with the reason. Pick others with `--models tfidf BAAI/bge-small-en-v1.5`.
`--vault` points at another library.

## 3. Read the results

The table is printed and saved to `eval/results.md`:

| Column | Meaning |
| --- | --- |
| Recall@5 (related) | Share of related pairs where each note finds the other among its 5 nearest notes, both directions counted. This is what you see in the graph and the Similar list. Higher is better. |
| Mean cosine, related / unrelated | Average similarity of each group. |
| Gap | Related minus unrelated. Models have different baselines (MiniLM rarely goes below 0.1, TF-IDF often sits near 0), so compare gaps, not raw scores. |

To switch the app to a model that wins, start it with `RB_EMBED_MODEL=<model name>`. The graph's
similarity slider default assumes MiniLM-like scores, so you may need to move it.
