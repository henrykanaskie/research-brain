"""Static, read-only HTML export of selected notes (for sharing a reading list with the lab).

    site = export_site(store, ids=None | [ids], include_pdfs=False, static_dir=STATIC)

writes  <vault>/exports/<timestamp>/
          index.html            the selected notes, papers first
          notes/<id>.html       one page per note: metadata, abstract, summary, notes, fragments
          pdfs/<id>.pdf         only with include_pdfs
          assets/               vendored KaTeX (woff2), marked, the LaTeX renderer, fonts, CSS
Everything is local files with relative links, so the folder can be zipped, opened from disk, or
put on any static host. Nothing is fetched from or sent to the network.

Markdown and math are rendered in the browser with the same renderer the app uses
(static/latex-editor.js + KaTeX), so pages look like the app. Each element also carries its raw
text, so the page is still readable with JavaScript off.

[[wikilinks]] and manual links to notes that are part of the export become links; links to notes
that aren't exported become plain text (their alias, or what was written). Titles of notes outside
the export are never written into it. Claude's critiques are not exported. Private notes (app/privacy.py)
are left out (no page, no fragment, no title in a link) unless include_private=True.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|]+?)(?:\|([^\[\]\n]*))?\]\]")
WIKI_FIELDS = ("summary", "body", "quote")

# (source under static/, destination under assets/)
ASSETS = [
    ("vendor/katex/katex.min.css", "katex/katex.min.css"),
    ("vendor/katex/katex.min.js", "katex/katex.min.js"),
    ("vendor/katex/fonts", "katex/fonts"),
    ("vendor/marked.min.js", "marked.min.js"),
    ("vendor/fonts", "fonts"),
    ("latex-editor.js", "latex-editor.js"),
]


class ExportError(ValueError):
    pass


def _title_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _json_script(obj) -> str:
    # Safe inside <script type="application/json">: no "</script>" or "<!--" can appear.
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028") \
        .replace("\u2029", "\\u2029")


def _stamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y-%m-%d_%H%M%S")


def select(notes: list[dict], ids: list[str] | None) -> tuple[list[dict], dict[str, dict]]:
    """Return (pages, fragments_by_parent). ids=None means every paper and note.
    A selected paper/note brings its fragments along (shown on its page). A selected fragment whose
    parent isn't selected gets a page of its own."""
    by_id = {n["id"]: n for n in notes}
    if ids is None:
        chosen = [n["id"] for n in notes if n["type"] != "fragment"]
    else:
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise KeyError(missing[0])
        chosen = list(dict.fromkeys(ids))
    if not chosen:
        raise ExportError("Nothing to export.")
    chosen_set = set(chosen)
    pages = [by_id[i] for i in chosen
             if by_id[i]["type"] != "fragment" or by_id[i]["parent"] not in chosen_set]
    page_ids = {n["id"] for n in pages}
    frags: dict[str, list[dict]] = {}
    for n in notes:
        if n["type"] == "fragment" and n["parent"] in page_ids and n["id"] not in page_ids:
            frags.setdefault(n["parent"], []).append(n)
    for lst in frags.values():
        lst.sort(key=lambda f: (f["page"] is None, f["page"] or 0, f["created"] or "", f["id"]))
    pages.sort(key=lambda n: ({"paper": 0, "note": 1}.get(n["type"], 2), (n["title"] or "").casefold()))
    return pages, frags


def _resolver(notes: list[dict]):
    """Same rule as the server: an exact id, else a case-insensitive title (non-fragments win, then oldest)."""
    ids = {n["id"] for n in notes}
    titles: dict[str, str] = {}
    for n in sorted(notes, key=lambda n: (n["type"] == "fragment", n["created"] or "", n["id"])):
        titles.setdefault(_title_key(n["title"]), n["id"])

    def resolve(target: str) -> str | None:
        t = target.strip()
        return t if t in ids else titles.get(_title_key(t))
    return resolve


def _href(target_id: str, where: dict[str, str], from_index: bool) -> str:
    """where: note id -> page id it's shown on (itself, or its parent page for a fragment)."""
    page = where[target_id]
    base = "notes/" if from_index else ""
    return f"{base}{page}.html" + (f"#{target_id}" if page != target_id else "")


