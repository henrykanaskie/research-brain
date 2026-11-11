"""PDF → plain text (with page markers) and a best-guess title.

PDFs come from the internet, so parsing one is treated as untrusted work: extract() runs the parser in a
separate, short-lived Python process with a time limit (RB_PDF_TIMEOUT, default 120 s), a CPU limit and,
on Linux, a memory limit (RB_PDF_MAX_MEM_MB, default 2048). A PDF that hangs the parser, loops, or
inflates to gigabytes (a decompression bomb) kills only that process; the import fails with a clear
message and the server carries on. The extracted text is capped at MAX_TEXT_CHARS.
RB_PDF_INPROCESS=1 parses in the server process instead (faster; for debugging).

This file is also the child's entry point (`python -I app/pdftext.py <pdf>`), so it imports only the
standard library at the top.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

TIMEOUT = float(os.environ.get("RB_PDF_TIMEOUT", "120"))
MAX_MEM_MB = int(os.environ.get("RB_PDF_MAX_MEM_MB", "2048"))
MAX_TEXT_CHARS = int(os.environ.get("RB_PDF_MAX_CHARS", str(4_000_000)))


class PdfError(ValueError):
    """The PDF couldn't be read safely (parser crashed, ran out of time or memory)."""


def _limits():  # runs in the child between fork and exec
    try:
        import resource
        cpu = int(TIMEOUT) + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        if sys.platform.startswith("linux") and MAX_MEM_MB > 0:
            mem = MAX_MEM_MB << 20
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def extract(path: Path, max_pages: int = 80, info: dict | None = None) -> tuple[str, str]:
    """Return (text, metadata_title). Text has '[[page N]]' markers so Claude can cite pages.
    Parses in a sandboxed child process (see the module docstring); raises PdfError if it fails.
    `info`, if given, is filled with identifiers from the PDF's document info and XMP metadata
    ({"xmp_doi", "info_doi", "subject", "xmp_arxiv"}; app/metadata.ids_from_pdf_info reads them)."""
    if os.environ.get("RB_PDF_INPROCESS") == "1":
        return extract_inprocess(path, max_pages, info)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    # the child needs the app's own site-packages (pypdfium2, pypdf) but nothing from the current directory
    here = Path(__file__).resolve().parent
    env["RB_PDF_SYS_PATH"] = json.dumps([p for p in sys.path if p and Path(p).is_dir()
                                         and Path(p).resolve() not in (here, here.parent)])
    try:
        proc = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), str(path), str(max_pages)],
                              capture_output=True, timeout=TIMEOUT, env=env, stdin=subprocess.DEVNULL,
                              preexec_fn=_limits if os.name == "posix" else None)
    except subprocess.TimeoutExpired:
        raise PdfError(f"reading it took longer than {int(TIMEOUT)} s")
    except OSError:  # couldn't start a process at all: parse here rather than fail every import
        return extract_inprocess(path, max_pages, info)
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        if proc.returncode < 0 or "MemoryError" in tail[0]:
            raise PdfError("the PDF parser ran out of time or memory")
        raise PdfError(tail[0][:200])
    try:
        out = json.loads(proc.stdout.decode("utf-8"))
        if info is not None and isinstance(out.get("info"), dict):
            info.update({str(k): str(v)[:2000] for k, v in out["info"].items() if v})
        return str(out["text"])[:MAX_TEXT_CHARS], str(out["title"])[:500]
    except (ValueError, KeyError, TypeError):
        raise PdfError("the PDF parser returned nothing usable")


_XMP_DOI = (r"<prism:doi>\s*([^<\s]+)", r"<pdfx:doi>\s*([^<\s]+)", r"<crossmark:DOI>\s*([^<\s]+)",
            r"prism:doi=\"([^\"]+)\"", r"pdfx:doi=\"([^\"]+)\"", r"<dc:identifier>[\s\S]{0,200}?(?:doi:|doi\.org/)?(10\.\d{4,9}/[^<\s]+)",
            r"<rdf:li>\s*(?:doi:|https?://doi\.org/)(10\.\d{4,9}/[^<\s]+)")


