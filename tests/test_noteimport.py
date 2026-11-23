"""Notes written elsewhere: pasted formatting, .md/.txt/.html/.docx files, zips of them, handwritten scans and
photos (kept as they are, and written out by the AI), and figures put into a note.

Run:  .venv/bin/python -m pytest -q tests/test_noteimport.py
"""
import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rb_app import load_server  # noqa: E402
from app import noteimport as ni  # noqa: E402


# ---------------------------------------------------------------- HTML (paste)
def test_nested_lists_become_four_space_levels():
    md = ni.html_to_markdown("<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li><li>d</li></ul>")
    assert md == "- a\n    - b\n        - c\n- d"


def test_ordered_and_checkbox_items():
    md = ni.html_to_markdown('<ol><li>one</li><li>two<ul><li><input type="checkbox" checked> done</li>'
                             '<li><input type="checkbox"> todo</li></ul></li></ol>')
    assert md == "1. one\n2. two\n    - [x] done\n    - [ ] todo"


def test_google_docs_flat_lists_and_inline_styles():
    """Google Docs: everything inside <b style="font-weight:normal">, depth on aria-level, bold/italic as spans."""
    html = ('<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1">'
            '<h2 dir="ltr"><span style="font-weight:700">Week 3</span></h2>'
            '<ul><li aria-level="1"><p><span style="font-weight:400">Plain </span><span style="font-weight:700">bold</span></p></li>'
            '<li aria-level="2"><p><span style="font-style:italic">nested italic</span></p></li></ul></b>')
    md = ni.html_to_markdown(html)
    assert md.startswith("## **Week 3**") or md.startswith("## Week 3")
    assert "- Plain **bold**" in md
    assert "    - *nested italic*" in md


def test_word_mso_list_paragraphs():
    html = ('<p class=MsoListParagraph style="mso-list:l0 level1 lfo1"><![if !supportLists]><span style="mso-list:Ignore">·'
            '<span>&nbsp;&nbsp;</span></span><![endif]>Top</p>'
            '<p class=MsoListParagraph style="mso-list:l0 level2 lfo1"><span style="mso-list:Ignore">o<span> </span></span>Child</p>'
            '<p class=MsoNormal>After</p>')
    md = ni.html_to_markdown(html)
    assert md.splitlines()[:2] == ["- Top", "    - Child"]
    assert md.rstrip().endswith("After")


def test_links_quotes_code_tables_and_junk():
    md = ni.html_to_markdown('<style>p{}</style><script>alert(1)</script><p>See <a href="https://x.org">this</a> and '
                             '<a href="javascript:alert(1)">that</a>, <code>x+1</code>, <mark>key</mark>.</p>'
                             '<blockquote><p>quoted</p></blockquote>'
                             '<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>')
    assert "alert" not in md and "javascript" not in md
    assert "[this](https://x.org)" in md and "that," in md and "`x+1`" in md and "==key==" in md
    assert "> quoted" in md
    assert "| a | b |\n|---|---|\n| 1 | 2 |" in md


# ---------------------------------------------------------------- files
def _docx(paras: str, numbering: str = "") -> bytes:
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document {W}><w:body>{paras}</w:body></w:document>')
        if numbering:
            z.writestr("word/numbering.xml", f'<w:numbering {W}>{numbering}</w:numbering>')
    return buf.getvalue()


def _p(text, style="", num=None, lvl=0, bold=False):
    ppr = ""
    if style or num is not None:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              (f'<w:numPr><w:ilvl w:val="{lvl}"/><w:numId w:val="{num}"/></w:numPr>' if num is not None else "") + "</w:pPr>"
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p>{ppr}<w:r>{rpr}<w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


def test_docx_headings_bullets_numbers():
    numbering = ('<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
                 '<w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
                 '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
                 '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>')
    data = _docx(_p("Lecture 4", "Title") + _p("Waves", "Heading1") + _p("Intro with ") +
                 _p("point", num=1) + _p("sub point", num=1, lvl=1) + _p("first", num=2) + _p("second", num=2, bold=True),
                 numbering)
    title, md = ni.docx_to_markdown(data)
    assert title == "Lecture 4"
    assert md == "# Waves\n\nIntro with\n\n- point\n    - sub point\n1. first\n2. **second**"


def test_text_note_frontmatter_heading_and_name():
    n = ni.text_note("x.md", "---\ntitle: From Obsidian\ntags: [plasma, '#waves']\n---\nBody [[Link]]\n")
    assert n == {"title": "From Obsidian", "body": "Body [[Link]]\n", "tags": ["plasma", "waves"]}
    n = ni.text_note("x.md", "# Heading title\n\n- a\n    - b\n")
    assert n["title"] == "Heading title" and n["body"] == "- a\n    - b\n"
    assert ni.text_note("Meeting notes 0123456789abcdef0123456789abcdef.md", "hi")["title"] == "Meeting notes"


def test_zip_reads_note_files_and_skips_the_rest():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Vault/A.md", "# A\nalpha")
        z.writestr("Vault/sub/B.txt", "beta")
        z.writestr("Vault/.obsidian/workspace.md", "hidden")
        z.writestr("__MACOSX/Vault/._A.md", "junk")
        z.writestr("Vault/pic.png", b"\x89PNG")
        z.writestr("Export/Page 0123456789abcdef0123456789abcdef.html", "<html><title>Page</title><h1>Page</h1><p>gamma</p></html>")
    notes, skipped = ni.notes_from_file("vault.zip", buf.getvalue())
    assert sorted(n["title"] for n in notes) == ["A", "B", "Page"]
    page = next(n for n in notes if n["title"] == "Page")
    assert page["body"] == "gamma\n"  # the heading that repeats the title is dropped
    assert skipped == []