def _links_for(texts: list[str], resolve, where: dict[str, str], titles: dict[str, str]) -> dict:
    """Map each raw [[target]] written in `texts` to {"href","title"} if exported, else {} (plain text)."""
    out: dict[str, dict] = {}
    for text in texts:
        for m in WIKILINK_RE.finditer(text or ""):
            raw = m.group(1).strip()
            if raw in out:
                continue
            tid = resolve(raw)
            out[raw] = {"href": _href(tid, where, False), "title": titles[tid]} if tid in where else {}
    return out


CSS = """
:root{--ground:#F6F7F8;--sheet:#FFFFFF;--ink:#1E2226;--ink-2:#5A6169;--rule:#E6E8EB;--red:#B23A33;
--machine:#7D8A98;--serif:"STIX Two Text","STIX Two","Times New Roman",Georgia,serif;
--sans:"Instrument Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--ground:#16191D;--sheet:#1D2126;--ink:#E4E7EA;--ink-2:#9AA2AB;
--rule:#2E343B;--red:#E06A60;--machine:#7F8C9A;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:13px/1.45 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:underline;text-decoration-color:var(--rule);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
.sheet{max-width:760px;margin:0 auto;background:var(--sheet);min-height:100vh;padding:48px 32px 64px;
border-left:1px solid var(--rule);border-right:1px solid var(--rule)}
.top{display:flex;gap:16px;align-items:baseline;color:var(--ink-2);margin-bottom:40px}
.top .sp{flex:1}
h1{font:600 28px/1.15 var(--serif);letter-spacing:-.01em;margin:0 0 12px}
.meta{color:var(--ink-2);display:flex;flex-wrap:wrap;gap:4px 16px;font-variant-numeric:tabular-nums}
.ident{font-family:var(--mono);font-size:11.5px}
.kind{display:inline-flex;align-items:center;gap:6px;color:var(--ink-2);margin-bottom:12px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ink);display:inline-block}
.dot.note{background:none;border:1.5px solid var(--ink)}.dot.fragment{background:var(--red);width:5px;height:5px}
section{margin-top:32px}
h2{font:600 13px/1.3 var(--sans);margin:0 0 10px}
.md{font:16px/1.6 var(--serif);color:var(--ink);overflow-wrap:anywhere;max-width:68ch}
.md>:first-child{margin-top:0}.md>:last-child{margin-bottom:0}.md p{margin:0 0 .65em}
.md h1,.md h2,.md h3,.md h4{font-family:var(--sans);font-weight:600;margin:1.1em 0 .4em;line-height:1.3}
.md h1{font-size:17px}.md h2{font-size:15px}.md h3,.md h4{font-size:14px}
.md ul,.md ol{padding-left:1.3em;margin:0 0 .65em}
.md code{font-family:var(--mono);font-size:.8em;background:var(--ground);padding:1px 4px;border-radius:2px}
.md pre{background:var(--ground);padding:8px 10px;overflow-x:auto;border-radius:3px}.md pre code{background:none;padding:0}
.md blockquote{margin:0 0 .65em;border-left:2px solid var(--rule);padding-left:12px;color:var(--ink-2)}
.md table{border-collapse:collapse;font-size:14px}.md th,.md td{border:1px solid var(--rule);padding:3px 8px}
.md .katex-display{overflow-x:auto;overflow-y:hidden;padding:2px 0;margin:.5em 0}
.md.raw{white-space:pre-wrap}
.mine{border-left:2px solid var(--red);padding-left:14px}
.frag{padding:14px 0;border-top:1px solid var(--rule)}.frag:first-of-type{border-top:none;padding-top:0}
.frag:target{background:linear-gradient(var(--sheet),var(--sheet))}
.frag .loc{color:var(--ink-2);font-variant-numeric:tabular-nums;margin-bottom:6px}
.frag .quote{font-style:italic}.frag .note{margin-top:8px;font-size:15px}
.cite{color:var(--ink);text-decoration-color:var(--red)}
.plain-link{color:var(--ink-2)}
ul.list{list-style:none;margin:0;padding:0}
ul.list li{padding:14px 0;border-top:1px solid var(--rule)}ul.list li:first-child{border-top:none}
ul.list .t{font:600 17px/1.3 var(--serif);text-decoration:none}ul.list .t:hover{text-decoration:underline}
ul.list .s{color:var(--ink-2);margin-top:4px;display:flex;gap:12px;flex-wrap:wrap}
ul.list .sum{font:14.5px/1.5 var(--serif);margin-top:6px;color:var(--ink)}
.lede{color:var(--ink-2);font-size:13px;margin:0 0 32px}
@media (max-width:640px){.sheet{padding:32px 16px 48px;border:none}h1{font-size:24px}}
"""

