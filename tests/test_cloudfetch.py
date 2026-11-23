"""Papers dragged from OneDrive on the web (app/cloudfetch.py): what a dropped link points at, where the
signed-in window downloads it, and the window part (sign-in shown only when a login page stays up, the file
read out in chunks, plain answers when it can't be had), with a stand-in for pywebview's window."""
import base64
import json
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import cloudfetch as cf  # noqa: E402

T = "https://contoso-my.sharepoint.com"
PATH = "/personal/ann_contoso_edu/Documents/Papers/Bohm criterion.pdf"


def test_parse_onedrive_links():
    by_id = cf.parse(f"{T}/personal/ann_contoso_edu/_layouts/15/onedrive.aspx?id=%2Fpersonal%2Fann_contoso_edu"
                     "%2FDocuments%2FPapers%2FBohm%20criterion.pdf&parent=%2Fpersonal%2Fann_contoso_edu%2FDocuments")
    assert (by_id.kind, by_id.path, by_id.site, by_id.name) == (
        "sharepoint", PATH, "/personal/ann_contoso_edu", "Bohm criterion.pdf")
    new_view = cf.parse(f"{T}/my?id=%2Fpersonal%2Fann_contoso_edu%2FDocuments%2FPapers%2FBohm%20criterion.pdf")
    assert new_view.path == PATH and new_view.site == "/personal/ann_contoso_edu"
    direct = cf.parse(f"{T}/personal/ann_contoso_edu/Documents/Papers/Bohm%20criterion.pdf?web=1")
    assert direct.path == PATH and not direct.share
    share = cf.parse(f"{T}/:b:/g/personal/ann_contoso_edu/EabcDEF?e=x1Y2")
    assert share.share and share.site == "/personal/ann_contoso_edu"
    doc = cf.parse("https://contoso.sharepoint.com/sites/Lab/_layouts/15/Doc.aspx"
                   "?sourcedoc=%7B0A1B2C3D-0000-1111-2222-333344445555%7D&file=notes.pdf")
    assert doc.unique_id == "0A1B2C3D-0000-1111-2222-333344445555" and doc.site == "/sites/Lab" and doc.name == "notes.pdf"
    assert cf.parse("https://1drv.ms/b/s!AbCdEf").kind == "consumer"
    assert cf.parse("https://arxiv.org/pdf/1706.03762") is None
    assert cf.parse("http://contoso-my.sharepoint.com/x.pdf") is None  # only https
    assert cf.parse("https://evil.example/contoso.sharepoint.com/x.pdf") is None
    assert cf.is_cloud(f"{T}/anything") and not cf.is_cloud("https://sharepoint.com.evil.example/x")


def test_downloads_and_anonymous_url():
    link = cf.parse(f"{T}/personal/ann_contoso_edu/Documents/Papers/Bohm%20criterion.pdf")
    first, api, own = cf.downloads(link)
    assert first == (f"{T}/personal/ann_contoso_edu/_layouts/15/download.aspx?SourceUrl="
                     "https%3A%2F%2Fcontoso-my.sharepoint.com%2Fpersonal%2Fann_contoso_edu%2FDocuments%2FPapers%2FBohm%2520criterion.pdf")
    assert api.startswith(f"{T}/personal/ann_contoso_edu/_api/web/GetFileByServerRelativePath(decodedurl='")
    assert own == f"{T}/personal/ann_contoso_edu/Documents/Papers/Bohm%20criterion.pdf"
    assert cf.start_page(link) == f"{T}/personal/ann_contoso_edu/"
    quote = cf.parse(f"{T}/personal/ann_contoso_edu/Documents/O'Neil.pdf")
    assert "O%27%27Neil.pdf" in cf.downloads(quote)[1]  # a quote doubled, as SharePoint's API wants
    share = f"{T}/:b:/g/personal/ann_contoso_edu/EabcDEF?e=x1Y2"
    assert cf.downloads(cf.parse(share)) == [share + "&download=1"]
    assert cf.start_page(cf.parse(share)) == share
    assert cf.anonymous_url(share) == share + "&download=1"
    assert cf.anonymous_url(share + "&download=0") == share + "&download=1"
    token = base64.urlsafe_b64encode(b"https://1drv.ms/b/s!AbCdEf").decode().rstrip("=")
    assert cf.anonymous_url("https://1drv.ms/b/s!AbCdEf") == f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"
    assert cf.anonymous_url(f"{T}/personal/ann_contoso_edu/Documents/x.pdf") is None  # your own file: sign in
    folder = cf.parse(f"{T}/personal/ann_contoso_edu/_layouts/15/onedrive.aspx?view=0")
    assert folder is not None and cf.downloads(folder) == []


class FakeWindow:
    """pywebview's Window as cloudfetch uses it: the pages it goes through (a login, then OneDrive), and the
    page's answers to the download script."""

    def __init__(self, pages, pdf=b"%PDF-1.4 hello", fail=""):
        self.pages, self.pdf, self.fail = list(pages), pdf, fail
        self.events = types.SimpleNamespace(loaded=threading.Event(), closed=_Hook())
        self.events.loaded.set()
        self.shown = self.hidden = self.destroyed = False
        self.fetched = None
        self.chunks = 0

    def get_current_url(self):
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def evaluate_js(self, js):
        if js == "document.readyState":
            return "complete"
        if js.startswith("(function (urls, max)"):
            self.fetched = json.loads(js[js.rindex("})(") + 3:js.rindex(",")])
            return 1
        if "window.__rbCF;" in js:
            if self.fail:
                return json.dumps({"state": "failed", "why": self.fail})
            return json.dumps({"state": "done", "size": len(self.pdf), "name": ""})
        if "subarray(i,i+n)" in js:
            i, n = [int(x) for x in js[js.rindex("})(") + 3:-1].split(",")]
            self.chunks += 1
            return base64.b64encode(self.pdf[i:i + n]).decode()
        raise AssertionError(js)

    def show(self):
        self.shown = True

    def hide(self):
        self.hidden = True

    def destroy(self):
        self.destroyed = True


