"""Dropping a folder on the window (static/app.js droppedFolders, static/live.js droppedPaths). In research brain's
own window (Mac or Windows) the window says where the folder is, and it's added like Add folders of papers (kept
where it is); in a browser its PDFs are read from the drop and offered like Import a folder (copied). The drop is
synthetic: Playwright can't drag a real folder in, so its DataTransfer and directory entries are faked.

Reuses the server/browser fixtures from test_e2e.py. Skipped when Playwright isn't available.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import browser, ctx, server  # noqa: E402,F401  (fixtures are used by name)

# a folder "Plasma" holding a.pdf, notes.txt and a subfolder "Hall" with b.pdf; and a loose c.pdf beside it
DROP = """(withLoose) => {
  const pdf = (n) => new File(['%PDF-1.4 x'], n, { type: 'application/pdf' });
  const fileEntry = (n, f) => ({ name: n, isFile: true, isDirectory: false, file: (ok) => ok(f) });
  const dirEntry = (n, kids) => ({ name: n, isFile: false, isDirectory: true, createReader() {
    let done = false; return { readEntries: (ok) => { const batch = done ? [] : kids; done = true; setTimeout(() => ok(batch), 0); } }; } });
  const hall = dirEntry('Hall', [fileEntry('b.pdf', pdf('b.pdf'))]);
  const root = dirEntry('Plasma', [fileEntry('a.pdf', pdf('a.pdf')), fileEntry('notes.txt', new File(['x'], 'notes.txt')), hall]);
  const folderFile = new File([], 'Plasma');
  const loose = pdf('c.pdf');
  const items = [{ kind: 'file', webkitGetAsEntry: () => root }];
  const files = [folderFile];
  if (withLoose) { items.push({ kind: 'file', webkitGetAsEntry: () => fileEntry('c.pdf', loose) }); files.push(loose); }
  window.__importedLoose = [];
  const ev = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'dataTransfer', { value: { types: ['Files'], files, items } });
  window.dispatchEvent(ev);
}"""


def open_page(ctx, server, query="", init=""):
    if init:
        ctx.add_init_script(init)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    page.goto(server["base"] + "/" + query)
    page.wait_for_function("window.__rb && window.__rbLive && window.__rbBulk")
    return page


def spy_bulk(page):
    page.evaluate("""() => {
      window.__linked = []; window.__copied = [];
      window.__rbBulk.linkDialog = (p) => window.__linked.push(p);
      window.__rbBulk.folderDialog = (fs) => window.__copied.push(fs.map((f) => f.webkitRelativePath));
    }""")


def test_browser_drop_offers_to_import_the_folders_pdfs(ctx, server):
    page = open_page(ctx, server)
    spy_bulk(page)
    page.evaluate(DROP, False)
    page.wait_for_function("window.__copied.length === 1")
    assert sorted(page.evaluate("window.__copied[0]")) == ["Plasma/Hall/b.pdf", "Plasma/a.pdf"]
    assert page.evaluate("window.__linked") == []
    assert not page.errors


def test_windows_window_drop_links_the_folder_where_it_is(ctx, server):
    fake = """
    window.chrome = window.chrome || {};
    window.chrome.webview = { __l: [], postMessage() {},
      addEventListener(t, f) { if (t === 'message') this.__l.push(f); },
      postMessageWithAdditionalObjects(msg, files) {  // as app/winwindow.py answers: where each dropped item is
        const paths = Array.from(files).map((f) => ({ name: f.name, path: 'C:\\\\Users\\\\me\\\\Documents\\\\' + f.name }));
        setTimeout(() => this.__l.forEach((f) => f({ data: { rb: { dropped: msg.rb.dropped, paths } } })), 10);
      } };"""
    page = open_page(ctx, server, "?app=win", fake)
    spy_bulk(page)
    page.evaluate(DROP, False)
    page.wait_for_function("window.__linked.length === 1")
    assert page.evaluate("window.__linked") == ["C:\\Users\\me\\Documents\\Plasma"]
    assert page.evaluate("window.__copied") == []
    assert not page.errors


def test_mac_window_drop_links_the_folder_where_it_is(ctx, server):
    fake = """
    window.webkit = { messageHandlers: { rb: { postMessage(s) {  // as app/macwindow.py answers through __rbDropped
      const m = JSON.parse(s);
      if (m.dropped) setTimeout(() => window.__rbDropped({ dropped: m.dropped,
        paths: m.names.map((n) => ({ name: n, path: '/Users/me/Papers/' + n })) }), 10);
    } } } };"""
    page = open_page(ctx, server, "?app=mac", fake)
    spy_bulk(page)
    page.evaluate(DROP, False)
    page.wait_for_function("window.__linked.length === 1")
    assert page.evaluate("window.__linked") == ["/Users/me/Papers/Plasma"]
    assert not page.errors


def test_window_that_doesnt_answer_falls_back_to_importing(ctx, server):
    fake = "window.webkit = { messageHandlers: { rb: { postMessage() {} } } };"  # never answers
    page = open_page(ctx, server, "?app=mac", fake)
    spy_bulk(page)
    page.evaluate(DROP, False)
    page.wait_for_function("window.__copied.length === 1", timeout=6000)
    assert page.evaluate("window.__linked") == []
    assert not page.errors