# Renders [data-md] elements from the JSON blob with the app's markdown + KaTeX renderer.
RENDER_JS = """(function(){
  var data = JSON.parse(document.getElementById('rb-data').textContent);
  var esc = function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
  function wiki(s, put){
    return s.replace(/\\[\\[([^\\[\\]\\n|]+?)(?:\\|([^\\[\\]\\n]*))?\\]\\]/g, function(m, raw, alias){
      raw = raw.trim(); alias = (alias||'').trim();
      var t = data.links[raw] || {};
      var label = alias || t.title || raw;
      if (/(LEPH)\\d+XL/.test(label)) return t.href ? put('<a class="cite" href="'+esc(t.href)+'">')+label+put('</a>') : label;
      return put(t.href ? '<a class="cite" href="'+esc(t.href)+'">'+esc(label)+'</a>' : '<span class="plain-link">'+esc(label)+'</span>');
    });
  }
  function go(){
    if (!window.LatexEditor) return;
    var els = document.querySelectorAll('[data-md]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i], src = data.fields[el.getAttribute('data-md')];
      if (src == null) continue;
      var out = window.LatexEditor.render(src, {inline: el.hasAttribute('data-inline'), transform: wiki});
      if (out) { el.innerHTML = out; el.classList.remove('raw'); }
    }
    var as = document.querySelectorAll('.md a[href^="http"]');
    for (var j = 0; j < as.length; j++) { as[j].target = '_blank'; as[j].rel = 'noopener noreferrer'; }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();
"""


def _head(title: str, prefix: str) -> str:
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta name="generator" content="research_brain export">\n'
            f'<title>{_e(title)}</title>\n'
            f'<link rel="stylesheet" href="{prefix}assets/fonts/fonts.css">\n'
            f'<link rel="stylesheet" href="{prefix}assets/katex/katex.min.css">\n'
            f'<link rel="stylesheet" href="{prefix}assets/export.css">\n'
            f'</head>\n<body>\n<div class="sheet">\n')


def _foot(prefix: str, data: dict) -> str:
    return ('</div>\n'
            f'<script type="application/json" id="rb-data">{_json_script(data)}</script>\n'
            f'<script src="{prefix}assets/katex/katex.min.js"></script>\n'
            f'<script src="{prefix}assets/marked.min.js"></script>\n'
            f'<script src="{prefix}assets/latex-editor.js"></script>\n'
            f'<script src="{prefix}assets/export.js"></script>\n'
            '</body>\n</html>\n')


def _md(key: str, text: str, cls: str = "", inline: bool = False) -> str:
    # The raw text is shown as-is until the renderer replaces it (and stays if JS is off).
    classes = " ".join(c for c in ("md", "raw", cls) if c)
    return (f'<div class="{classes}" data-md="{_e(key)}"{" data-inline" if inline else ""}>'
            f'{_e(text)}</div>')


def _authors(a: list[str], n: int = 6) -> str:
    return ", ".join(a[:n]) + (" et al." if len(a) > n else "")


def _kind(n: dict) -> str:
    t = n["type"]
    return f'<div class="kind"><span class="dot {t}"></span>{_e(t.capitalize())}</div>'


