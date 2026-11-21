"""Reading text out of PDFs (app/pdftext.py): a paper's abstract comes from its PDF when no catalogue has one (APS
records on Crossref carry none), and the parser's child process hands back text the console code page can't spell.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import app.pdftext as pdftext
from app.pdftext import guess_abstract

from test_api import client, make_pdf  # noqa: F401  (fixture)

APS_ABSTRACT = [
    "The classical and quantum theories leading to the asymptotic Bethe formula of the stopping power of",
    "matter for charged particles heavier than the electron are briefly reviewed. Models and approximations for",
    "the practical calculation of various corrections that extend the validity range of the formula are described.",
    "The asymptotic formula and the associated shell correction were determined previously from an extensive",
    "database of atomic generalized oscillator strengths, calculated for an independent-electron model.",
]

# page 1 as pdftext.extract gives it for a Physical Review paper: no "Abstract" heading
APS_PAGE = "\n\n".join([
    "[[page 1]]", "PHYSICAL REVIEW A 106, 032809 (2022)",
    "Bethe stopping-power formula and its corrections", "Francesc Salvat *",
    "Facultat de Física, FQA and ICC, Universitat de Barcelona, Diagonal 645, 08028 Barcelona, Catalonia, Spain",
    "(Received 10 May 2022; accepted 29 August 2022; published 19 September 2022)",
    *APS_ABSTRACT,
    "DOI: 10.1103/PhysRevA.106.032809", "I. INTRODUCTION",
    "A fast charged particle in matter loses energy through interactions of different kinds, namely, inelastic",
    "collisions, i.e., interactions that produce electronic excitations of the material and many other things too.",
])


def test_guess_abstract_finds_the_abstract_block_on_page_one():
    """An unlabelled abstract between the authors and the first section; one after an "Abstract." heading that
    ends at Keywords; the first prose block rather than a later, longer one; and nothing on a page of short lines."""
    a = guess_abstract(APS_PAGE)
    assert a.startswith("The classical and quantum theories") and a.endswith("independent-electron model.")
    assert "DOI" not in a and "Barcelona" not in a and "fast charged particle" not in a

    text = "\n".join(["[[page 1]]", "A title of the paper here", "Some Author, Some University",
                      "Abstract. " + APS_ABSTRACT[0], *APS_ABSTRACT[1:], "Keywords: stopping power",
                      "1. Introduction", "Body text that must not be part of the abstract at all, not one bit."])
    a = guess_abstract(text)
    assert a.startswith("The classical") and "Keywords" not in a and "Body text" not in a

    # no Introduction heading: the block ending at "Keywords:" wins over the longer body text after it
    text = "\n\n".join(["[[page 1]]", "Plasma and Fusion Research: Letters", *APS_ABSTRACT, "Keywords:", "DOI:",
                        *(APS_ABSTRACT[::-1] * 3)])
    assert guess_abstract(text) == " ".join(APS_ABSTRACT)

    assert guess_abstract("[[page 1]]\nTitle\nAuthor\n\n[[page 2]]\nThe loss is L.") == ""


def _aps_pdf() -> bytes:
    return make_pdf("Bethe stopping-power formula and its corrections",
                    "\n".join(["Francesc Salvat", "Universitat de Barcelona, Barcelona, Spain",
                               "(Received 10 May 2022; accepted 29 August 2022)", "", *APS_ABSTRACT, "",
                               "DOI: 10.1103/PhysRevA.106.032809", "I. INTRODUCTION"]))


def test_imported_and_already_stored_papers_get_their_abstract_from_the_pdf(client):
    """An imported PDF gets its abstract at once; a stored paper without one gets it from fill_missing_abstracts
    at startup, and a second run has nothing left to do."""
    from app import server
    r = client.post("/api/import/pdf", files={"file": ("bethe.pdf", _aps_pdf(), "application/pdf")})
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    n = client.get(f"/api/notes/{nid}").json()
    assert n["abstract"].startswith("The classical and quantum theories"), n["abstract"]
    n = server.store.get(nid)
    n["abstract"] = ""
    server.store.save(n)
    assert server.store.fill_missing_abstracts() == [nid]
    assert server.store.get(nid)["abstract"].startswith("The classical")
    assert server.store.fill_missing_abstracts() == []  # nothing left to do the second time


TEXT = "Electron temperature T∗e ≈ 5 eV, ∂n/∂t, α β γ"

# A stand-in for pypdfium2 (one page with TEXT), then the child entry point, with stdout as on Windows.
WRAPPER = r'''
import io, runpy, sys, types
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="cp1252")
TEXT = sys.argv.pop()
class _TP:
    def get_text_range(self): return TEXT
    def close(self): pass
class _Page:
    def get_textpage(self): return _TP()
    def close(self): pass
class _Doc:
    def __init__(self, path): pass
    def get_metadata_dict(self): return {"Title": ""}
    def __len__(self): return 1
    def __getitem__(self, i): return _Page()
    def close(self): pass
sys.modules["pypdfium2"] = types.SimpleNamespace(PdfDocument=_Doc)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''


def test_child_output_survives_a_cp1252_stdout(tmp_path):
    """On Windows a piped stdout is encoded with the locale code page (cp1252), so writing "∗" (U+2217), "≈" or
    Greek letters through sys.stdout raised UnicodeEncodeError and every such PDF failed to import. The child now
    writes UTF-8 bytes. This forces a cp1252 stdout on any platform to reproduce that."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    proc = subprocess.run([sys.executable, "-c", WRAPPER, str(Path(pdftext.__file__).resolve()), str(pdf), "5", TEXT],
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = json.loads(proc.stdout.decode("utf-8"))
    assert TEXT in out["text"]
