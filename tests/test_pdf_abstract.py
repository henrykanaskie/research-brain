"""A paper's abstract comes from its PDF when no catalogue has one (APS records on Crossref carry none)."""
from app.pdftext import guess_abstract

from test_api import client, make_pdf  # noqa: F401  (fixture)

APS_ABSTRACT = [
    "The classical and quantum theories leading to the asymptotic Bethe formula of the stopping power of",
    "matter for charged particles heavier than the electron are briefly reviewed. Models and approximations for",
    "the practical calculation of various corrections that extend the validity range of the formula are described.",
    "The asymptotic formula and the associated shell correction were determined previously from an extensive",
    "database of atomic generalized oscillator strengths, calculated for an independent-electron model.",
]

# page 1 as pdftext.extract gives it for a Physical Review paper: no "Abstract" heading
APS_PAGE = "\n\n".join([
    "[[page 1]]", "PHYSICAL REVIEW A 106, 032809 (2022)",
    "Bethe stopping-power formula and its corrections", "Francesc Salvat *",
    "Facultat de Física, FQA and ICC, Universitat de Barcelona, Diagonal 645, 08028 Barcelona, Catalonia, Spain",
    "(Received 10 May 2022; accepted 29 August 2022; published 19 September 2022)",
    *APS_ABSTRACT,
    "DOI: 10.1103/PhysRevA.106.032809", "I. INTRODUCTION",
    "A fast charged particle in matter loses energy through interactions of different kinds, namely, inelastic",
    "collisions, i.e., interactions that produce electronic excitations of the material and many other things too.",
])


def test_unlabelled_abstract_between_authors_and_first_section():
    a = guess_abstract(APS_PAGE)
    assert a.startswith("The classical and quantum theories") and a.endswith("independent-electron model.")
    assert "DOI" not in a and "Barcelona" not in a and "fast charged particle" not in a


def test_abstract_heading_and_keywords_end_it():
    text = "\n".join(["[[page 1]]", "A title of the paper here", "Some Author, Some University",
                      "Abstract. " + APS_ABSTRACT[0], *APS_ABSTRACT[1:], "Keywords: stopping power",
                      "1. Introduction", "Body text that must not be part of the abstract at all, not one bit."])
    a = guess_abstract(text)
    assert a.startswith("The classical") and "Keywords" not in a and "Body text" not in a


def test_first_prose_block_not_a_later_longer_one():
    # no Introduction heading: the block ending at "Keywords:" wins over the longer body text after it
    text = "\n\n".join(["[[page 1]]", "Plasma and Fusion Research: Letters", *APS_ABSTRACT, "Keywords:", "DOI:",
                        *(APS_ABSTRACT[::-1] * 3)])
    assert guess_abstract(text) == " ".join(APS_ABSTRACT)


def test_no_abstract_on_a_page_of_short_lines():
    assert guess_abstract("[[page 1]]\nTitle\nAuthor\n\n[[page 2]]\nThe loss is L.") == ""


def _aps_pdf() -> bytes:
    return make_pdf("Bethe stopping-power formula and its corrections",
                    "\n".join(["Francesc Salvat", "Universitat de Barcelona, Barcelona, Spain",
                               "(Received 10 May 2022; accepted 29 August 2022)", "", *APS_ABSTRACT, "",
                               "DOI: 10.1103/PhysRevA.106.032809", "I. INTRODUCTION"]))


def test_imported_pdf_gets_its_abstract(client):
    r = client.post("/api/import/pdf", files={"file": ("bethe.pdf", _aps_pdf(), "application/pdf")})
    assert r.status_code == 201, r.text
    n = client.get(f"/api/notes/{r.json()['id']}").json()
    assert n["abstract"].startswith("The classical and quantum theories"), n["abstract"]


def test_papers_already_stored_get_theirs_at_startup(client):
    from app import server
    nid = client.post("/api/import/pdf", files={"file": ("bethe.pdf", _aps_pdf(), "application/pdf")}).json()["id"]
    n = server.store.get(nid)
    n["abstract"] = ""
    server.store.save(n)
    assert server.store.fill_missing_abstracts() == [nid]
    assert server.store.get(nid)["abstract"].startswith("The classical")
    assert server.store.fill_missing_abstracts() == []  # nothing left to do the second time
