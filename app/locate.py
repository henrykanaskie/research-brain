"""Find the sentence a citation points at, so clicking "[[paper]] p. 6" can highlight it in the PDF.

An AI answer cites a page; the claim it makes is the sentence the citation ends. Score each sentence (and
each pair of neighbouring sentences) on the cited page and the pages either side by how much of the claim's
vocabulary it contains, rarer words and numbers counting more. Return the best one as a quote the viewer can
find in the PDF's text layer. When a neighbouring page matches clearly better, the page is corrected: models
are often one page off. Runs locally, no AI, no network.
"""
from __future__ import annotations

import math
import re

from . import chunks

STOP = frozenset("""
a about above after again against all also although am among an and any are as at be because been before being
below between both but by can could did do does doing down during each either else even ever every few for from
further had has have having he her here hers him his how however i if in into is it its itself just less like
made make many may me might more most much must my no nor not now of off often on once one only or other our out
over own per rather same she should since so some such than that the their them then there these they this those
though through thus to too two under until up upon us use used using very via was we well were what when where
whether which while who whom whose why will with within without would yet you your paper study show shows shown
result results figure fig table eq equation section see also et al
""".split())
_TEX_CMD = re.compile(r"\\[A-Za-z]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z\-]{2,}|\d+(?:\.\d+)?")
_SENT = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[A-Z0-9(\[])")
_ABBREV = re.compile(r"\b(?:et al|e\.g|i\.e|Fig|Figs|Eq|Eqs|Ref|Refs|Sec|vs|cf|approx|resp|no|vol|pp)\.$", re.I)
MIN_SCORE = 0.18      # a sentence (or pair) this good is "the" sentence
ROUGH_MIN = 0.08      # below MIN_SCORE but above this: mark the stretch of the page the claim is roughly from
ROUGH_SENTS = 4       # how many sentences a rough stretch spans
MAX_ROUGH = 700
SWITCH_GAIN = 1.4     # a neighbouring page must score this much better to move the citation there
MAX_QUOTE = 320


def _stem(w: str) -> str:
    w = w.lower().strip("-")
    for suf in ("ations", "ation", "ments", "ment", "ities", "ity", "ings", "ing", "ies", "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w[:6]  # "oscillation" and "oscillates" meet at "oscill"


def terms(text: str) -> list[str]:
    text = _TEX_CMD.sub(" ", text or "").replace("$", " ")
    text = re.sub(r"\[\[[^\]]*\]\]", " ", text)
    out = []
    for w in _WORD.findall(text):
        lw = w.lower()
        if lw in STOP:
            continue
        out.append(lw if w[0].isdigit() else _stem(lw))
    return out


def sentences(text: str) -> list[str]:
    """Sentences of one page, without page/section markers and with line-break hyphenation undone."""
    t = re.sub(r"\[\[(?:page|section)[^\]]*\]\]", " ", text or "")
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return []
    parts, buf = [], ""
    for piece in _SENT.split(t):
        buf = f"{buf} {piece}".strip() if buf else piece
        if _ABBREV.search(buf):  # "et al." / "Fig." doesn't end a sentence
            continue
        parts.append(buf)
        buf = ""
    if buf:
        parts.append(buf)
    return [p for p in parts if len(p) >= 12]


def _best(claim_terms: list[str], sents: list[str], idf: dict[str, float]) -> tuple[float, str]:
    want = {}
    for t in claim_terms:
        want[t] = max(want.get(t, 0.0), idf.get(t, 1.0) * (1.6 if t[0].isdigit() else 1.0))
    total = sum(want.values())
    if not total or not sents:
        return 0.0, ""
    sets = [set(terms(s)) for s in sents]
    best = (0.0, "")
    for i, s in enumerate(sents):
        for j in (i, i + 1):  # one sentence, or it and the next
            if j >= len(sents):
                break
            have = sets[i] | sets[j] if j != i else sets[i]
            got = sum(w for t, w in want.items() if t in have)
            text = s if j == i else f"{s} {sents[j]}"
            # a pair must earn its extra length
            score = got / total - (0.06 if j != i else 0.0) - (0.04 if len(text) > MAX_QUOTE else 0.0)
            if score > best[0]:
                best = (score, text)
    return best


def _rough(claim_terms: list[str], sents: list[str], idf: dict[str, float]) -> tuple[float, str]:
    """The stretch of ROUGH_SENTS sentences holding most of the claim's vocabulary: no single sentence says
    it, but the claim is drawn from around here (a paragraph, not the whole page)."""
    want = {}
    for t in claim_terms:
        want[t] = max(want.get(t, 0.0), idf.get(t, 1.0) * (1.6 if t[0].isdigit() else 1.0))
    total = sum(want.values())
    if not total or not sents:
        return 0.0, ""
    sets = [set(terms(s)) for s in sents]
    best = (0.0, "")
    for i in range(len(sents)):
        have = set().union(*sets[i:i + ROUGH_SENTS])
        score = sum(w for t, w in want.items() if t in have) / total
        if score > best[0] + 1e-9:
            best = (score, " ".join(sents[i:i + ROUGH_SENTS]))
    return best


def _quote(text: str, claim_terms: list[str], cap: int = MAX_QUOTE) -> str:
    """At most `cap` characters, cut around the claim's words if the passage is long."""
    if len(text) <= cap:
        return text
    want = set(claim_terms)
    words = text.split(" ")
    hits = [i for i, w in enumerate(words) if set(terms(w)) & want]
    mid = hits[len(hits) // 2] if hits else 0
    lo, hi = mid, mid
    while hi - lo < len(words) and len(" ".join(words[lo:hi + 1])) < cap:
        if lo > 0:
            lo -= 1
        if hi < len(words) - 1:
            hi += 1
        if lo == 0 and hi == len(words) - 1:
            break
    return " ".join(words[lo:hi + 1])


def locate(fulltext: str, page: int | None, claim: str) -> dict:
    """{page, quote, score, moved, precision}. precision is "sentence" (the sentence or two that say it),
    "rough" (the few sentences it's roughly drawn from) or "page" (nothing on the page matches; quote "")."""
    pages = dict((p, t) for p, t in chunks.split_pages(fulltext) if p is not None)
    ct = terms(claim)
    if not pages or not page or not ct:
        return {"page": page, "quote": "", "score": 0.0, "moved": False, "precision": "page"}
    cand = [p for p in (page, page - 1, page + 1) if p in pages]
    sents = {p: sentences(pages[p]) for p in cand}
    # rarity across the candidate pages' sentences
    df: dict[str, int] = {}
    n = 0
    for ss in sents.values():
        for s in ss:
            n += 1
            for t in set(terms(s)):
                df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}
    scored = {p: _best(ct, sents[p], idf) for p in cand}
    here = scored.get(page, (0.0, ""))
    best_p = max(scored, key=lambda p: scored[p][0])
    moved = best_p != page and scored[best_p][0] >= MIN_SCORE and scored[best_p][0] >= here[0] * SWITCH_GAIN + 0.05
    use = best_p if moved else page
    score, text = scored.get(use, (0.0, ""))
    rs, rtext = _rough(ct, sents.get(use, []), idf)
    # a sentence or two that says it; unless the claim is clearly spread over a longer stretch (a summary
    # of a paragraph), in which case marking one sentence would point at the wrong thing
    if score >= MIN_SCORE and not (score < 0.5 and rs >= score + 0.2):
        return {"page": use, "quote": _quote(text, ct), "score": round(score, 3), "moved": moved, "precision": "sentence"}
    if rs >= ROUGH_MIN:
        return {"page": use, "quote": _quote(rtext, ct, MAX_ROUGH), "score": round(rs, 3), "moved": moved,
                "precision": "rough"}
    return {"page": page, "quote": "", "score": round(score, 3), "moved": False, "precision": "page"}
