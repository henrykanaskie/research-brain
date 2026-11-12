"""A copy of a paper's PDF with your highlights and notes written in as real PDF annotations.

Your fragments are stored as text (the quote, its page and your note), not as page coordinates, so they
survive re-downloads and other copies of the paper. To write them into a PDF, each quote is found again
in its page's text with pdfium (the same engine as the text the app extracted), whitespace and line-end
hyphenation ignored: the whole quote if possible, otherwise its first and last few words. Each line it
covers becomes a highlight (a /Highlight annotation with quad points, so Preview, Acrobat, Zotero and
browsers all show it), and your note goes in the annotation's text, which readers show when you click or
hover it. Quotes that can't be found (a scanned page, an edited quote) are listed in the response header
and in a note on page 1. The library's own PDF is never changed: this makes a new file.
"""
from __future__ import annotations

import io
import re

HIGHLIGHT = "ffd84d"
ANCHOR = 48   # characters of the start and end used when the whole quote isn't found


def _norm_map(text: str) -> tuple[str, list[int]]:
    """Lower-cased text with whitespace runs collapsed and 'hyphen-newline' joins removed, plus, for each
    character of it, the index of the original character."""
    out, idx = [], []
    i, n = 0, len(text)
    prev_space = True
    while i < n:
        ch = text[i]
        if ch == "-" and i + 1 < n and text[i + 1] in "\r\n":  # "oscil-\nlation"
            j = i + 1
            while j < n and text[j] in "\r\n ":
                j += 1
            i = j
            continue
        if ch.isspace() or ch == "\x00":
            if not prev_space:
                out.append(" ")
                idx.append(i)
                prev_space = True
            i += 1
            continue
        out.append(ch.lower())
        idx.append(i)
        prev_space = False
        i += 1
    return "".join(out), idx


def _norm(s: str) -> str:
    return _norm_map(s)[0].strip()


def find_range(page_text: str, quote: str) -> tuple[int, int] | None:
    """[start, end) of the quote in page_text (original indices), or None."""
    hay, idx = _norm_map(page_text)
    q = _norm(re.sub(r"\$[^$]*\$", " ", quote or ""))
    if len(q) < 4:
        return None
    k = hay.find(q)
    if k >= 0:
        return idx[k], idx[k + len(q) - 1] + 1
    if len(q) <= 2 * ANCHOR:
        return None
    head, tail = q[:ANCHOR].strip(), q[-ANCHOR:].strip()
    a = hay.find(head)
    if a < 0:
        return None
    b = hay.find(tail, a + len(head))
    if b < 0 or b - a > len(q) * 2:  # the end must follow, not too far off
        return None
    return idx[a], idx[b + len(tail) - 1] + 1


def annotate(pdf_path, fragments: list[dict], title: str = "") -> tuple[bytes, list[dict]]:
    """(the annotated PDF's bytes, [fragments that couldn't be placed]). fragments: {quote, page, body, title}."""
    import pypdfium2 as pdfium
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import FreeText, Highlight
    from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject

    doc = pdfium.PdfDocument(str(pdf_path))
    writer = PdfWriter(clone_from=PdfReader(str(pdf_path)))
    missed = []
    texts: dict[int, tuple] = {}
    for f in fragments:
        pno = int(f.get("page") or 0)
        quote = (f.get("quote") or "").strip()
        if not quote or not (1 <= pno <= len(doc)):
            if quote:
                missed.append(f)
            continue
        if pno not in texts:
            tp = doc[pno - 1].get_textpage()
            n = tp.count_chars()
            full = tp.get_text_range(0, n) if n else ""
            if len(full) != n:  # pdfium can add or drop generated characters: map one by one
                full = "".join(tp.get_text_range(i, 1) or " " for i in range(n))
            texts[pno] = (tp, full)
        tp, full = texts[pno]
        rng = find_range(full, quote)
        if not rng:
            missed.append(f)
            continue
        a, b = rng
        nrect = tp.count_rects(a, b - a)
        rects = [tp.get_rect(i) for i in range(nrect)]  # (left, bottom, right, top), one per line run
        rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
        if not rects:
            missed.append(f)
            continue
        quads = []
        for l, bt, r, t in rects:
            quads += [l, t, r, t, l, bt, r, bt]
        box = (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))
        ann = Highlight(rect=box, quad_points=ArrayObject([FloatObject(x) for x in quads]), highlight_color=HIGHLIGHT, printing=True)
        note = (f.get("body") or "").strip()
        if note:
            ann[NameObject("/Contents")] = TextStringObject(note[:5000])
        ann[NameObject("/T")] = TextStringObject("research brain")
        writer.add_annotation(page_number=pno - 1, annotation=ann)
    if missed and len(writer.pages):
        page = writer.pages[0]
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        lines = [f"{len(missed)} highlight{'s' if len(missed) != 1 else ''} couldn't be placed (the text differs on the page):"]
        lines += [f"p. {m.get('page') or '?'}: {(m.get('quote') or '')[:90]}" for m in missed[:8]]
        note = FreeText(text="\n".join(lines), rect=(24, h - 24 - 14 * (len(lines) + 1), min(w - 24, 420), h - 24),
                        font_size="9pt", font_color="333333", border_color="d0a020", background_color="fff8dc")
        writer.add_annotation(page_number=0, annotation=note)
    if title:
        writer.add_metadata({"/Title": title})
    buf = io.BytesIO()
    writer.write(buf)
    doc.close()
    return buf.getvalue(), missed
