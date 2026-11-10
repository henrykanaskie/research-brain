"""The app must work with no internet: every script and stylesheet is served from static/vendor/.

Fails if any <script src> or <link rel=stylesheet href> in static/*.html points at http(s) (or a
protocol-relative //host URL), if a referenced local file is missing, or if a vendored CSS file pulls
fonts or imports from the network.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HTML_FILES = sorted(STATIC.glob("*.html"))
REMOTE = re.compile(r"^\s*(https?:)?//", re.I)


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []  # (tag, url)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("src"):
            self.refs.append(("script", a["src"]))
        if tag == "link" and "stylesheet" in (a.get("rel") or "").lower().split() and a.get("href"):
            self.refs.append(("stylesheet", a["href"]))
        if tag == "link" and (a.get("rel") or "").lower() in ("preconnect", "dns-prefetch", "preload", "modulepreload") \
                and a.get("href"):
            self.refs.append((a["rel"], a["href"]))


def _refs(path):
    p = _Refs()
    p.feed(path.read_text(encoding="utf-8"))
    return p.refs


def _local_path(url):
    assert url.startswith("/static/"), f"expected an absolute /static/ path, got {url!r}"
    return STATIC / url.split("?", 1)[0].split("#", 1)[0][len("/static/"):]


def test_html_files_found():
    names = {p.name for p in HTML_FILES}
    assert {"index.html", "viewer.html"} <= names


@pytest.mark.parametrize("html", HTML_FILES, ids=lambda p: p.name)
def test_no_remote_scripts_or_styles(html):
    remote = [(tag, url) for tag, url in _refs(html) if REMOTE.match(url)]
    assert not remote, f"{html.name} loads from the network: {remote}"


@pytest.mark.parametrize("html", HTML_FILES, ids=lambda p: p.name)
def test_referenced_local_files_exist(html):
    for tag, url in _refs(html):
        if url.startswith("data:"):
            continue
        assert _local_path(url).is_file(), f"{html.name}: {tag} {url} is missing"


def test_no_remote_module_imports_in_viewer():
    src = (STATIC / "viewer.html").read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in src and "unpkg.com" not in src
    for f in ("build/pdf.min.mjs", "build/pdf.worker.min.mjs"):
        assert (STATIC / "vendor" / "pdfjs" / f).is_file()
    assert (STATIC / "vendor" / "pdfjs" / "cmaps").is_dir()
    assert (STATIC / "vendor" / "pdfjs" / "standard_fonts").is_dir()


def test_vendored_css_is_self_contained():
    for css in (STATIC / "vendor").rglob("*.css"):
        text = css.read_text(encoding="utf-8")
        assert "@import" not in text, css
        for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", text):
            assert not REMOTE.match(url), f"{css} references {url}"
            if url.startswith("data:"):
                continue
            assert (css.parent / url).resolve().is_file(), f"{css}: {url} is missing"
