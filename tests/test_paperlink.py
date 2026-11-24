"""A link to a paper on its publisher's site, paywalled or not, turned into the paper (app/paperlink.py): from the
link itself, Crossref, OpenAlex, the page's citation tags or the title the link spells out. Nothing is guessed.
Typed into Add, dropped on the window, or linked in a web-search answer (app/finder.py)."""
from app import finder, paperlink
from test_api import client  # noqa: F401  (fixture)


def test_what_a_link_says_by_itself():
    r = paperlink.read
    assert r("https://www.sciencedirect.com/science/article/pii/S0010465500002319") == {"pii": "S0010465500002319"}
    assert r("https://www.sciencedirect.com/science/article/abs/pii/S0010-4655(00)00231-9")["pii"] == "S0010465500002319"
    assert r("https://linkinghub.elsevier.com/retrieve/pii/S0010465500002319")["pii"] == "S0010465500002319"
    assert r("https://ieeexplore.ieee.org/document/8253399") == {"ieee": "8253399"}
    assert r("https://ieeexplore.ieee.org/abstract/document/8253399/") == {"ieee": "8253399"}
    assert r("https://pubs.aip.org/aip/pop/article/27/3/033501/108346") == \
        {"volume": "27", "issue": "3", "page": "033501", "journal": "pop"}
    assert r("https://academic.oup.com/mnras/article/498/1/1/5891176")["journal"] == "mnras"
    assert r("https://www.jstor.org/stable/2984829") == {"doi_guess": "10.2307/2984829"}
    assert r("https://www.nature.com/articles/s41586-021-03819-2") == {"doi_guess": "10.1038/s41586-021-03819-2"}
    assert r("https://onlinelibrary.wiley.com/doi/10.1002/ctpp.201900001/abstract") == {"doi": "10.1002/ctpp.201900001"}
    assert r("https://journals.aps.org/pre/abstract/10.1103/PhysRevE.101.033203") == {"doi": "10.1103/physreve.101.033203"}
    assert r("https://www.researchgate.net/publication/312345678_Tutorial_Physics_and_modeling_of_Hall_thrusters") == \
        {"title": "Tutorial Physics and modeling of Hall thrusters"}
    assert "title" not in r("https://www.researchgate.net/publication/312345678_Hall")  # too short to be a title
    assert r("https://www.semanticscholar.org/paper/Attention-is-All-you-Need-Vaswani/"
             "204e3073870fae3d05bcbc2f6a8e263d9b72e776")["s2"] == "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
    assert paperlink.is_paper_link("https://www.sciencedirect.com/x") and not paperlink.is_paper_link("https://example.com/a")
    assert paperlink.links_in("see [x](https://www.sciencedirect.com/science/article/pii/S0010465500002319). "
                              "and https://example.com/z") == ["https://www.sciencedirect.com/science/article/pii/S0010465500002319"]


def _offline(monkeypatch, crossref=(), openalex=(), page="", has=(), titles=None):
    asked = []
    monkeypatch.setattr(paperlink, "_crossref", lambda p: asked.append(("crossref", p)) or list(crossref))
    monkeypatch.setattr(paperlink, "_openalex", lambda p: asked.append(("openalex", p)) or list(openalex))
    monkeypatch.setattr(paperlink, "_page", lambda u: asked.append(("page", u)) or page)
    monkeypatch.setattr(paperlink, "_crossref_has", lambda d: d in has)
    monkeypatch.setattr(paperlink, "_by_title", lambda t: (titles or {}).get(t, ""))
    return asked