def _note_page(n: dict, frags: list[dict], where: dict[str, str], titles: dict[str, str], resolve,
               list_title: str, has_pdf: bool) -> str:
    fields: dict[str, str] = {}
    parts = [_head(f"{n['title']} ({list_title})", "../"),
             f'<div class="top"><a href="../index.html">{_e(list_title)}</a><span class="sp"></span></div>',
             _kind(n), f"<h1>{_e(n['title'])}</h1>"]
    meta = []
    if n["authors"]:
        meta.append(f"<span>{_e(_authors(n['authors']))}</span>")
    if n["venue"]:
        meta.append(f"<span>{_e(n['venue'])}</span>")
    if n["year"]:
        meta.append(f"<span>{_e(n['year'])}</span>")
    if n["doi"]:
        meta.append(f'<span>DOI <a class="ident" href="https://doi.org/{_e(n["doi"])}">{_e(n["doi"])}</a></span>')
    if n["arxiv"]:
        meta.append(f'<span>arXiv <a class="ident" href="https://arxiv.org/abs/{_e(n["arxiv"])}">{_e(n["arxiv"])}</a></span>')
    if n["url"] and re.match(r"^https?://", n["url"], re.I):
        meta.append(f'<span><a href="{_e(n["url"])}">Source page</a></span>')
    if has_pdf:
        meta.append(f'<span><a href="../pdfs/{_e(n["id"])}.pdf">PDF</a></span>')
    if n["type"] == "fragment" and n["locator"]:
        meta.append(f"<span>{_e(n['locator'])}</span>")
    if meta:
        parts.append(f'<div class="meta">{"".join(meta)}</div>')
    if n["type"] == "fragment" and n["quote"].strip():
        fields["quote"] = n["quote"]
        parts.append(f'<section><h2>Quote</h2>{_md("quote", n["quote"], "mine quote")}</section>')
    if n["summary"].strip():
        fields["summary"] = n["summary"]
        parts.append(f'<section><h2>Summary</h2>{_md("summary", n["summary"], "mine")}</section>')
    if n["abstract"].strip():
        fields["abstract"] = n["abstract"]
        parts.append(f'<section><h2>Abstract</h2>{_md("abstract", n["abstract"])}</section>')
    if n["body"].strip():
        fields["body"] = n["body"]
        parts.append(f'<section><h2>Notes</h2>{_md("body", n["body"])}</section>')
    if frags:
        rows = []
        for f in frags:
            loc = f["locator"] or (f"p. {f['page']}" if f["page"] else "")
            row = [f'<div class="frag" id="{_e(f["id"])}">']
            if loc:
                row.append(f'<div class="loc">{_e(loc)}</div>')
            if f["quote"].strip():
                fields[f"q:{f['id']}"] = f["quote"]
                row.append(_md(f"q:{f['id']}", f["quote"], "mine quote"))
            if f["body"].strip():
                fields[f"b:{f['id']}"] = f["body"]
                row.append(_md(f"b:{f['id']}", f["body"], "note"))
            if not f["quote"].strip() and not f["body"].strip():
                row.append(f'<div class="md">{_e(f["title"])}</div>')
            row.append("</div>")
            rows.append("".join(row))
        parts.append(f'<section><h2>Fragments</h2>{"".join(rows)}</section>')
    linked = [j for j in n["links"] if j in where and j != n["id"]]
    if n["type"] == "fragment" and n["parent"] in where:
        linked = [n["parent"]] + [j for j in linked if j != n["parent"]]
    if linked:
        items = "".join(f'<li><a class="t" href="{_e(_href(j, where, False))}">{_e(titles[j])}</a></li>' for j in linked)
        parts.append(f'<section><h2>Linked notes</h2><ul class="list">{items}</ul></section>')
    texts = [n.get(k, "") for k in WIKI_FIELDS] + [t for f in frags for t in (f["quote"], f["body"])]
    links = _links_for(texts, resolve, where, titles)
    parts.append(_foot("../", {"fields": fields, "links": links}))
    return "\n".join(parts)


