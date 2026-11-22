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
import tempfile
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
    # Opened from the Windows Start menu the app runs under pythonw.exe, and so does this child: a program with no
    # console may be given no standard output at all. There its answer goes through a file, and no window appears.
    out_file = None
    if os.name == "nt":
        fd, out_file = tempfile.mkstemp(prefix="rb_pdf_", suffix=".json")
        os.close(fd)
        env["RB_PDF_OUT"] = out_file
    try:
        proc = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), str(path), str(max_pages)],
                              capture_output=True, timeout=TIMEOUT, env=env, stdin=subprocess.DEVNULL,
                              preexec_fn=_limits if os.name == "posix" else None,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        answer = proc.stdout
        if out_file:
            answer = Path(out_file).read_bytes() or answer
    except subprocess.TimeoutExpired:
        raise PdfError(f"reading it took longer than {int(TIMEOUT)} s")
    except OSError:  # couldn't start a process at all: parse here rather than fail every import
        return extract_inprocess(path, max_pages, info)
    finally:
        if out_file:
            try:
                os.unlink(out_file)
            except OSError:
                pass
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        if proc.returncode < 0 or "MemoryError" in tail[0]:
            raise PdfError("the PDF parser ran out of time or memory")
        if "not been decrypted" in tail[0] or "password" in tail[0].lower():
            raise PdfError("it needs a password. Open it, save a copy without one (print to PDF), and add that")
        raise PdfError(tail[0][:200])
    try:
        out = json.loads(answer.decode("utf-8"))
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


_ABS_HEAD = re.compile(r"^\s*(abstract|summary)\b\s*[:.\-—–]?\s*", re.I)
_ABS_END = re.compile(r"^\s*((\d+|[IVX]+)\.?\s+)?(introduction|background|keywords?|key\s*words|index terms|pacs|"
                      r"subject areas?|contents|nomenclature)\b|^\s*(doi\s*:|submitted to\b|https?://doi|©|\(c\)\s*\d{4}|copyright\b|"
                      r"\*?\s*corresponding author|published by\b|received\b|\*\S+@)", re.I)
_ABS_NOT_PROSE = re.compile(r"@|\b(received|accepted|published|submitted|revised|available online)\b|"
                            r"\b(university|universitat|universit[éeà]|institute|laboratory|department|facult)", re.I)


def _headingish(line: str) -> bool:
    words = [w for w in re.split(r"[\s\-]+", line) if len(w) > 3 and w[0].isalpha()]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) >= 0.5 and not line.rstrip().endswith(".")


def guess_abstract(text: str) -> str:
    """The abstract printed on the first pages of a paper's text ('' if none is found). Takes the paragraph
    under an "Abstract" heading; without one (APS and many other journals print it unlabelled between the
    authors and the first section), the first block of prose on page 1 that is long enough to be one."""
    head = text.split("[[page 3]]")[0]
    lines = [ln.strip() for ln in re.sub(r"\[\[page \d+\]\]", "\n", head).splitlines()]
    lines = [ln for ln in lines if ln]

    def joined(block: list[str]) -> str:
        t = " ".join(block)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    for i, ln in enumerate(lines[:120]):
        m = _ABS_HEAD.match(ln)
        if not m or (len(ln) > 60 and not ln[m.end():m.end() + 1].isupper()):
            continue  # "Abstract" alone, "Abstract: The ...", "ABSTRACT The ..." (not "Summary statistics ...")
        block = [ln[m.end():]] if ln[m.end():].strip() else []
        for nxt in lines[i + 1:i + 80]:
            if _ABS_END.match(nxt):
                break
            block.append(nxt)
        t = joined(block)
        if len(t) >= 200:
            return t[:5000]
    # no heading: the first substantial run of prose on page 1 (title, authors and affiliations come before it)
    first = [ln.strip() for ln in head.split("[[page 2]]")[0].splitlines() if ln.strip() and not ln.startswith("[[page")]
    run: list[str] = []
    for ln in first[:150] + [""]:
        prose = (len(ln) >= 35 and not _ABS_END.match(ln) and not _ABS_NOT_PROSE.search(ln)
                 and sum(c.isalpha() for c in ln) >= 0.6 * len(ln))
        if prose:
            run.append(ln)
            continue
        while run and _headingish(run[0]):  # a journal name or title right above it is not part of it
            run.pop(0)
        t = joined(run)
        if len(t) >= 300 and len(run) >= 3:
            return t[:5000]
        run = []
        if re.match(r"^\s*((\d+|[IVX]+)\.?\s+)?introduction\b", ln, re.I):
            break
    return ""


if __name__ == "__main__":  # the child process started by extract()
    try:
        sys.path[:0] = [p for p in json.loads(os.environ.get("RB_PDF_SYS_PATH", "[]")) if isinstance(p, str)]
    except ValueError:
        pass
    _info: dict = {}
    _text, _title = extract_inprocess(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 80, _info)
    # bytes, not sys.stdout: on Windows a piped stdout uses the locale code page (cp1252), which can't encode
    # characters papers are full of (∗, ≈, Greek letters) and the import failed with UnicodeEncodeError.
    # extract() decodes UTF-8.
    _answer = json.dumps({"text": _text, "title": _title, "info": _info}, ensure_ascii=False).encode("utf-8", "replace")
    if os.environ.get("RB_PDF_OUT"):
        Path(os.environ["RB_PDF_OUT"]).write_bytes(_answer)
    else:
        sys.stdout.buffer.write(_answer)
        sys.stdout.buffer.flush()