def test_a_paywalled_link_is_found_without_its_page(monkeypatch):
    f = lambda u: paperlink.find(u, online=True)  # noqa: E731
    _offline(monkeypatch, crossref=[{"DOI": "10.1016/S0010-4655(00)00231-9"}])
    assert f("https://www.sciencedirect.com/science/article/pii/S0010465500002319") == \
        {"doi": "10.1016/s0010-4655(00)00231-9", "how": "Crossref"}

    # several papers share volume 27, page 033501: only the publisher's own is taken
    asked = _offline(monkeypatch, openalex=[{"doi": "https://doi.org/10.1103/physrevaccelbeams.27.033501"},
                                             {"doi": "https://doi.org/10.1063/1.5136042"}])
    assert f("https://pubs.aip.org/aip/pop/article/27/3/033501/108346") == {"doi": "10.1063/1.5136042", "how": "OpenAlex"}
    assert asked[0][1]["filter"] == "biblio.volume:27,biblio.first_page:033501,biblio.issue:3"
    _offline(monkeypatch, openalex=[{"doi": "https://doi.org/10.1063/1.1"}, {"doi": "https://doi.org/10.1063/1.2"}])
    assert f("https://pubs.aip.org/aip/pop/article/27/3/033501/108346") == {}  # two of AIP's: can't tell

    # a DOI made from the link is used only when Crossref has it
    _offline(monkeypatch, has={"10.1038/s41586-021-03819-2"})
    assert f("https://www.nature.com/articles/s41586-021-03819-2")["doi"] == "10.1038/s41586-021-03819-2"
    _offline(monkeypatch)
    assert f("https://www.jstor.org/stable/2984829") == {}

    # the page's tags when it can be read; the title the link spells out otherwise
    page = '<html><head><meta name="citation_title" content="A &amp; B"><meta name="citation_doi" content="doi:10.1234/ab.5"></head>'
    _offline(monkeypatch, page=page)
    assert f("https://www.mdpi.com/2076-3417/1/2/3") == {"doi": "10.1234/ab.5", "how": "the page"}
    t = "Tutorial Physics and modeling of Hall thrusters"
    _offline(monkeypatch, titles={t: "10.1063/1.4972269"})
    assert f("https://www.researchgate.net/publication/312345678_Tutorial_Physics_and_modeling_of_Hall_thrusters") == \
        {"doi": "10.1063/1.4972269", "how": "its title"}
    ieee = '<script>xplGlobal.document.metadata={"title":"x","doi":"10.1109/TPS.2018.2789478","articleNumber":"8253399"};</script>'
    _offline(monkeypatch, page=ieee)
    assert f("https://ieeexplore.ieee.org/document/8253399")["doi"] == "10.1109/tps.2018.2789478"
    _offline(monkeypatch)
    assert f("https://ieeexplore.ieee.org/document/8253399") == {}
    assert "IEEE" in paperlink.blocked_message("https://ieeexplore.ieee.org/document/8253399")

    # online lookups off: only what the link says, nobody asked
    asked = _offline(monkeypatch)
    assert paperlink.find("https://www.sciencedirect.com/science/article/pii/S0010465500002319", online=False) == {}
    assert paperlink.find("https://doi.org/10.1063/1.4945558", online=False)["doi"] == "10.1063/1.4945558"
    assert asked == []


def test_add_and_drop_a_paywalled_link(client, monkeypatch):
    from app import server
    sd = "https://www.sciencedirect.com/science/article/pii/S0010465500002319"
    monkeypatch.setattr(server.paperlink, "find", lambda u, **k: {"doi": "10.1063/1.4945558", "how": "Crossref"}
                        if "S0010465500002319" in u else {})
    r = client.post("/api/import/lookup", json={"query": sd})
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Hall thruster plasma oscillations"
    assert client.post("/api/import/lookup", json={"query": sd}).json()["duplicate"]
    # a link no one can read is said so, and isn't searched as if it were a title
    r = client.post("/api/import/lookup", json={"query": "https://ieeexplore.ieee.org/document/8253399"})
    assert r.status_code == 404 and "IEEE" in r.json()["detail"]
    # dropped on the window: the page isn't a PDF, so the paper it is is added
    monkeypatch.setattr(server.urlfetch, "check_url", lambda u: ["93.184.216.34"])

    def not_pdf(url, max_bytes=None):
        raise server.urlfetch.FetchError("The site answered HTTP 403", 502)
    monkeypatch.setattr(server.urlfetch, "download_pdf", not_pdf)
    monkeypatch.setattr(server.paperlink, "find", lambda u, **k: {"arxiv": "1706.03762", "how": "the page"})
    r = client.post("/api/import/url", json={"url": "https://www.sciencedirect.com/science/article/pii/S1111111111111111"})
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Attention Is All You Need"


def test_a_web_answers_publisher_links_become_cards(tmp_path):
    class S:
        root = tmp_path

        def all(self):
            return []
    (tmp_path / "state").mkdir()
    text = ("Two papers: [CRModel](https://www.sciencedirect.com/science/article/pii/S0010465500002319) and "
            "[Hall](https://doi.org/10.1063/1.4945558), plus https://ieeexplore.ieee.org/document/8253399.")
    found = {"https://www.sciencedirect.com/science/article/pii/S0010465500002319": {"doi": "10.1016/x.1"}}
    rows = {"10.1016/x.1": "CRModel", "10.1063/1.4945558": "Hall"}
    out = finder.from_answer(S(), "q", text, lookup=lambda k, v: {"title": rows.get(v), "doi": v},
                             copies=[], wait=True, resolve_link=lambda u: found.get(u, {}))
    assert [i["title"] for i in out["items"]] == ["Hall", "CRModel"]
