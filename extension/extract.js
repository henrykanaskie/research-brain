// research_brain extension: "Save to research_brain (link + text)", the page side.
// No AI in this extension.
//
// Injected into the tab you are on, only when you click the button in the popup (activeTab + scripting).
// It reads the article you are looking at, in this page as your browser shows it, and returns:
//   - citation metadata from the page's own tags: Highwire citation_* (Google Scholar's), Dublin Core
//     (DC.* / dcterms.*), PRISM, JSON-LD (schema.org ScholarlyArticle / Article) and Open Graph, plus
//     publisher-specific places: IEEE Xplore's `xplGlobal.document.metadata` (read from the inline script's
//     text: the page's JS globals aren't visible to an extension), ScienceDirect's header markup, DOI links in
//     an article header, data-doi attributes and per-publisher URL patterns (/doi/10..., /article/10...,
//     journals.aps.org/<j>/abstract/10.1103/...). Identifiers: DOI, arXiv id, PMID, ISBN, OSTI and NTRS ids;
//     bibliographic details: volume, issue, pages / article number, ISSN, publisher, journal abbreviation,
//     conference, and a CSL type the page implies (genre). research_brain then asks Crossref & co. for the
//     authoritative record (app/metadata.py); what the page says only fills gaps;
//   - the article's main text, each heading turned into a [[section <heading>]] marker so research_brain
//     can cite "§3.2"; math kept as $TeX$ where the page carries it (MathML alttext / TeX annotation, KaTeX,
//     MathJax 2 script tags incl. math/mml, MathJax 3 mjx-container with assistive MathML or data-latex);
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
    const sfx = /^(jr|sr|ii|iii|iv)\.?$/i;
    const p3 = a.split(",").map((x) => x.trim());
    if (p3.length === 3 && p3[0] && p3[1] && sfx.test(p3[2])) return `${p3[1]} ${p3[0]} ${p3[2]}`;  // "King, Martin, Jr."
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

  /** IEEE Xplore: `xplGlobal.document.metadata = {...};` in an inline script (parsed from its text). */
  function ieeeMeta(doc) {
    for (const sc of doc.querySelectorAll("script:not([src])")) {
      const t = sc.textContent || "";
      const at = t.indexOf("xplGlobal.document.metadata");
      if (at < 0) continue;
      const open = t.indexOf("{", at);
      if (open < 0) continue;
      let depth = 0, inStr = false, esc = false, end = -1;
      for (let i = open; i < t.length && i < open + 2_000_000; i++) {  // string-aware brace matching
        const c = t[i];
        if (inStr) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === '"') inStr = false; continue; }
        if (c === '"') inStr = true;
        else if (c === "{") depth++;
        else if (c === "}" && --depth === 0) { end = i; break; }
      }
      if (end < 0) continue;
      try { const o = JSON.parse(t.slice(open, end + 1)); if (o && typeof o === "object") return o; } catch { /* not JSON */ }
    }
    return null;
  }

  /** ScienceDirect's article header (it has few citation_* tags). */
  function sciencedirectMeta(doc) {
    if (!doc.querySelector("#publication-title, .publication-title, #author-group, .title-text")) return null;
    const authors = [...doc.querySelectorAll("#author-group .react-xocs-alternative-link, #author-group a.author, #author-group .author")]
      .map((a) => {
        const g = clean((a.querySelector(".given-name") || {}).textContent), f = clean((a.querySelector(".surname") || {}).textContent);
        return f ? [g, f].filter(Boolean).join(" ") : "";
      }).filter(Boolean);
    const vol = clean((doc.querySelector(".publication-volume .text-xs, .publication-volume") || {}).textContent);
    return {
      title: clean((doc.querySelector("h1 .title-text, span.title-text") || {}).textContent),
      authors: [...new Set(authors)],
      venue: clean((doc.querySelector(".publication-title-link, #publication-title a, h2.publication-title") || {}).textContent),
      doi: doiOf((doc.querySelector("a.doi, a.anchor.doi") || {}).getAttribute?.("href") || ""),
      volume: (/Volume\s+(\w+)/i.exec(vol) || [])[1] || "", year: (/\b(1[5-9]\d\d|2[01]\d\d)\b/.exec(vol) || [])[1] || "",
      article: (/,\s*(\d{5,7})\s*$/.exec(vol) || [])[1] || "",
    };
  }

  const inRefs = (el) => { for (let e = el; e && e.nodeType === 1; e = e.parentElement) { if (REF_BOX_RE.test(attrs(e)) || e.getAttribute("role") === "doc-bibliography") return true; } return false; };
  // DOIs in a publisher URL (the article's own address)
  const URL_DOI_RES = [
    /journals\.aps\.org\/[a-z]+\/(?:abstract|fulltext|pdf|supplemental|references)\/(10\.1103\/[^?#\s]+)/i,  // APS
    /\/doi\/(?:abs|full|pdf|epdf|epub|book|ref|figure|citedby|reader|pdfdirect|suppl)?\/?(10\.\d{4,9}\/[^?#\s]+)/i,  // Atypon: Wiley, T&F, AIAA, ...
    /\/article\/(?:pii\/)?(10\.\d{4,9}\/[^?#\s]+)/i,                                                      // IOP, Springer
    /\/(?:chapter|book|referenceworkentry)\/(10\.\d{4,9}\/[^?#\s]+)/i,                                     // Springer books
  ];
  function urlDoi(href) {
    const u = decodeURIComponentSafe(String(href || ""));
    for (const re of URL_DOI_RES) {
      const m = re.exec(u);
      if (m) return m[1].replace(/\/(meta|pdf|full|abstract|epdf|references|fulltext|html|figures|metrics|citedby)\/?$/i, "").replace(/[.,;)\]]+$/, "");
    }
    return "";
  }

  function findDoi(doc, href, m, ld, xpl, sd) {
    const metaVals = [first(m, ["citation_doi", "prism.doi", "bepress_citation_doi", "dc.identifier.doi"])];
    for (const el of doc.querySelectorAll('meta[name="dc.identifier" i], meta[name="dcterms.identifier" i], meta[name="citation_id" i]')) {
      const scheme = lc(el.getAttribute("scheme"));
      const v = clean(el.getAttribute("content"));
      if (scheme === "doi" || /^(doi:|https?:\/\/(dx\.)?doi\.org\/|10\.)/i.test(v)) metaVals.push(v);
    }
    const ldIds = [ldText(ld.doi), ...[].concat(ld.identifier || []).map((x) => (typeof x === "string" ? x : x && (x.value || x["@value"]) || "")),
      ...[].concat(ld.sameAs || []), ...[].concat(ld["@id"] || [])];
    const dataDoi = [...doc.querySelectorAll("[data-doi]")].filter((e) => !inRefs(e)).map((e) => e.getAttribute("data-doi"));
    const headerLinks = [...doc.querySelectorAll(
      'a.doi, .doi a, #doi a, .citation__doi a, .epub-section__doilink a, .article-header a[href*="doi.org/10."], header a[href*="doi.org/10."], ' +
      '.c-bibliographic-information__value a[href*="doi.org/"], .article-meta a[href*="doi.org/10."], .doi-link a')]
      .filter((a) => !inRefs(a)).map((a) => a.getAttribute("href") || a.textContent);
    const canon = (doc.querySelector('link[rel="canonical"]') || {}).href || "";
    for (const v of [...metaVals, xpl && xpl.doi, ...ldIds, sd && sd.doi, ...dataDoi, urlDoi(href), urlDoi(canon), ...headerLinks, canon, href]) {
      const d = doiOf(v);
      if (d) return d;
    }
    return "";
  }

  /** The CSL type the page implies, for research_brain's metadata resolver (it uses Crossref's when it has one). */
  function genre(m, xpl) {
    if (first(m, ["citation_conference_title", "citation_conference"]) || (xpl && /conference/i.test(xpl.contentType || ""))) return "paper-conference";
    if (first(m, ["citation_dissertation_institution", "citation_dissertation_name"])) return "thesis";
    if (first(m, ["citation_technical_report_institution", "citation_technical_report_number"])) return "report";
    if (first(m, ["citation_inbook_title"]) || (first(m, ["citation_book_title"]) && first(m, ["citation_firstpage"]))) return "chapter";
    if (first(m, ["citation_isbn"]) && !first(m, ["citation_journal_title"])) return "book";
    if (first(m, ["citation_journal_title"]) || (xpl && /journal|periodical/i.test(xpl.contentType || ""))) return "article-journal";
    return "";
  }

  function metadata(doc, href) {
    const m = metaIndex(doc);
    const ld = jsonLd(doc);
    const xpl = ieeeMeta(doc);
    const sd = sciencedirectMeta(doc);
    const title = first(m, ["citation_title", "dc.title", "dcterms.title", "prism.title", "eprints.title"])
      || (xpl && clean(xpl.title || xpl.displayDocTitle || "")) || (sd && sd.title)
      || ldText(ld.headline) || ldText(ld.name) || first(m, ["og:title", "twitter:title"])
      || clean((doc.querySelector("h1.citation__title, #maincontent h1, h1") || {}).textContent) || clean(doc.title);
    // single-page apps (NASA NTRS) put the site's name in og:title; the record's title is the page's h1
    const siteTitle = /^(ntrs\b|nasa technical reports server)/i.test(title);
    const title2 = siteTitle ? clean((doc.querySelector("h1") || {}).textContent) || title : title;
    let authors = all(m, ["citation_author", "dc.creator", "dcterms.creator", "eprints.creators_name"]).map(personName);
    if (!authors.length && xpl && Array.isArray(xpl.authors)) {
      authors = xpl.authors.map((a) => clean(a && (a.name || [a.firstName, a.lastName].filter(Boolean).join(" ")))).filter(Boolean);
    }
    if (!authors.length && sd) authors = sd.authors;
    if (!authors.length) authors = ldAuthors(ld);
    const doi = findDoi(doc, href, m, ld, xpl, sd);
    let arxiv = first(m, ["citation_arxiv_id"]).replace(/^arxiv:\s*/i, "");
    const ax = /arxiv\.org\/(?:abs|pdf|html)\/([^?#\s]+?)(?:v\d+)?(?:\.pdf)?(?:[?#/]|$)/i.exec(href || "");
    if (!arxiv && ax) arxiv = ax[1];
    const axDoi = /^10\.48550\/arxiv\.(.+)$/i.exec(doi || "");
    if (!arxiv && axDoi) arxiv = axDoi[1];
    const date = first(m, ["citation_publication_date", "citation_date", "citation_online_date", "citation_cover_date",
      "citation_year", "prism.publicationdate", "dc.date", "dcterms.issued", "dcterms.date", "article:published_time"])
      || (xpl && clean(xpl.publicationDate || xpl.publicationYear || "")) || ldText(ld.datePublished) || ldText(ld.dateCreated)
      || (sd && sd.year) || "";
    const ym = /\b(1[5-9]\d\d|2[01]\d\d)\b/.exec(date || "");
    const isPartOf = [].concat(ld.isPartOf || [])[0];
    const venue = first(m, ["citation_journal_title", "citation_conference_title", "citation_inbook_title",
      "prism.publicationname", "citation_book_title", "dc.source", "dc.relation.ispartof"])
      || (xpl && clean(xpl.displayPublicationTitle || xpl.publicationTitle || "")) || (sd && sd.venue)
      || (isPartOf && (ldText(isPartOf) || ldText(isPartOf.isPartOf)))
      || first(m, ["citation_dissertation_institution", "citation_technical_report_institution", "citation_publisher", "dc.publisher"]);
    const canon = absUrl((doc.querySelector('link[rel="canonical"]') || {}).getAttribute?.("href") || "", href);
    const og = absUrl(first(m, ["og:url"]), href);
    const here = absUrl(href, href).split("#")[0];
    // the canonical address when it is on the same site (some sites point canonical at a CDN or a hub page)
    const sameSite = (u) => { try { return new URL(u).hostname.replace(/^www\./, "") === new URL(here).hostname.replace(/^www\./, ""); } catch { return false; } };
    const url = (canon && sameSite(canon) ? canon : og && sameSite(og) ? og : here) || here;
    const pdfLink = doc.querySelector("a.article-pdfLink, a#pdf-link");
    const pdf = absUrl(first(m, ["citation_pdf_url", "eprints.document_url"]) || (pdfLink && (pdfLink.getAttribute("href") || pdfLink.getAttribute("data-article-url"))) || "", href);
    let abstract = first(m, ["citation_abstract", "dcterms.abstract", "dc.description.abstract"]) || ldText(ld.abstract)
      || (xpl && clean(String(xpl.abstract || "").replace(/<[^>]+>/g, " "))) || "";
    const domAbs = abstractFromDom(doc);
    if (domAbs && domAbs.length > abstract.length) abstract = domAbs;
    if (!abstract) {
      const d = first(m, ["dc.description", "description", "og:description"]) || ldText(ld.description);
      if (d.length > 300) abstract = d;  // a short description is usually site copy, not an abstract
    }
    const fp = first(m, ["citation_firstpage"]) || (xpl && xpl.startPage) || (sd && sd.article) || "";
    const lp = first(m, ["citation_lastpage"]) || (xpl && xpl.endPage) || "";
    const issn = [...new Set([...all(m, ["citation_issn", "prism.issn", "prism.eissn"]),
      ...((xpl && Array.isArray(xpl.issn)) ? xpl.issn.map((x) => clean(x && x.value)) : [])].filter(Boolean))].slice(0, 4);
    const low = decodeURIComponentSafe(href || "").toLowerCase();
    const pick = (re) => (re.exec(low) || [])[1] || "";
    return {
      title: title2.slice(0, 500), authors: authors.slice(0, 300), doi, arxiv, year: ym ? +ym[1] : null,
      venue: venue.slice(0, 300), abstract: abstract.replace(/^abstract[:.\s]*/i, "").slice(0, 20000), url, pdf_url: pdf,
      date: (/^\d{4}([-/]\d{1,2}([-/]\d{1,2})?)?/.exec(date || "") || [""])[0],
      volume: first(m, ["citation_volume", "prism.volume"]) || (xpl && String(xpl.volume || "")) || (sd && sd.volume) || "",
      issue: first(m, ["citation_issue", "prism.number"]) || (xpl && String(xpl.issue || "")) || "",
      pages: fp ? (lp && lp !== fp ? `${fp}-${lp}` : String(fp)) : "",
      issn, publisher: first(m, ["citation_publisher", "dc.publisher", "prism.publisher"]) || (xpl && xpl.publisher) || "",
      journal_abbrev: first(m, ["citation_journal_abbrev"]),
      conference: first(m, ["citation_conference_title", "citation_conference"]),
      genre: genre(m, xpl),
      pmid: first(m, ["citation_pmid"]) || pick(/pubmed\.ncbi\.nlm\.nih\.gov\/(\d{1,9})/),
      isbn: first(m, ["citation_isbn", "prism.isbn"]) || (xpl && Array.isArray(xpl.isbn) && xpl.isbn[0] && clean(xpl.isbn[0].value)) || "",
      osti: first(m, ["citation_osti_id"]) || pick(/osti\.gov\/(?:pages\/)?biblio\/(\d{3,10})/),
      ntrs: pick(/ntrs\.nasa\.gov\/citations\/(\d{6,14})/),
    };
  }

  const ABSTRACT_SEL = ["#abstract-section-content", "div.abstract.author:not(.author-highlights)",          // APS, ScienceDirect
    "div.abstract-text", "div.article-abstract", ".wd-jnl-art-abstract",                                     // IEEE, IOP
    "blockquote.abstract", "section.abstract", "div.abstract:not(.author-highlights):not(.graphical)", "#abstract", "#abstracts",
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
    "section.article-section__full", "div[data-widgetname='ArticleFulltext']", ".widget-ArticleFulltext", // Wiley, AIP (Silverchair)
    "div.article-content", "div[itemprop='articleBody']",                           // IOP
    "#document-body", ".document-text", "#content-container .content-box",          // IEEE Xplore, Cambridge Core
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
    if (tag === "SCRIPT" && /^math\/mml/i.test(el.type || "")) {  // MathJax 2 with MathML input (ScienceDirect)
      try {
        const m = new DOMParser().parseFromString(el.textContent || "", "text/html").querySelector("math");
        return m ? tex(m) : "";
      } catch { return ""; }
    }
    if (tag === "MJX-CONTAINER") {  // MathJax 3
      const lt = el.getAttribute("data-latex") || (el.querySelector("[data-latex]") || { getAttribute: () => "" }).getAttribute("data-latex");
      const display = el.getAttribute("display") === "true" || el.getAttribute("display") === "block";
      if (clean(lt)) return display ? `$$${clean(lt)}$$` : `$${clean(lt)}$`;
      const m = el.querySelector("mjx-assistive-mml math, math");
      if (m) { const t = tex(m); return display && /^\$[^$]/.test(t) ? `$${t}$` : t; }
      return clean(el.getAttribute("aria-label") || "");
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
        || el.classList.contains("MathJax_CHTML") || el.classList.contains("MJX_Assistive_MathML")
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
        refs.push(...refItems(el));
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

  /** The entries of a reference list: the first item selector that matches, each entry on one line, minus the
   *  publishers' link labels (Google Scholar, Crossref, ...). */
  function refItems(el) {
    let items = [];
    for (const sel of [".reference-container", "li", ".ltx_bibitem", ".c-article-references__item", "dd.reference, dd", ".ref-content",
      "p.ref, div.ref", ".reference", ".citation", ".bib-item"]) {
      items = el.querySelectorAll(sel);
      if (items.length) break;
    }
    const out = [];
    for (const it of (items.length ? items : [el])) {
      const r = clean(inlineText(it)).replace(/\s*(Google Scholar|Crossref|CrossRef|Search ADS|PubMed|Web of Science|Scopus|View in Scopus|Find this author)(\s*,?\s*(Google Scholar|Crossref|CrossRef|Search ADS|PubMed|Web of Science|Scopus|View in Scopus))*\s*$/g, "");
      if (r) out.push(r.slice(0, 1000));
    }
    return out;
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
        || n.classList.contains("MathJax_SVG") || n.classList.contains("MathJax_CHTML") || n.classList.contains("MJX_Assistive_MathML")
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

  const REF_CONTAINERS = ["section.bibliography", "section.article-section__references", ".article__references",
    "#references-list", "#references-section-container", "div.ref-list", "ol.references", "ul.references", "dl.references",
    "[role='doc-bibliography']", ".c-article-references", "#references", "section.references"];

  function run(doc = document, href = location.href) {
    const meta = metadata(doc, href);
    const root = pickRoot(doc);
    const { lines, refs } = root ? walkText(root, { title: meta.title }) : { lines: [], refs: [] };
    if (!refs.length) {  // the reference list sits outside the article container (ScienceDirect, Wiley, IOP, Cambridge)
      for (const sel of REF_CONTAINERS) {
        let box = null;
        try { box = doc.querySelector(sel); } catch { continue; }
        if (box && !(root && root.contains(box))) {
          refs.push(...refItems(box));
          if (refs.length) break;
        }
      }
    }
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

  window.__rbExtract = { run, metadata, pickRoot, walkText, personName, urlDoi };
})();