class _Hook:
    def __init__(self):
        self.fns = []

    def __iadd__(self, fn):
        self.fns.append(fn)
        return self


@pytest.fixture
def window(monkeypatch):
    made = {}

    def create_window(title, url, **kw):
        made["url"], made["kw"] = url, kw
        return made["win"]
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(create_window=create_window))
    monkeypatch.setattr(cf.time, "sleep", lambda s: None)
    clock = {"t": 0.0}

    def tick():
        clock["t"] += 0.5
        return clock["t"]
    monkeypatch.setattr(cf.time, "monotonic", tick)
    return made


LINK = f"{T}/personal/ann_contoso_edu/_layouts/15/onedrive.aspx?id=%2Fpersonal%2Fann_contoso_edu%2FDocuments%2Fbohm.pdf"


def test_signed_in_downloads_without_a_window(window):
    window["win"] = w = FakeWindow([LINK])
    asked = []
    name, data = cf.Fetcher("http://127.0.0.1:1", None).fetch(LINK, on_signin=lambda: asked.append(1))
    assert (name, data) == ("bohm.pdf", b"%PDF-1.4 hello")
    assert window["url"] == f"{T}/personal/ann_contoso_edu/" and window["kw"]["hidden"] is True
    assert not w.shown and not asked and w.destroyed
    assert w.fetched[0].startswith(f"{T}/personal/ann_contoso_edu/_layouts/15/download.aspx?SourceUrl=")


def test_sign_in_window_shown_only_while_a_login_stays_up(window, monkeypatch):
    monkeypatch.setattr(cf, "CHUNK", 4)  # several hops out of the page
    login = "https://login.microsoftonline.com/common/oauth2/authorize?client_id=x"
    window["win"] = w = FakeWindow([login] * 6 + ["https://login.contoso.edu/duo"] * 3 + [LINK])
    asked = []
    name, data = cf.Fetcher("http://127.0.0.1:1", None).fetch(LINK, on_signin=lambda: asked.append(1))
    assert data == b"%PDF-1.4 hello" and w.chunks == 4
    assert w.shown and w.hidden and asked == [1]

    window["win"] = w = FakeWindow([login, LINK])  # still signed in: a quick hop through the login shows nothing
    cf.Fetcher("http://127.0.0.1:1", None).fetch(LINK)
    assert not w.shown


@pytest.mark.parametrize("why,says", [("HTTP 403", "can't open that file"), ("HTTP 404", "moved or renamed"),
                                      ("not a PDF", "isn't a PDF"), ("too big", "larger than")])
def test_download_failures_say_why(window, why, says):
    window["win"] = FakeWindow([LINK], fail=why)
    with pytest.raises(cf.CloudError, match=says):
        cf.Fetcher("http://127.0.0.1:1", None).fetch(LINK)


def test_links_it_cant_download(window):
    f = cf.Fetcher("http://127.0.0.1:1", None)
    with pytest.raises(cf.CloudError, match="personal OneDrive"):
        f.fetch("https://1drv.ms/b/s!AbCdEf")
    with pytest.raises(cf.CloudError, match="folder"):
        f.fetch(f"{T}/personal/ann_contoso_edu/_layouts/15/onedrive.aspx?view=0")
    with pytest.raises(cf.CloudError, match="isn't a PDF"):
        f.fetch(f"{T}/personal/ann_contoso_edu/Documents/notes.docx")
    assert "url" not in window  # no window for any of them


def test_closing_the_sign_in_window_stops(window):
    window["win"] = w = FakeWindow(["https://login.microsoftonline.com/x"])

    def show():  # you close it as soon as it's up
        w.shown = True
        for fn in w.events.closed.fns:
            fn()
    w.show = show
    with pytest.raises(cf.CloudError, match="Sign-in was closed"):
        cf.Fetcher("http://127.0.0.1:1", None).fetch(LINK)


def test_reply_carries_the_paper_or_the_error(window, monkeypatch):
    window["win"] = FakeWindow([LINK])
    monkeypatch.setattr(cf, "import_pdf", lambda server, name, data: {"id": "p1", "title": name})
    got = []
    f = cf.Fetcher("http://127.0.0.1:1", got.append)
    f._run("c1", LINK)
    assert got == [{"cloud": "c1", "note": {"id": "p1", "title": "bohm.pdf"}}]
    got.clear()
    f._run("c2", "https://1drv.ms/b/s!AbCdEf")
    assert got[0]["cloud"] == "c2" and "personal OneDrive" in got[0]["error"]


def test_multipart_is_a_file_upload():
    body, ctype = cf._multipart('a "b".pdf', b"%PDF-x")
    boundary = ctype.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode()) and body.endswith(f"\r\n--{boundary}--\r\n".encode())
    assert b'filename="a _b_.pdf"' in body and b"\r\n\r\n%PDF-x\r\n" in body