def pdf_info(path: Path) -> dict:
    """Identifiers the PDF declares about itself: XMP (prism:doi, pdfx:doi, crossmark:DOI, dc:identifier) and
    the document info dictionary (/doi, /Subject). Only small strings come back."""
    out: dict = {}
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        meta = reader.metadata or {}
        for k, v in meta.items():
            kk = str(k).lower().lstrip("/")
            if kk in ("doi", "prism:doi") and v:
                out["info_doi"] = str(v)[:300]
            elif kk == "subject" and v:
                out["subject"] = str(v)[:1000]
        root = reader.trailer.get("/Root")
        xmp = root.get_object().get("/Metadata") if root is not None else None
        raw = xmp.get_object().get_data()[:400_000].decode("utf-8", "replace") if xmp is not None else ""
        for rx in _XMP_DOI:
            m = re.search(rx, raw, re.I)
            if m:
                out["xmp_doi"] = m.group(1)[:300]
                break
        m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})", raw)
        if m:
            out["xmp_arxiv"] = m.group(1)
    except Exception:
        pass
    return out


def extract_inprocess(path: Path, max_pages: int = 80, info: dict | None = None) -> tuple[str, str]:
    """extract() without the child process."""
    if info is not None:
        info.update(pdf_info(path))
    pages: list[str] = []
    meta_title = ""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        try:
            meta_title = (pdf.get_metadata_dict() or {}).get("Title", "") or ""
        except Exception:
            meta_title = ""
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            tp = page.get_textpage()
            pages.append(tp.get_text_range() or "")
            tp.close()
            page.close()
        pdf.close()
    except Exception:
        pages = []
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        try:
            meta_title = (reader.metadata.title if reader.metadata else "") or ""
        except Exception:
            meta_title = ""
        for page in reader.pages[:max_pages]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
    text = "\n\n".join(f"[[page {i + 1}]]\n{_clean(t)}" for i, t in enumerate(pages))
    return text[:MAX_TEXT_CHARS], _clean_title(meta_title)


def _clean(t: str) -> str:
    t = t.replace("\r", "\n").replace("\x00", "")
    t = re.sub(r"-\n(?=[a-z])", "", t)          # re-join hyphenated line breaks
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _clean_title(t: str) -> str:
    t = " ".join((t or "").split())
    bad = ("untitled", "microsoft word", ".dvi", ".pdf", ".tex", "arxiv")
    if len(t) < 8 or any(b in t.lower() for b in bad):
        return ""
    return t


def guess_title(text: str) -> str:
    """First plausible title-like line on page 1."""
    first = text.split("[[page 2]]")[0]
    for line in first.splitlines()[:40]:
        line = line.strip()
        if line.startswith("[[page") or len(line) < 12 or len(line) > 220:
            continue
        low = line.lower()
        if re.search(r"arxiv|preprint|journal|vol\.|doi|http|@|copyright|received|university|abstract", low):
            continue
        if re.match(r"^(\d+(\.\d+)*|[ivx]+\.?|[A-Z]\.)\s", line, re.I) or re.match(
                r"^(introduction|contents|keywords|index terms|nomenclature|table of contents)\b", low):
            continue  # section headings, not titles
        if sum(c.isalpha() for c in line) < 0.6 * len(line):
            continue
        words = [w for w in re.split(r"[\s\-]+", line) if len(w) > 3]
        if words and sum(w[0].isupper() for w in words) / len(words) < 0.4:
            continue  # reads like a body sentence, not a title
        return line
    return ""


if __name__ == "__main__":  # the child process started by extract()
    try:
        sys.path[:0] = [p for p in json.loads(os.environ.get("RB_PDF_SYS_PATH", "[]")) if isinstance(p, str)]
    except ValueError:
        pass
    _info: dict = {}
    _text, _title = extract_inprocess(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 80, _info)
    sys.stdout.write(json.dumps({"text": _text, "title": _title, "info": _info}, ensure_ascii=False))
