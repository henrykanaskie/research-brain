// research_brain extension: "Save to research_brain (link + text)", the page side.
// No AI in this extension.
//
// Injected into the tab you are on, only when you click the button in the popup (activeTab + scripting).
// It reads the article you are looking at, in this page as your browser shows it, and returns:
//   - citation metadata from the page's own tags: Highwire citation_* (Google Scholar's), Dublin Core
//     (DC.* / dcterms.*), PRISM, JSON-LD (schema.org ScholarlyArticle / Article) and Open Graph;
//   - the article's main text, each heading turned into a [[section <heading>]] marker so research_brain
//     can cite "§3.2"; math kept as $TeX$ where the page carries it (MathML alttext, KaTeX, MathJax 2);
//   - the reference list, separately (research_brain links it to papers already in your library).
// It never fetches anything, never reads cookies or storage, and sends nothing itself: the popup posts the
// result to research_brain on this computer.
(() => {
  const MAX_CHARS = 5_000_000;
  const ROOT_MIN = 1500;  // paragraph text that makes a known publisher container "the article"
  const BODY_MIN = 400;   // less main text than this besides the abstract = an abstract-only landing page

  const clean = (s) => String(s == null ? "" : s).replace(/[­​-‍⁠﻿]/g, "").replace(/\s+/g, " ").trim();
  const lc = (s) => String(s || "").toLowerCase();

  /* ── metadata ──────────────────────────────────────────── */
  function metaIndex(doc) {
    const idx = new Map();
    for (const m of doc.querySelectorAll("meta[name], meta[property]")) {
      const k = lc(m.getAttribute("name") || m.getAttribute("property"));
      const v = clean(m.getAttribute("content"));
      if (!k || !v) continue;
      if (!idx.has(k)) idx.set(k, []);
      idx.get(k).push(v);
    }
    return idx;
  }
  const first = (idx, names) => { for (const n of names) { const v = idx.get(n); if (v && v.length) return v[0]; } return ""; };
  const all = (idx, names) => { for (const n of names) { const v = idx.get(n); if (v && v.length) return v; } return []; };

  function jsonLd(doc) {
    const out = [];
    const visit = (o) => {
      if (!o || typeof o !== "object") return;
      if (Array.isArray(o)) { o.forEach(visit); return; }
      out.push(o);
      if (o["@graph"]) visit(o["@graph"]);
    };
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      try { visit(JSON.parse(s.textContent || "")); } catch { /* not JSON */ }
    }
    const types = (o) => [].concat(o["@type"] || []).map(lc);
    const rank = (o) => {
      const t = types(o);
      if (t.some((x) => /scholarlyarticle|medicalscholarlyarticle/.test(x))) return 3;
      if (t.some((x) => /^(article|techarticle|report|chapter|thesis)$/.test(x))) return 2;
      if (t.some((x) => /article/.test(x))) return 1;
      return 0;
    };
    let best = null;
    for (const o of out) if (rank(o) > 0 && (!best || rank(o) > rank(best))) best = o;
    return best || {};
  }
  const ldText = (v) => (typeof v === "string" ? clean(v) : v && typeof v === "object" ? clean(v.name || v["@value"] || "") : "");
  function ldAuthors(ld) {
    return [].concat(ld.author || ld.creator || []).map((a) => {
      if (typeof a === "string") return clean(a);
      if (!a || typeof a !== "object") return "";
      return clean(a.name || [a.givenName, a.familyName].filter(Boolean).join(" "));
    }).filter(Boolean);
  }

  const DOI_RE = /\b(10\.\d{4,9}\/[^\s"<>{}]+)/i;
  function doiOf(s) {
    const m = DOI_RE.exec(decodeURIComponentSafe(String(s || "")));
    return m ? m[1].replace(/[.,;)\]]+$/, "") : "";
  }
  function decodeURIComponentSafe(s) { try { return decodeURIComponent(s); } catch { return s; } }

  /** "Vaswani, Ashish" -> "Ashish Vaswani" (citation_author is often Last, First) */
  function personName(a) {
    a = clean(a);
    const parts = a.split(",");
    if (parts.length === 2 && parts[0].trim() && parts[1].trim() && !/\b(jr|sr|ii|iii|iv)\.?$/i.test(parts[1].trim())) {
      return `${parts[1].trim()} ${parts[0].trim()}`;
    }
    return a;
  }

  function absUrl(u, base) {
    if (!u) return "";
    try { const x = new URL(u, base); return /^https?:$/.test(x.protocol) ? x.href : ""; } catch { return ""; }
  }

  function metadata(doc, href) {
    const m = metaIndex(doc);
    const ld = jsonLd(doc);
    const title = first(m, ["citation_title", "dc.title", "dcterms.title", "prism.title", "eprints.title"])
      || ldText(ld.headline) || ldText(ld.name) || first(m, ["og:title", "twitter:title"])
      || clean((doc.querySelector("h1") || {}).textContent) || clean(doc.title);
    let authors = all(m, ["citation_author", "dc.creator", "dcterms.creator", "eprints.creators_name"]).map(personName);
    if (!authors.length) authors = ldAuthors(ld);
    let doi = "";
    for (const v of [first(m, ["citation_doi", "prism.doi", "bepress_citation_doi"]),
      ...all(m, ["dc.identifier", "dcterms.identifier", "citation_id"]),
      ldText(ld.doi), ...[].concat(ld.identifier || []).map((x) => (typeof x === "string" ? x : x && (x.value || x["@value"] || x.propertyID === "DOI" && x.value) || "")),
      ...[].concat(ld.sameAs || []), ...[].concat(ld["@id"] || []),
      (doc.querySelector('link[rel="canonical"]') || {}).href || "", href]) {
      doi = doiOf(v);
      if (doi) break;
    }
    let arxiv = first(m, ["citation_arxiv_id"]);
    const ax = /arxiv\.org\/(?:abs|pdf|html)\/([^?#\s]+?)(?:v\d+)?(?:\.pdf)?(?:[?#/]|$)/i.exec(href || "");
    if (!arxiv && ax) arxiv = ax[1];
    const date = first(m, ["citation_publication_date", "citation_date", "citation_online_date", "citation_cover_date",
      "citation_year", "prism.publicationdate", "dc.date", "dcterms.issued", "dcterms.date", "article:published_time"])
      || ldText(ld.datePublished) || ldText(ld.dateCreated);
    const ym = /\b(1[5-9]\d\d|2[01]\d\d)\b/.exec(date || "");
    const isPartOf = [].concat(ld.isPartOf || [])[0];
    const venue = first(m, ["citation_journal_title", "citation_conference_title", "citation_inbook_title",
      "prism.publicationname", "citation_book_title", "dc.source", "dc.relation.ispartof"])
      || (isPartOf && (ldText(isPartOf) || ldText(isPartOf.isPartOf))) || first(m, ["citation_publisher", "dc.publisher"]);
    const canon = absUrl((doc.querySelector('link[rel="canonical"]') || {}).getAttribute?.("href") || "", href);
    const og = absUrl(first(m, ["og:url"]), href);
    const here = absUrl(href, href).split("#")[0];
    // the canonical address when it is on the same site (some sites point canonical at a CDN or a hub page)
    const sameSite = (u) => { try { return new URL(u).hostname.replace(/^www\./, "") === new URL(here).hostname.replace(/^www\./, ""); } catch { return false; } };
    const url = (canon && sameSite(canon) ? canon : og && sameSite(og) ? og : here) || here;
    const pdf = absUrl(first(m, ["citation_pdf_url", "eprints.document_url"]), href);
    let abstract = first(m, ["citation_abstract", "dcterms.abstract", "dc.description.abstract"]) || ldText(ld.abstract);
    const domAbs = abstractFromDom(doc);
    if (domAbs && domAbs.length > abstract.length) abstract = domAbs;
    if (!abstract) {
      const d = first(m, ["dc.description", "description", "og:description"]) || ldText(ld.description);
      if (d.length > 300) abstract = d;  // a short description is usually site copy, not an abstract
    }
    return { title: title.slice(0, 500), authors: authors.slice(0, 300), doi, arxiv, year: ym ? +ym[1] : null,
             venue: venue.slice(0, 300), abstract: abstract.replace(/^abstract[:.\s]*/i, "").slice(0, 20000), url, pdf_url: pdf };
  }

  const ABSTRACT_SEL = ["blockquote.abstract", "section.abstract", "div.abstract", "#abstract", "#abstracts",
    "#Abs1-content", "section[data-title='Abstract' i]", ".c-article-section__content[id^='Abs']", ".abstract-content",
    ".article-section__abstract", "[role='doc-abstract']", ".ltx_abstract", ".abstractSection", ".hlFld-Abstract",
    "div[class*='abstract' i]", "section[id*='abstract' i]"];
  function abstractFromDom(doc) {
    for (const sel of ABSTRACT_SEL) {
      let el = null;
      try { el = doc.querySelector(sel); } catch { continue; }
      if (!el) continue;
      const t = clean(blockText(el).replace(/\[\[section [^\]]*\]\]/g, " ")).replace(/^abstract[:.\s]*/i, "");
      if (t.length > 80) return t;
    }
    return "";
  }

  /* ── main text ─────────────────────────────────────────── */
  // Publisher article containers, best first (checked before the generic readability-style fallback).
  const ROOTS = [
    ".ltx_page_content article", "article.ltx_document", ".ltx_document",          // arXiv HTML (LaTeXML), ar5iv
    ".c-article-body", "div.main-content[data-article-body]",                       // Springer Nature, BMC
    "#bodymatter", ".article__body", ".article-body", "#article-body",               // Wiley, ACS, AIP, IOP, APS
    ".hlFld-Fulltext", ".NLM_sec_level_1",                                          // Taylor & Francis, ACS
    "#body", "div.Body", "#centerInner .Body",                                      // ScienceDirect (Elsevier)
    ".html-body",                                                                   // MDPI
    "#mc .jig-ncbiinpagenav", "main article .tsec", "section.body.main-article-body", // PubMed Central
    "#article-container .article-content", ".fulltext-view", "#article",            // IEEE Xplore, generic
    ".article-full-text", ".fulltext", "#fulltext", ".article-text", "#content-inner article",
    "article", "[role='main']", "main",
  ];
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "NAV", "FOOTER", "ASIDE", "FORM", "BUTTON", "SELECT",
    "INPUT", "TEXTAREA", "SVG", "CANVAS", "IFRAME", "OBJECT", "EMBED", "VIDEO", "AUDIO", "IMG", "PICTURE", "TEMPLATE",
    "DIALOG", "MENU", "LINK", "META", "HEAD"]);
  const SKIP_RE = /(^|[\s_-])(nav|navbar|navigation|menu|breadcrumbs?|share|sharing|social|toolbar|cookies?|banner|advert|ads|promo|related|recommended|recommendations|sidebar|metrics|altmetric|article-tools|skip|print-only|visually-hidden|sr-only|u-visually-hidden|modal|popup|tooltip|access-options|signin|login|subscribe|newsletter|comments?|ltx_page_footer|ltx_page_header|ltx_role_footnote|author-info|authors?|byline|ltx_authors|contributors|affiliations?|c-article-header|article-header|issue-info|figure-viewer|downloads?)([\s_-]|$)/i;
  const BLOCK = new Set(["P", "DIV", "SECTION", "ARTICLE", "LI", "UL", "OL", "DL", "DT", "DD", "BLOCKQUOTE", "PRE",
    "FIGURE", "FIGCAPTION", "TABLE", "TR", "HR", "MAIN", "HEADER", "ADDRESS", "DETAILS", "SUMMARY", "CAPTION", "BR"]);
  const HEAD_RE = /^H[1-6]$/;
  const REF_HEAD_RE = /^(\d+\.?\s*|[IVX]+\.\s*)?(references?( and notes)?|bibliography|literature cited|works cited|cited literature)$/i;
  const REF_BOX_RE = /(^|[\s_-])(references?|ref-list|reflist|bibliography|ltx_bibliography|citation-list|c-article-references|backmatter-references)([\s_-]|$)/i;

  const attrs = (el) => `${el.id || ""} ${typeof el.className === "string" ? el.className : (el.getAttribute && el.getAttribute("class")) || ""}`;
  function hidden(el) {
    if (el.hidden || el.getAttribute("aria-hidden") === "true") return true;
    if (typeof el.checkVisibility === "function") {
      try {
        if (el.checkVisibility()) return false;
        const cs = el.ownerDocument.defaultView && el.ownerDocument.defaultView.getComputedStyle(el);
        return !(cs && cs.display === "contents");  // display: contents has no box but shows its children
      } catch { return false; }
    }
    return false;
  }
  function headLevel(el) {
    if (HEAD_RE.test(el.tagName)) return +el.tagName[1];
    if (el.getAttribute && el.getAttribute("role") === "heading") return +(el.getAttribute("aria-level") || 2);
    return 0;
  }

  /** TeX for a math element, or "" */
  function tex(el) {
    const tag = el.tagName.toUpperCase();
    if (tag === "MATH") {
      const alt = el.getAttribute("alttext") || "";
      const ann = el.querySelector('annotation[encoding="application/x-tex"], annotation[encoding="TeX"]');
      const t = clean(alt || (ann && ann.textContent) || "");
      if (t) return el.getAttribute("display") === "block" ? `$$${t}$$` : `$${t}$`;
      return clean(el.textContent);
    }
    if (el.classList && el.classList.contains("katex")) {
      const ann = el.querySelector('annotation[encoding="application/x-tex"]');
      return ann ? `$${clean(ann.textContent)}$` : "";
    }
    if (tag === "SCRIPT" && /^math\/tex/i.test(el.type || "")) {
      const t = clean(el.textContent);
      return t ? (/mode=display/i.test(el.type) ? `$$${t}$$` : `$${t}$`) : "";
    }
    if (tag === "MJX-CONTAINER") {
      const m = el.querySelector("mjx-assistive-mml math, math");
      return m ? tex(m) : "";
    }
    return null;  // not math
  }

  /** Walk one container: paragraphs, [[section …]] markers for headings, references kept apart. */
  function walkText(root, opts = {}) {
    const lines = [];
    const refs = [];
    let buf = "";
    let refLevel = 0;  // >0: inside a "References" section opened by a heading of this level
    const skipTitle = clean(opts.title || "").toLowerCase();
    const flush = () => {
      const p = clean(buf);
      buf = "";
      if (!p) return;
      if (refLevel) { refs.push(p); return; }
      if (lines[lines.length - 1] !== p) lines.push(p);
    };
    const visit = (node, top) => {
      if (node.nodeType === 3) { buf += node.nodeValue; return; }
      if (node.nodeType !== 1) return;
      const el = node;
      const tag = el.tagName.toUpperCase();
      const t = tex(el);
      if (t !== null) { buf += ` ${t} `; return; }
      if (tag === "SCRIPT" || tag === "STYLE") return;
      if (el.classList && (el.classList.contains("MathJax") || el.classList.contains("MathJax_Preview")
        || el.classList.contains("MathJax_Display") || el.classList.contains("MathJax_SVG")
        || el.classList.contains("katex-html"))) return;  // rendered copies of math kept as TeX elsewhere
      if (SKIP_TAGS.has(tag)) return;
      if (!top && (hidden(el) || SKIP_RE.test(attrs(el)))) return;
      const lvl = headLevel(el);
      if (lvl) {
        flush();
        const h = clean(inlineText(el)).replace(/\s*[#¶§]+$/, "").replace(/^(link to|permalink)\s+/i, "").slice(0, 200);
        if (!h) return;
        if (refLevel && lvl <= refLevel) refLevel = 0;
        if (REF_HEAD_RE.test(h)) { refLevel = lvl; return; }
        if (refLevel) return;
        if (lvl === 1 && skipTitle && h.toLowerCase() === skipTitle) return;  // the article title itself
        lines.push(`[[section ${h.replace(/[\[\]]/g, "")}]]`);
        return;
      }
      if (!top && !refLevel && (REF_BOX_RE.test(attrs(el)) || el.getAttribute("role") === "doc-bibliography")) {
        flush();
        const items = el.querySelectorAll("li, .ltx_bibitem, .c-article-references__item, p.ref, div.ref");
        for (const it of (items.length ? items : [el])) { const r = clean(inlineText(it)); if (r) refs.push(r.slice(0, 1000)); }
        return;
      }
      if (tag === "FIGURE" || /(^|[\s_-])(table-wrap|fig|figure)([\s_-]|$)/i.test(attrs(el)) && !top) {
        flush();
        const cap = el.querySelector("figcaption, .caption, caption");
        if (cap) { buf = inlineText(cap); flush(); }
        return;
      }
      if (tag === "TABLE" && (el.querySelector("th, thead") || /ltx_tabular|tbl/i.test(attrs(el)))) {
        flush();
        const cap = el.querySelector("caption");
        if (cap) { buf = inlineText(cap); flush(); }
        return;
      }
      const block = BLOCK.has(tag);
      if (block) flush();
      if (tag === "PRE") { buf += el.textContent; flush(); return; }
      for (const c of el.childNodes) visit(c, false);
      if (block) flush();
      else if (tag === "TD" || tag === "TH") buf += " ";
    };
    visit(root, true);
    flush();
    return { lines, refs };
  }

  /** The text of an element on one line, math as $TeX$. */
  function inlineText(el) {
    let s = "";
    const visit = (n) => {
      if (n.nodeType === 3) { s += n.nodeValue; return; }
      if (n.nodeType !== 1) return;
      const t = tex(n);
      if (t !== null) { s += ` ${t} `; return; }
      const tag = n.tagName.toUpperCase();
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "BUTTON" || tag === "SVG") return;
      if (n.classList && (n.classList.contains("MathJax") || n.classList.contains("MathJax_Preview")
        || n.classList.contains("katex-html") || n.classList.contains("visually-hidden") || n.classList.contains("u-visually-hidden"))) return;
      for (const c of n.childNodes) visit(c);
      if (BLOCK.has(tag)) s += " ";
    };
    visit(el);
    return s;
  }
  function blockText(el) { return walkText(el).lines.join("\n\n"); }

  function paraChars(el) {
    let n = 0;
    for (const p of el.querySelectorAll("p")) n += clean(p.textContent).length;
    return n;
  }

  /** The article's main container: a known publisher container, else the element holding the most paragraph text. */
  function pickRoot(doc) {
    let best = null, bestN = 0;
    for (const sel of ROOTS) {
      let els;
      try { els = doc.querySelectorAll(sel); } catch { continue; }
      for (const el of els) {
        const n = paraChars(el);
        if (n > bestN) { best = el; bestN = n; }
      }
      if (best && bestN >= ROOT_MIN) return best;
    }
    // readability-style: score each paragraph's parent (and half to the grandparent) by its text
    const score = new Map();
    for (const p of doc.querySelectorAll("p")) {
      const len = clean(p.textContent).length;
      if (len < 60) continue;
      const a = p.parentElement, b = a && a.parentElement;
      if (a) score.set(a, (score.get(a) || 0) + len);
      if (b) score.set(b, (score.get(b) || 0) + len / 2);
    }
    let top = null, topN = 0;
    for (const [el, n] of score) if (n > topN && !SKIP_RE.test(attrs(el))) { top = el; topN = n; }
    return topN > bestN ? top : best || top || doc.body;
  }

  function run(doc = document, href = location.href) {
    const meta = metadata(doc, href);
    const root = pickRoot(doc);
    const { lines, refs } = root ? walkText(root, { title: meta.title }) : { lines: [], refs: [] };
    // a trailing heading with nothing under it says nothing
    const out = lines.slice();
    while (out.length && out[out.length - 1].startsWith("[[section ")) out.pop();
    let body = out.join("\n\n");
    const bodyChars = out.filter((l) => !l.startsWith("[[section ")).join(" ").length;
    const hasAbs = meta.abstract && body.replace(/\s+/g, " ").includes(meta.abstract.slice(0, 80).replace(/\s+/g, " "));
    const landing = /arxiv\.org\/abs\//i.test(href || "");  // arXiv's abstract page; the full text is its HTML or PDF
    let partial = false;
    if (meta.abstract && (landing || bodyChars - (hasAbs ? meta.abstract.length : 0) < BODY_MIN)) {
      body = `[[section Abstract]]\n\n${meta.abstract}`;  // a landing page: only the abstract is here
      partial = true;
    } else if (meta.abstract && !hasAbs) {
      body = `[[section Abstract]]\n\n${meta.abstract}\n\n${body}`;
    }
    body = body.replace(/\$ ([,.;:)])/g, "$$$1");  // "$x$ ," -> "$x$,"
    if (body.length > MAX_CHARS) body = body.slice(0, MAX_CHARS);
    return { ...meta, text: body, references: refs.slice(0, 3000), kind: "html", partial, chars: body.length };
  }

  window.__rbExtract = { run, metadata, pickRoot, walkText, personName };
})();
