"""The PDF parser's child process must hand back text the console code page can't spell.

On Windows a piped stdout is encoded with the locale code page (cp1252), so writing "∗" (U+2217), "≈" or Greek
letters through sys.stdout raised UnicodeEncodeError and every such PDF failed to import. The child now writes
UTF-8 bytes. This test forces a cp1252 stdout on any platform to reproduce that."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import app.pdftext as pdftext

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
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    proc = subprocess.run([sys.executable, "-c", WRAPPER, str(Path(pdftext.__file__).resolve()), str(pdf), "5", TEXT],
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = json.loads(proc.stdout.decode("utf-8"))
    assert TEXT in out["text"]