# ---------------------------------------------------------------- endpoints
@pytest.fixture()
def srv(tmp_path, monkeypatch):
    return load_server(monkeypatch, tmp_path)


@pytest.fixture()
def client(srv):
    from fastapi.testclient import TestClient
    return TestClient(srv.app)


def test_convert_html_endpoint(client):
    r = client.post("/api/notes/convert-html", json={"html": "<ul><li><b>x</b><ul><li>y</li></ul></li></ul>"})
    assert r.status_code == 200 and r.json()["markdown"] == "- **x**\n    - y"


def test_import_notes_creates_and_skips_duplicates(client):
    files = [("files", ("one.md", b"# One\n- a\n    - b\n", "text/markdown")),
             ("files", ("two.docx", _docx(_p("Two body")), "application/octet-stream")),
             ("files", ("bad.exe", b"MZ", "application/octet-stream"))]
    r = client.post("/api/import/notes", files=files, data={"topic": ""})
    assert r.status_code == 201, r.text
    out = r.json()
    assert sorted(c["title"] for c in out["created"]) == ["One", "two"]
    assert [s["name"] for s in out["skipped"]] == ["bad.exe"]
    one = next(c for c in out["created"] if c["title"] == "One")
    assert client.get(f"/api/notes/{one['id']}").json()["body"].rstrip() == "- a\n    - b"
    again = client.post("/api/import/notes", files=files[:1]).json()
    assert again["created"] == [] and again["skipped"][0]["why"] == "already in your library"


def _tiny_jpeg() -> bytes:
    pil = pytest.importorskip("PIL.Image")  # comes with reportlab (requirements-dev)
    b = io.BytesIO()
    pil.new("RGB", (300, 400), "white").save(b, "JPEG")
    return b.getvalue()


def test_handwritten_photos_kept_as_pdf_then_transcribed(client):
    jpg = _tiny_jpeg()
    r = client.post("/api/import/handwritten", files=[("files", ("IMG_1.jpg", jpg, "image/jpeg")),
                                                      ("files", ("IMG_2.jpg", jpg, "image/jpeg"))],
                    data={"title": "Lab notebook"})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["title"] == "Lab notebook" and n["has_pdf"] and n["body"] == "" and "handwritten" in n["tags"]
    import pypdfium2 as pdfium
    srvmod = sys.modules["app.server"]
    doc = pdfium.PdfDocument(str(srvmod.store.pdf_path(n["id"])))
    assert len(doc) == 2
    doc.close()
    t = client.post(f"/api/notes/{n['id']}/transcribe")
    assert t.status_code == 403 and "only if you say so" in t.json()["detail"]  # notes are private by default
    t = client.post(f"/api/notes/{n['id']}/transcribe", json={"send_private": True})
    assert t.status_code == 200, t.text
    body = t.json()["body"]
    assert "read 2 image(s)" in body and "    - nested point" in body
    assert t.json()["has_pdf"]  # the scan stays


def test_handwritten_pdf_scan_without_text(client, tmp_path):
    jpg = _tiny_jpeg()
    pdf = tmp_path / "scan.pdf"
    ni.images_to_pdf([jpg], pdf)
    r = client.post("/api/import/handwritten", files=[("files", ("Scan 3.pdf", pdf.read_bytes(), "application/pdf"))])
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Scan 3" and r.json()["has_pdf"]
    bad = client.post("/api/import/handwritten", files=[("files", ("x.png", b"\x89PNG\r\n\x1a\n...", "image/png"))])
    assert bad.status_code == 400


def test_transcribe_refuses_without_scan(client):
    nid = client.post("/api/notes", json={"type": "note", "title": "t"}).json()["id"]
    assert client.post(f"/api/notes/{nid}/transcribe").status_code == 400


def test_transcribe_with_a_backend_that_cant_read_images(client, srv, monkeypatch):
    class NoEyes:
        name = "Some CLI"
        id = "x"
        reads_images = False
    jpg = _tiny_jpeg()
    nid = client.post("/api/import/handwritten", files=[("files", ("a.jpg", jpg, "image/jpeg"))]).json()["id"]
    monkeypatch.setattr(srv, "_ai_agent", lambda: NoEyes())
    r = client.post(f"/api/notes/{nid}/transcribe", json={"send_private": True})
    assert r.status_code == 400 and "can't read images" in r.json()["detail"]


def test_note_image_upload_and_serving(client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    r = client.post("/api/notes/image?name=Figure 2.png", content=png, headers={"Content-Type": "image/png"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["url"].startswith("/attachments/figure-2-") and out["markdown"] == f"![Figure 2]({out['url']})"
    g = client.get(out["url"])
    assert g.status_code == 200 and g.content == png
    svg = client.post("/api/notes/image", content=b"<svg onload=alert(1)>", headers={"Content-Type": "image/png"})
    assert svg.status_code == 415
    wrong = client.post("/api/notes/image", content=png, headers={"Content-Type": "text/plain"})
    assert wrong.status_code in (400, 415)
