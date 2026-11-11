"""Paper metadata from identifiers (app/metadata.py): each source's mapping to CSL-JSON (fixtures in
tests/fixtures/metadata/, no network), name/title/number normalisation, the priority and merge rules
(Crossref beats a page scrape, Zotero data is never overwritten, your edits are kept), the cache and the
rate limiter, the SSRF guard (only the allowed API hosts, only public addresses, loopback only for the
translation-server), the background update with its live event, the refresh endpoint and PDF XMP DOIs.

Network access is replaced at the lowest level (app/urlfetch._request / _request_local / resolve), so the
real request path, redirects and host checks are exercised.
"""
from __future__ import annotations

import importlib
import io
import json
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures" / "metadata"


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


# ---------------------------------------------------------------------------------------------- fake network
class FakeResp:
    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None):
        self.status_code, self._body = status, body
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


class FakeNet:
    """Routes exact URLs (or URL prefixes ending in '*') to answers; anything else is a 404. Records every
    request (url, method, headers)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []
        self.local_calls: list[tuple[int, str, bytes | None]] = []
        self.local = None  # (status, body) for the translation-server, or an Exception

    def request(self, url, addr, method, headers, body, timeout):
        self.calls.append((url, method, dict(headers)))
        r = self.routes.get(url)
        if r is None:
            for k, v in self.routes.items():
                if k.endswith("*") and url.startswith(k[:-1]):
                    r = v
                    break
        if r is None:
            return FakeResp(404, b'{"status":"error"}')
        if callable(r):
            r = r(url, headers)
        if isinstance(r, FakeResp):
            return r
        status, payload = r
        return FakeResp(status, payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    def request_local(self, port, path, method, headers, body, timeout):
        self.local_calls.append((port, path, body))
        if isinstance(self.local, Exception):
            raise self.local
        if self.local is None:
            raise ConnectionRefusedError("nothing on that port")
        status, payload = self.local
        return FakeResp(status, payload if isinstance(payload, bytes) else json.dumps(payload).encode())


@pytest.fixture()
def net(monkeypatch):
    from app import urlfetch
    n = FakeNet({})
    monkeypatch.setattr(urlfetch, "resolve", lambda host: ["93.184.216.34"])  # a public address
    monkeypatch.setattr(urlfetch, "_request", n.request)
    monkeypatch.setattr(urlfetch, "_request_local", n.request_local)
    return n


@pytest.fixture()
def resolver(tmp_path):
    from app import metadata as md
    return md.Resolver(md.Cache(tmp_path / "metadata_cache.json"), md.RateLimiter({}, default=0.0),
                       email="me@example.org", ts_port=0)


CR = "https://api.crossref.org/works/"
AX = "https://export.arxiv.org/api/query?id_list="


# ---------------------------------------------------------------------------------------------- source mappings
def test_crossref_aps_article_number_names_and_mathml_title(net, resolver):
    net.routes[CR + "10.1103/physreve.101.063204"] = (200, fx("crossref_aps.json"))
    res = resolver.resolve({"doi": "https://doi.org/10.1103/PhysRevE.101.063204"})
    assert res["source"] == "crossref"
    c = res["csl"]
    assert c["type"] == "article-journal"
    assert c["title"] == "Kinetic simulation of the $E\\times B$ electron drift instability in Hall thrusters"
    assert c["container-title"] == "Physical Review E" and c["container-title-short"] == "Phys. Rev. E"
    assert (c["volume"], c["issue"], c["page"], c["number"]) == ("101", "6", "063204", "063204")
    assert c["issued"] == {"date-parts": [[2020, 6, 8]]}
    assert c["DOI"] == "10.1103/physreve.101.063204" and c["URL"] == "https://doi.org/10.1103/physreve.101.063204"
    assert c["ISSN"] == "2470-0045, 2470-0053" and c["publisher"] == "American Physical Society (APS)"
    assert c["author"] == [
        {"family": "Berg", "given": "Johannes D.", "non-dropping-particle": "van der"},
        {"family": "King", "given": "Martin", "suffix": "Jr."},
        {"family": "Boeuf", "given": "J.-P."}]
    from app.metadata import legacy_from_csl
    leg = legacy_from_csl(c)
    assert leg["authors"] == ["Johannes D. van der Berg", "Martin King Jr.", "J.-P. Boeuf"]
    assert (leg["year"], leg["venue"], leg["doi"]) == (2020, "Physical Review E", "10.1103/physreve.101.063204")
    ua = net.calls[0][2]["User-Agent"]
    assert ua.startswith("research_brain/") and "mailto:me@example.org" in ua  # Crossref's polite pool


def test_crossref_conference_paper_and_corporate_author(net, resolver):
    net.routes[CR + "10.2514/6.2023-1234"] = (200, fx("crossref_aiaa_conf.json"))
    c = resolver.resolve({"doi": "10.2514/6.2023-1234"})["csl"]
    assert c["type"] == "paper-conference" and c["container-title"] == "AIAA SCITECH 2023 Forum"
    assert c["event-title"] == "AIAA SCITECH 2023 Forum" and c["event-place"] == "National Harbor, MD & Online"
    assert c["author"][1] == {"literal": "NASA Glenn Electric Propulsion Team"}
    assert c["abstract"] == "We model wall erosion with ab initio sputtering yields."
    assert c["ISBN"] == "9781624106996"


def test_not_a_crossref_doi_falls_back_to_content_negotiation_datacite(net, resolver):
    def doi_org(url, headers):
        assert headers["Accept"] == "application/vnd.citationstyles.csl+json"
        return FakeResp(302, b"", {"Location": "https://api.datacite.org/dois/10.5281/zenodo.1234567"})
    net.routes["https://doi.org/10.5281/zenodo.1234567"] = doi_org
    net.routes["https://api.datacite.org/dois/10.5281/zenodo.1234567"] = (200, fx("datacite_csl.json"))
    res = resolver.resolve({"doi": "10.5281/ZENODO.1234567"})
    assert res["source"] == "datacite"
    c = res["csl"]
    assert c["type"] == "dataset" and c["publisher"] == "Zenodo" and c["DOI"] == "10.5281/zenodo.1234567"
    assert c["author"] == [{"family": "Hopper", "given": "Grace"}, {"literal": "PESPL Collaboration"}]
    assert [u for u, _, _ in net.calls][0] == CR + "10.5281/zenodo.1234567"  # Crossref was asked first


def test_arxiv_with_journal_doi_prefers_the_published_record_and_keeps_the_arxiv_id(net, resolver):
    net.routes[AX + "2001.01234"] = (200, fx("arxiv_journal.xml"))
    net.routes[CR + "10.1063/5.0012345"] = (200, fx("crossref_pop.json"))
    res = resolver.resolve({"arxiv": "arXiv:2001.01234v3"})
    assert res["source"] == "crossref"
    c = res["csl"]
    assert c["title"] == "Sheath formation at a floating wall: a kinetic study"  # subtitle joined
    assert c["container-title"] == "Physics of Plasmas" and c["page"] == "063505" and c["issued"] == {"date-parts": [[2020, 6]]}
    assert c["custom"]["arxiv"] == "2001.01234"
    assert "Bohm criterion $c_s=\\sqrt{kT_e/m_i}$" in c["abstract"]  # the abstract Crossref lacked, from arXiv
    from app.metadata import legacy_from_csl
    leg = legacy_from_csl(c)
    assert (leg["doi"], leg["arxiv"], leg["venue"]) == ("10.1063/5.0012345", "2001.01234", "Physics of Plasmas")


def test_arxiv_preprint(net, resolver):
    net.routes[AX + "2401.00001"] = (200, fx("arxiv_preprint.xml"))
    res = resolver.resolve({"arxiv": "2401.00001"})
    c = res["csl"]
    assert res["source"] == "arxiv" and c["type"] == "article" and c["genre"] == "Preprint"
    assert (c["number"], c["archive"], c["publisher"]) == ("2401.00001", "arXiv", "arXiv")
    assert c["DOI"] == "10.48550/arxiv.2401.00001" and c["URL"] == "https://arxiv.org/abs/2401.00001"
    assert c["author"][1] == {"family": "Vries", "given": "Hans Peter", "non-dropping-particle": "de"}
    from app.metadata import legacy_from_csl
    leg = legacy_from_csl(c)
    assert (leg["doi"], leg["arxiv"], leg["venue"], leg["year"]) == ("", "2401.00001", "arXiv", 2024)


def test_pubmed(net, resolver):
    net.routes["https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=31415926&retmode=json&tool=research_brain&email=me%40example.org"] = (200, fx("pubmed_esummary.json"))
    res = resolver.resolve({"pmid": "31415926"})
    c = res["csl"]
    assert res["source"] == "pubmed" and c["type"] == "article-journal"
    assert c["title"] == "Cold atmospheric plasma for wound healing: a randomized trial"
    assert c["container-title"] == "Plasma medicine" and c["container-title-short"] == "Plasma Med"
    assert c["author"][:2] == [{"family": "Smith", "given": "J. A."},
                               {"family": "Vries", "given": "H.", "non-dropping-particle": "de"}]
    assert len(c["author"]) == 2  # the collective name is not an author entry here
    assert (c["PMID"], c["PMCID"], c["page"], c["issued"]) == ("31415926", "PMC6789012", "211-230", {"date-parts": [[2019, 11, 12]]})


def test_openlibrary_book(net, resolver):
    net.routes["https://openlibrary.org/api/books?bibkeys=ISBN%3A9780306307515&format=json&jscmd=data"] = (200, fx("openlibrary.json"))
    res = resolver.resolve({"isbn": "978-0-306-30751-5"})
    c = res["csl"]
    assert res["source"] == "openlibrary" and c["type"] == "book"
    assert c["title"] == "Introduction to Plasma Physics and Controlled Fusion: Volume 1: Plasma Physics"
    assert c["author"] == [{"family": "Chen", "given": "Francis F."}]
    assert (c["publisher"], c["publisher-place"], c["ISBN"]) == ("Plenum Press", "New York", "9780306307515, 0306307510")
    from app.metadata import legacy_from_csl
    assert legacy_from_csl(c)["venue"] == "Plenum Press"


def test_osti_and_ntrs(net, resolver):
    net.routes["https://www.osti.gov/api/v1/records/1617000"] = (200, fx("osti.json"))
    net.routes["https://ntrs.nasa.gov/api/citations/20200002395"] = (200, fx("ntrs.json"))
    o = resolver.resolve({}, url="https://www.osti.gov/biblio/1617000")
    assert o["source"] == "osti"
    c = o["csl"]
    assert c["type"] == "report" and c["number"] == "ORNL/TM-2020/1234"
    assert c["author"] == [{"family": "Nominator", "given": "Lois D."},
                           {"family": "Berg", "given": "Johannes", "non-dropping-particle": "van der"}]
    assert c["publisher"].startswith("Oak Ridge National Laboratory") and c["custom"]["osti"] == "1617000"
    n = resolver.resolve({}, url="https://ntrs.nasa.gov/citations/20200002395")
    c = n["csl"]
    assert n["source"] == "ntrs" and c["type"] == "paper-conference"
    assert [a["family"] for a in c["author"]] == ["Kamhawi", "Hofer"]  # by sequence
    assert c["container-title"] == "AIAA Propulsion and Energy Forum" and c["issued"] == {"date-parts": [[2020, 1, 6]]}


def test_title_search_needs_a_near_exact_match(net, resolver):
    net.routes["https://api.crossref.org/works?query.bibliographic=*"] = (200, fx("crossref_search.json"))
    res = resolver.resolve({}, title="Machine learning for plasma diagnostics: A review")
    assert res["source"] == "crossref" and res["csl"]["DOI"] == "10.1088/1361-6595/ab9b52"
    assert res["csl"]["page"] == "074001"
    assert resolver.resolve({}, title="Machine learning for fusion reactor control systems") is None


def test_semantic_scholar_is_the_last_fallback(net, resolver):
    # Crossref unreachable (5xx), no negotiation answer: S2 (raw API JSON) answers
    net.routes[CR + "10.1063/1.4945558"] = (503, b"")
    net.routes["https://doi.org/10.1063/1.4945558"] = (503, b"")
    net.routes["https://api.semanticscholar.org/graph/v1/paper/DOI:10.1063/1.4945558?*"] = (200, fx("s2.json"))
    res = resolver.resolve({"doi": "10.1063/1.4945558"})
    assert res["source"] == "s2"
    c = res["csl"]
    assert c["container-title"] == "Physics of Plasmas" and c["volume"] == "23" and c["page"] == "053501"
    assert any("503" in e for e in res["errors"])
    # an S2 result the import already has is used as is (not fetched again)
    n0 = len(net.calls)
    from app.metadata import Resolver, Cache, RateLimiter
    r2 = Resolver(Cache(None), RateLimiter({}, default=0), email="", ts_port=0)
    res2 = r2.resolve({}, s2={"title": "A paper", "authors": ["A B"], "year": 2001, "venue": "Nature", "doi": ""})
    assert res2["source"] == "s2" and res2["csl"]["container-title"] == "Nature" and len(net.calls) == n0


def test_translation_server_for_a_url_without_identifiers(net, tmp_path):
    from app import metadata as md
    r = md.Resolver(md.Cache(None), md.RateLimiter({}, default=0), email="", ts_port=1969)
    url = "https://www.cambridge.org/core/journals/journal-of-plasma-physics/article/x"
    net.local = (200, fx("translation_server.json"))
    res = r.resolve({}, url=url)
    assert res["source"] == "translation-server"
    c = res["csl"]
    assert c["container-title"] == "Journal of Plasma Physics" and c["container-title-short"] == "J. Plasma Phys."
    assert c["page"] == "905870401" and c["author"] == [{"family": "Helander", "given": "Per"}]
    assert c["editor"] == [{"family": "Editor", "given": "Anne"}]
    assert net.local_calls == [(1969, "/web", url.encode())]  # only the URL is sent, to 127.0.0.1
    # several items on the page (300) -> not used; a server that isn't running is not asked again for a while
    r2 = md.Resolver(md.Cache(None), md.RateLimiter({}, default=0), email="", ts_port=1969)
    net.local = (300, {"url": url, "session": "x", "items": {"1": {"title": "a"}}})
    assert r2.resolve({}, url=url + "?2") is None
    r3 = md.Resolver(md.Cache(None), md.RateLimiter({}, default=0), email="", ts_port=1969)
    net.local = None
    n = len(net.local_calls)
    assert r3.resolve({}, url=url + "?3") is None and r3.resolve({}, url=url + "?4") is None
    assert len(net.local_calls) == n + 1


def test_page_record_is_the_last_resort_and_fills_gaps(net, resolver):
    page = {"title": "A page-only article", "authors": ["Smith, John"], "year": 2022, "venue": "Open Plasma Letters",
            "volume": "3", "issue": "1", "pages": "12-19", "issn": ["1234-5678"], "url": "https://example.org/a"}
    from app.metadata import csl_from_page
    res = resolver.resolve({}, page=csl_from_page(page))
    assert res["source"] == "page"
    c = res["csl"]
    assert (c["volume"], c["page"], c["ISSN"], c["author"]) == ("3", "12-19", "1234-5678", [{"family": "Smith", "given": "John"}])
    # with an authoritative record, the page only adds what it lacks (here: the abstract)
    net.routes[CR + "10.1103/physreve.101.063204"] = (200, fx("crossref_aps.json"))
    res = resolver.resolve({"doi": "10.1103/PhysRevE.101.063204"},
                           page=csl_from_page({"title": "WRONG", "doi": "10.1103/PhysRevE.101.063204",
                                               "abstract": "From the page.", "venue": "Phys Rev E"}))
    assert res["csl"]["title"].startswith("Kinetic simulation") and res["csl"]["abstract"] == "From the page."
    assert res["csl"]["container-title"] == "Physical Review E"


# ---------------------------------------------------------------------------------------------- normalisation
@pytest.mark.parametrize("raw,want", [
    ("van der Waals, Johannes Diderik", {"family": "Waals", "given": "Johannes Diderik", "non-dropping-particle": "van der"}),
    ("Ludwig van Beethoven", {"family": "Beethoven", "given": "Ludwig", "non-dropping-particle": "van"}),
    ("de la Cruz, María", {"family": "Cruz", "given": "María", "non-dropping-particle": "de la"}),
    ("Martin Luther King Jr.", {"family": "King", "given": "Martin Luther", "suffix": "Jr."}),
    ("King, Martin Luther, Jr", {"family": "King", "given": "Martin Luther", "suffix": "Jr."}),
    ("Smith JA", {"family": "Smith", "given": "J. A."}),
    ("J.A. Smith", {"family": "Smith", "given": "J. A."}),
    ("ITER Organization", {"literal": "ITER Organization"}),
    ("The JET Team", {"literal": "The JET Team"}),
    ({"name": "W7-X Team"}, {"literal": "W7-X Team"}),
    ({"given": "JEAN-PIERRE", "family": "BOEUF"}, {"family": "Boeuf", "given": "Jean-Pierre"}),
    ({"family": "Langmuir", "given": "Irving", "suffix": "III"}, {"family": "Langmuir", "given": "Irving", "suffix": "III"}),
])
def test_name_normalisation(raw, want):
    from app.metadata import norm_name
    assert norm_name(raw) == want


def test_titles_keep_case_and_tex_and_lose_markup():
    from app.metadata import clean_markup, clean_abstract
    assert clean_markup("The <i>pH</i> of H<sub>2</sub>O in a DC <b>Glow</b>") == "The pH of H2O in a DC Glow"
    assert clean_markup('Drift of <math alttext="\\nabla B"><mi>∇</mi><mi>B</mi></math> and $\\beta$ &amp; more') == \
        "Drift of $\\nabla B$ and $\\beta$ & more"
    assert clean_markup('<math><semantics><mi>x</mi><annotation encoding="application/x-tex">x^2</annotation></semantics></math>') == "$x^2$"
    assert clean_markup("<mml:math><mml:mi>T</mml:mi><mml:mi>e</mml:mi></mml:math> scaling") == "Te scaling"
    assert clean_markup("eBay and iPhone and LaTeX") == "eBay and iPhone and LaTeX"  # never re-cased
    assert clean_abstract("<jats:title>Abstract</jats:title><jats:p>One.</jats:p><jats:p>Two.</jats:p>") == "One.\n\nTwo."


def test_dates_types_and_identifiers():
    from app import metadata as md
    assert md.date_parts("2020/06/04") == {"date-parts": [[2020, 6, 4]]}
    assert md.date_parts("2019 Nov 12") == {"date-parts": [[2019, 11, 12]]}
    assert md.date_parts({"date-parts": [[None]]}, "2001") == {"date-parts": [[2001]]}
    assert md.CROSSREF_TYPES["posted-content"] == "article" and md.CROSSREF_TYPES["proceedings-article"] == "paper-conference"
    assert md.clean_doi("https://doi.org/10.1103/PhysRevE.101.063204.") == "10.1103/physreve.101.063204"
    assert md.normalize_ids({"doi": "10.48550/arXiv.2401.00001"}) == {"arxiv": "2401.00001"}
    assert md.clean_isbn("0-306-30751-0") == "0306307510" and md.clean_isbn("978-0-306-30751-6") == ""
    assert md.ids_from_url("https://journals.aps.org/pre/abstract/10.1103/PhysRevE.101.063204") == {"doi": "10.1103/physreve.101.063204"}
    assert md.ids_from_url("https://arc.aiaa.org/doi/abs/10.2514/6.2023-1234") == {"doi": "10.2514/6.2023-1234"}
    assert md.ids_from_url("https://onlinelibrary.wiley.com/doi/full/10.1002/ctpp.201900104") == {"doi": "10.1002/ctpp.201900104"}
    assert md.ids_from_url("https://www.osti.gov/biblio/1617000") == {"osti": "1617000"}
    assert md.ids_from_url("https://pubmed.ncbi.nlm.nih.gov/31415926/") == {"pmid": "31415926"}
    assert md.ids_from_url("https://arxiv.org/pdf/2401.00001v2") == {"arxiv": "2401.00001"}


# ---------------------------------------------------------------------------------------------- cache, rate limit
def test_cache_serves_repeats_and_expires(net, tmp_path):
    from app import metadata as md
    clock = [1000.0]
    cache = md.Cache(tmp_path / "c.json", days=1, neg_hours=1, clock=lambda: clock[0])
    r = md.Resolver(cache, md.RateLimiter({}, default=0), email="", ts_port=0)
    net.routes[CR + "10.1063/5.0012345"] = (200, fx("crossref_pop.json"))
    assert r.crossref("10.1063/5.0012345")["title"].startswith("Sheath")
    assert r.crossref("10.1063/5.0012345") and len(net.calls) == 1
    # a fresh Resolver on the same file: still cached (it's on disk)
    r2 = md.Resolver(md.Cache(tmp_path / "c.json", days=1, clock=lambda: clock[0]), md.RateLimiter({}, default=0),
                     email="", ts_port=0)
    assert r2.crossref("10.1063/5.0012345") and len(net.calls) == 1
    # "not found" is cached for an hour; errors are not cached at all
    assert r.crossref("10.9999/nope") is None and r.crossref("10.9999/nope") is None and len(net.calls) == 2
    net.routes[CR + "10.9999/flaky"] = (500, b"")
    for _ in range(2):
        with pytest.raises(md.MetaError):
            r.crossref("10.9999/flaky")
    assert len(net.calls) == 4
    clock[0] += 2 * 86400
    assert r.crossref("10.1063/5.0012345") and len(net.calls) == 5  # expired: asked again


def test_rate_limiter_spaces_requests_per_host():
    from app.metadata import RateLimiter
    t = [0.0]
    slept = []

    def sleep(d):
        slept.append(round(d, 3))
        t[0] += d
    rl = RateLimiter({"export.arxiv.org": 3.0, "api.crossref.org": 0.25}, clock=lambda: t[0], sleep=sleep)
    rl.wait("export.arxiv.org")
    rl.wait("api.crossref.org")  # another host doesn't wait for arXiv
    rl.wait("export.arxiv.org")
    rl.wait("export.arxiv.org")
    assert slept == [3.0, 3.0]


# ---------------------------------------------------------------------------------------------- SSRF guard
def test_only_allowed_hosts_and_public_addresses(net, resolver, monkeypatch):
    from app import metadata as md, urlfetch
    # a redirect to a host that isn't an API host is refused (not followed)
    net.routes[CR + "10.1000/redir"] = FakeResp(302, b"", {"Location": "https://evil.example.com/x"})
    net.routes["https://doi.org/10.1000/redir"] = FakeResp(302, b"", {"Location": "http://169.254.169.254/latest/meta-data"})
    res = resolver.resolve({"doi": "10.1000/redir"})
    assert res["csl"] is None and any("isn't one of the hosts" in e for e in res["errors"])
    assert all("evil" not in u and "169.254" not in u for u, _, _ in net.calls)
    # every request any resolver made went to an allowed host, over https
    from urllib.parse import urlsplit
    assert {urlsplit(u).hostname for u, _, _ in net.calls} <= md.ALLOWED_HOSTS
    assert all(u.startswith("https://") for u, _, _ in net.calls)
    # an allowed name that resolves to a private address is refused before connecting
    monkeypatch.setattr(urlfetch, "resolve", lambda host: ["10.0.0.7"])
    n = len(net.calls)
    with pytest.raises(md.MetaError):
        resolver.http(CR + "10.1000/private")
    assert len(net.calls) == n
    with pytest.raises(urlfetch.FetchError):
        urlfetch.fetch("https://example.org/x", allowed_hosts=md.ALLOWED_HOSTS)
    # the local fetch only knows 127.0.0.1 + a port + a path
    with pytest.raises(urlfetch.FetchError):
        urlfetch.fetch_local(1969, "//evil.example/web")
    with pytest.raises(urlfetch.FetchError):
        urlfetch.fetch_local(0, "/web")


def test_compressed_or_oversized_answers_are_refused(net):
    from app import urlfetch
    net.routes["https://api.crossref.org/works/10.1/gz"] = FakeResp(200, b"x", {"Content-Encoding": "gzip"})
    net.routes["https://api.crossref.org/works/10.1/big"] = FakeResp(200, b"x" * 5000)
    with pytest.raises(urlfetch.FetchError):
        urlfetch.fetch("https://api.crossref.org/works/10.1/gz", allowed_hosts={"api.crossref.org"})
    with pytest.raises(urlfetch.FetchError):
        urlfetch.fetch("https://api.crossref.org/works/10.1/big", allowed_hosts={"api.crossref.org"}, max_bytes=1000)


# ---------------------------------------------------------------------------------------------- merge rules
class MiniStore:
    """Enough of app/store.Store for apply()."""

    def __init__(self, tmp):
        from app.store import Store
        self.s = Store(tmp / "vault")

    def __getattr__(self, k):
        return getattr(self.s, k)


def test_apply_crossref_beats_page_and_page_never_overwrites_crossref(tmp_path):
    from app import metadata as md
    st = MiniStore(tmp_path)
    n = st.create({"type": "paper", "title": "breathing mode osc. (scraped)", "authors": ["A. Physicist"], "year": 2015})
    cr = md.csl_from_crossref(json.loads(fx("crossref_aps.json"))["message"])
    page = md.csl_from_page({"title": "Page title", "authors": ["X Y"], "year": 2019, "venue": "Some venue"})
    assert md.apply(st, n["id"], {"csl": page, "source": "page"}, fresh=True)
    assert st.get(n["id"])["meta_source"] == "page" and st.get(n["id"])["title"] == "Page title"
    assert md.apply(st, n["id"], {"csl": cr, "source": "crossref"})
    got = st.get(n["id"])
    assert got["meta_source"] == "crossref" and got["title"].startswith("Kinetic simulation")
    assert got["authors"][0] == "Johannes D. van der Berg" and got["year"] == 2020 and got["venue"] == "Physical Review E"
    assert got["doi"] == "10.1103/physreve.101.063204" and got["csl"]["page"] == "063204" and got["meta_checked"]
    # a later page scrape doesn't replace the Crossref record or the fields derived from it
    md.apply(st, n["id"], {"csl": page, "source": "page"})
    got = st.get(n["id"])
    assert got["meta_source"] == "crossref" and got["title"].startswith("Kinetic simulation")
    # the record round-trips through the note file
    assert md.legacy_from_csl(st.s._parse(n["id"], st.note_path(n["id"]).read_text())["csl"])["year"] == 2020


def test_zotero_metadata_is_not_overwritten_but_gaps_are_filled(tmp_path):
    from app import metadata as md
    st = MiniStore(tmp_path)
    n = st.create({"type": "paper", "title": "Kinetic simulation of ExB instability (my Zotero title)",
                   "authors": ["J. van der Berg"], "year": 2020, "venue": "Phys. Rev. E", "doi": "10.1103/PhysRevE.101.063204",
                   "zotero_key": "ZK1", "meta_source": "zotero"})
    cr = md.csl_from_crossref(json.loads(fx("crossref_aps.json"))["message"])
    md.apply(st, n["id"], {"csl": cr, "source": "crossref"}, force=True)
    got = st.get(n["id"])
    assert got["meta_source"] == "zotero"
    assert (got["title"], got["authors"], got["venue"]) == (n["title"], ["J. van der Berg"], "Phys. Rev. E")
    assert got["csl"]["title"] == n["title"] and got["csl"]["container-title"] == "Phys. Rev. E"
    assert got["csl"]["volume"] == "101" and got["csl"]["page"] == "063204" and got["csl"]["ISSN"]  # filled in
    assert got["csl"]["type"] == "article-journal"


def test_old_paper_without_a_record_only_gets_gaps_filled_unless_refreshed(tmp_path):
    from app import metadata as md
    st = MiniStore(tmp_path)
    n = st.create({"type": "paper", "title": "My own title for it", "authors": ["Someone"], "doi": "10.1063/5.0012345"})
    cr = md.csl_from_crossref(json.loads(fx("crossref_pop.json"))["message"])
    md.apply(st, n["id"], {"csl": cr, "source": "crossref"})  # a background pass, not fresh, not forced
    got = st.get(n["id"])
    assert got["title"] == "My own title for it" and got["authors"] == ["Someone"]
    assert got["venue"] == "Physics of Plasmas" and got["year"] == 2020  # empty before: filled
    md.apply(st, n["id"], {"csl": cr, "source": "crossref"}, force=True, keep_edits=False)
    assert st.get(n["id"])["title"] == "Sheath formation at a floating wall: a kinetic study"


def test_edits_flow_into_the_record_and_survive_lookups(tmp_path):
    from app import metadata as md
    st = MiniStore(tmp_path)
    n = st.create({"type": "paper", "title": "x", "doi": "10.1063/5.0012345"})
    cr = md.csl_from_crossref(json.loads(fx("crossref_pop.json"))["message"])
    md.apply(st, n["id"], {"csl": cr, "source": "crossref"}, fresh=True)
    st.update(n["id"], {"title": "Sheath formation at a floating wall (corrected)", "year": 2021})
    got = st.get(n["id"])
    assert got["csl"]["title"] == "Sheath formation at a floating wall (corrected)"
    assert got["csl"]["issued"] == {"date-parts": [[2021]]}
    assert sorted(got["csl"]["custom"]["rb_edited"]) == ["title", "year"]
    md.apply(st, n["id"], {"csl": cr, "source": "crossref"}, force=True)  # refresh, keeping edits
    got = st.get(n["id"])
    assert got["title"] == "Sheath formation at a floating wall (corrected)" and got["year"] == 2021
    assert got["csl"]["title"] == "Sheath formation at a floating wall (corrected)"
    md.apply(st, n["id"], {"csl": cr, "source": "crossref"}, force=True, keep_edits=False)  # replace them
    got = st.get(n["id"])
    assert got["title"].startswith("Sheath formation at a floating wall: a kinetic") and got["year"] == 2020
    assert "rb_edited" not in (got["csl"].get("custom") or {})


def test_cite_reads_the_record(tmp_path):
    """The citations side (app/cite.py csl_for) uses the stored record."""
    cite = pytest.importorskip("app.cite")
    from app import metadata as md
    st = MiniStore(tmp_path)
    n = st.create({"type": "paper", "title": "x", "doi": "10.1103/PhysRevE.101.063204"})
    md.apply(st, n["id"], {"csl": md.csl_from_crossref(json.loads(fx("crossref_aps.json"))["message"]), "source": "crossref"},
             fresh=True)
    item = cite.csl_for(st.get(n["id"]))
    assert item["container-title"] == "Physical Review E" and item["page"] == "063204" and item["volume"] == "101"


# ---------------------------------------------------------------------------------------------- PDFs
def test_pdf_xmp_and_info_doi(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, StreamObject
    from app import metadata as md, pdftext
    w = PdfWriter()
    w.add_blank_page(612, 792)
    w.add_metadata({"/Title": "A paper", "/Subject": "Phys. Plasmas 27, 063505 (2020); doi: 10.1063/5.0099999"})
    xmp = StreamObject()
    xmp.set_data(b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                 b'<rdf:Description xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">'
                 b'<prism:doi>10.1063/5.0012345</prism:doi></rdf:Description></rdf:RDF></x:xmpmeta>')
    xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    w._root_object[NameObject("/Metadata")] = w._add_object(xmp)
    p = tmp_path / "x.pdf"
    with p.open("wb") as f:
        w.write(f)
    info = pdftext.pdf_info(p)
    assert info["xmp_doi"] == "10.1063/5.0012345" and "10.1063/5.0099999" in info["subject"]
    assert md.ids_from_pdf_info(info) == {"doi": "10.1063/5.0012345"}  # XMP beats the subject line
    got: dict = {}
    pdftext.extract(p, info=got)  # through the sandboxed child process
    assert got.get("xmp_doi") == "10.1063/5.0012345"


# ---------------------------------------------------------------------------------------------- the app
@pytest.fixture()
def app_env(tmp_path, monkeypatch, net):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    monkeypatch.setenv("RB_PDF_INPROCESS", "1")
    monkeypatch.setenv("RB_METADATA", "1")
    monkeypatch.setenv("RB_TRANSLATION_SERVER_PORT", "0")
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    import app.urlfetch as uf  # the re-imported module: patch it again
    monkeypatch.setattr(uf, "resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(uf, "_request", net.request)
    monkeypatch.setattr(uf, "_request_local", net.request_local)
    monkeypatch.setattr(server.scholar, "lookup", lambda q: None)
    r = server.metadata.Resolver(server.metadata.Cache(None), server.metadata.RateLimiter({}, default=0), email="", ts_port=0)
    monkeypatch.setattr(server.metadata, "resolver_for", lambda store: r)
    events = []
    monkeypatch.setattr(server.event_bus, "publish", lambda ev: events.append(ev))
    from fastapi.testclient import TestClient
    return TestClient(server.app), server, events


def test_linked_import_resolves_crossref_within_the_response(app_env, net):
    c, server, events = app_env
    net.routes[CR + "10.1103/physreve.101.063204"] = (200, fx("crossref_aps.json"))
    r = c.post("/api/import/linked", json={
        "url": "https://journals.aps.org/pre/abstract/10.1103/PhysRevE.101.063204", "title": "Kinetic simulation (page)",
        "authors": ["Berg, Johannes van der"], "doi": "10.1103/PhysRevE.101.063204", "year": "2020/06/08",
        "venue": "Physical Review E", "abstract": "The page's abstract.", "text": "[[section 1. Intro]]\n\nBody text."})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["meta_source"] == "crossref" and n["title"].startswith("Kinetic simulation of the $E\\times B$")
    assert n["csl"]["page"] == "063204" and n["csl"]["abstract"] == "The page's abstract."  # page fills the gap
    assert n["authors"] == ["Johannes D. van der Berg", "Martin King Jr.", "J.-P. Boeuf"]
    assert n["source"] == "linked"
    assert any(e.get("reason") == "metadata" and e["ids"] == [n["id"]] for e in events)


def test_pdf_import_resolves_in_the_background_with_a_live_event(app_env, net):
    c, server, events = app_env
    net.routes[CR + "10.1063/5.0012345"] = (200, fx("crossref_pop.json"))
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    cv.drawString(72, 720, "Sheath formation at a floating wall")
    cv.drawString(72, 700, "doi: 10.1063/5.0012345")
    cv.showPage()
    cv.save()
    r = c.post("/api/import/pdf", files={"file": ("x.pdf", buf.getvalue(), "application/pdf")})
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    for _ in range(100):
        if server.store.get(nid).get("meta_source") == "crossref":
            break
        time.sleep(0.05)
    n = server.store.get(nid)
    assert n["meta_source"] == "crossref" and n["venue"] == "Physics of Plasmas" and n["year"] == 2020
    assert n["title"] == "Sheath formation at a floating wall: a kinetic study"
    for _ in range(40):
        if events:
            break
        time.sleep(0.05)
    assert {"type": "notes-changed", "ids": [nid], "reason": "metadata", "source": "crossref"} in events


def test_lookup_by_isbn_when_semantic_scholar_has_nothing(app_env, net):
    c, server, events = app_env
    net.routes["https://openlibrary.org/api/books?bibkeys=ISBN%3A9780306307515&format=json&jscmd=data"] = (200, fx("openlibrary.json"))
    r = c.post("/api/import/lookup", json={"query": "ISBN 978-0-306-30751-5"})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["title"].startswith("Introduction to Plasma Physics") and n["authors"] == ["Francis F. Chen"]
    assert n["meta_source"] == "openlibrary" and n["csl"]["type"] == "book" and n["year"] == 1984


def test_refresh_endpoint(app_env, net):
    c, server, events = app_env
    n = server.store.create({"type": "paper", "title": "old scraped title", "doi": "10.1063/5.0012345"})
    net.routes[CR + "10.1063/5.0012345"] = (200, fx("crossref_pop.json"))
    r = c.post(f"/api/notes/{n['id']}/refresh-metadata")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["metadata"]["found"] and out["metadata"]["source"] == "crossref" and out["metadata"]["changed"]
    assert out["title"] == "Sheath formation at a floating wall: a kinetic study" and out["csl"]["ISSN"] == "1070-664X, 1089-7674"
    assert events and events[-1]["ids"] == [n["id"]]
    frag = server.store.create({"type": "fragment", "parent": n["id"], "title": "q", "quote": "q"})
    assert c.post(f"/api/notes/{frag['id']}/refresh-metadata").status_code == 400
    assert c.post("/api/notes/nope/refresh-metadata").status_code == 404
    # nothing found: says so, changes nothing
    n2 = server.store.create({"type": "paper", "title": "zz", "doi": "10.9999/missing"})
    out = c.post(f"/api/notes/{n2['id']}/refresh-metadata").json()
    assert out["metadata"]["found"] is False and out["title"] == "zz"


def test_disabled_means_no_requests(app_env, net, monkeypatch):
    c, server, events = app_env
    monkeypatch.setenv("RB_METADATA", "0")
    n = server.store.create({"type": "paper", "title": "x", "doi": "10.1063/5.0012345"})
    assert server.metadata.schedule(server.store, n["id"], wait=1) is False
    assert c.post(f"/api/notes/{n['id']}/refresh-metadata").status_code == 503
    assert net.calls == []