def _index_page(pages: list[dict], frag_count: int, list_title: str, created: str) -> str:
    fields: dict[str, str] = {}
    items = []
    for n in pages:
        sub = []
        if n["type"] != "paper":
            sub.append(f"<span>{_e(n['type'].capitalize())}</span>")
        if n["authors"]:
            sub.append(f"<span>{_e(_authors(n['authors'], 3))}</span>")
        if n["year"]:
            sub.append(f"<span>{_e(n['year'])}</span>")
        summary = n["summary"] if n["type"] != "fragment" else (n["quote"] or n["body"])
        sum_html = ""
        if summary.strip():
            key = f"s:{n['id']}"
            fields[key] = summary
            sum_html = _md(key, summary, "sum")
        items.append(f'<li><a class="t" href="notes/{_e(n["id"])}.html">{_e(n["title"])}</a>'
                     f'{"<div class=s>" + "".join(sub) + "</div>" if sub else ""}{sum_html}</li>')
    papers = sum(1 for n in pages if n["type"] == "paper")
    counts = [f"{papers} paper{'s' if papers != 1 else ''}" if papers else "",
              f"{len(pages) - papers} note{'s' if len(pages) - papers != 1 else ''}" if len(pages) - papers else "",
              f"{frag_count} fragment{'s' if frag_count != 1 else ''}" if frag_count else ""]
    lede = ", ".join(c for c in counts if c) + f". Exported {created}."
    return "\n".join([_head(list_title, ""), f"<h1>{_e(list_title)}</h1>", f'<p class="lede">{_e(lede)}</p>',
                      f'<ul class="list">{"".join(items)}</ul>', _foot("", {"fields": fields, "links": {}})])


def _copy_assets(static_dir: Path, dest: Path) -> None:
    for src, dst in ASSETS:
        s, d = static_dir / src, dest / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("LICENSE*"))
        else:
            shutil.copy2(s, d)
    (dest / "export.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (dest / "export.js").write_text(RENDER_JS, encoding="utf-8")


def export_site(store, ids: list[str] | None = None, include_pdfs: bool = False, *, static_dir: Path,
                title: str = "Reading list", now: dt.datetime | None = None, include_private: bool = False) -> dict:
    notes = store.all()
    skipped = 0
    if not include_private:  # private notes (app/privacy.py) stay out of anything shared, unless asked for
        from . import privacy
        priv = privacy.private_ids() if privacy.configured() else {n["id"] for n in notes if privacy.is_private(n)}
        if ids is not None:
            missing = [i for i in ids if i not in {n["id"] for n in notes}]
            if missing:
                raise KeyError(missing[0])
            skipped = len([i for i in dict.fromkeys(ids) if i in priv])
            ids = [i for i in ids if i not in priv]
            if not ids:
                raise ExportError("Everything you picked is private. Private notes are left out of exports unless "
                                  "you choose to include them.")
        else:
            skipped = len([n for n in notes if n["id"] in priv and n["type"] != "fragment"])
        # private notes are gone entirely: no page, no fragment row, no title in a link
        notes = [n for n in notes if n["id"] not in priv]
        if priv and privacy.configured():  # [[Private idea]] written in a shared note -> "a private note"
            notes = [{**n, **{k: privacy.scrub(n.get(k) or "") for k in WIKI_FIELDS}} for n in notes]
    pages, frags = select(notes, ids)
    by_id = {n["id"]: n for n in notes}
    where: dict[str, str] = {n["id"]: n["id"] for n in pages}
    for parent, lst in frags.items():
        for f in lst:
            where[f["id"]] = parent
    titles = {i: by_id[i]["title"] for i in where}
    resolve = _resolver(notes)

    root = store.root / "exports"
    name = _stamp(now)
    out = root / name
    k = 1
    while out.exists():
        k += 1
        out = root / f"{name}-{k}"
    tmp = root / f".{out.name}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "notes").mkdir(parents=True)
    try:
        _copy_assets(Path(static_dir), tmp / "assets")
        pdfs = 0
        for n in pages:
            has_pdf = False
            if include_pdfs and n["type"] == "paper":
                src = store.pdf_path(n["id"])
                if src.exists():
                    (tmp / "pdfs").mkdir(exist_ok=True)
                    shutil.copy2(src, tmp / "pdfs" / f"{n['id']}.pdf")
                    has_pdf, pdfs = True, pdfs + 1
            page = _note_page(n, frags.get(n["id"], []), where, titles, resolve, title, has_pdf)
            (tmp / "notes" / f"{n['id']}.html").write_text(page, encoding="utf-8")
        when = now or dt.datetime.now()
        created = f"{when.day} {when:%B %Y}"
        frag_count = sum(len(v) for v in frags.values())
        (tmp / "index.html").write_text(_index_page(pages, frag_count, title, created), encoding="utf-8")
        tmp.rename(out)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"path": str(out), "name": out.name, "notes": len(pages), "fragments": frag_count, "pdfs": pdfs,
            "index": str(out / "index.html"), "private_skipped": skipped}
