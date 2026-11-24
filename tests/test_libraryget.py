"""A paywalled paper through your own library (app/libraryget.py): the sign-in link setting, the link it opens, and
the PDF you download there found in Downloads and attached (only that paper's PDF, never another one)."""
import shutil
import time

import pytest

from app import libraryget
from test_api import client, make_pdf  # noqa: F401  (fixture)


def test_the_library_link():
    assert libraryget.clean_proxy(" https://oregonstate.idm.oclc.org/login?url= ") == "https://oregonstate.idm.oclc.org/login?url="
    assert libraryget.clean_proxy("https://proxy.lib.x.edu/go?u={url}&x=1") == "https://proxy.lib.x.edu/go?u={url}&x=1"
    assert libraryget.clean_proxy("") == ""
    for bad in ("http://oregonstate.idm.oclc.org/login?url=", "oregonstate.idm.oclc.org/login?url=",
                "https://oregonstate.idm.oclc.org/login", "javascript:alert(1)//?url="):
        with pytest.raises(libraryget.LibraryError):
            libraryget.clean_proxy(bad)
    p = "https://oregonstate.idm.oclc.org/login?url="
    assert libraryget.link(p, {"doi": "10.1063/1.4972269"}) == p + "https://doi.org/10.1063/1.4972269"
    assert libraryget.link(p, {"url": "https://www.sciencedirect.com/science/article/pii/S1"}) == \
        p + "https://www.sciencedirect.com/science/article/pii/S1"
    assert libraryget.link("https://x.edu/go?u={url}", {"doi": "10.1234/a"}) == "https://x.edu/go?u=https://doi.org/10.1234/a"
    assert libraryget.link(p, {"url": "file:///etc/passwd"}) == ""
    n = {"doi": "10.1063/1.4972269", "title": "Tutorial: Physics and modeling of Hall thrusters"}
    assert libraryget.is_it(n, "J. Appl. Phys. 121, 011101 (2017); doi: 10.1063/1.4972269")
    assert libraryget.is_it({"title": n["title"]}, "TUTORIAL: PHYSICS AND MODELING OF HALL THRUSTERS\nJ.-P. Boeuf")
    assert not libraryget.is_it(n, "A different paper about tokamaks")
    assert not libraryget.is_it({"title": "Hall"}, "Hall")  # a title too short to tell


def test_its_pdf_is_found_in_downloads_and_attached(client, tmp_path, monkeypatch):
    from app import server
    store = server.store
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / "old.pdf").write_bytes(make_pdf("Tutorial: Physics and modeling of Hall thrusters", "old copy"))
    monkeypatch.setenv("RB_DOWNLOADS", str(dl))
    monkeypatch.setattr(libraryget, "POLL_S", 0.05)
    orig = libraryget.watch
    monkeypatch.setattr(libraryget, "watch", lambda *a, **k: orig(*a, **{**k, "poll": 0.05}))
    nid = client.post("/api/notes", json={"type": "paper", "title": "Tutorial: Physics and modeling of Hall thrusters",
                                          "doi": "10.1063/1.4972269"}).json()["id"]
    assert client.post(f"/api/notes/{nid}/library-access").status_code == 409  # no library link yet
    assert client.put("/api/settings", json={"library_proxy": "http://nope"}).status_code == 400
    assert client.put("/api/settings", json={"library_proxy": "https://oregonstate.idm.oclc.org/login?url="}).json()[
        "library_proxy"] == "https://oregonstate.idm.oclc.org/login?url="
    r = client.post(f"/api/notes/{nid}/library-access").json()
    assert r["url"] == "https://oregonstate.idm.oclc.org/login?url=https://doi.org/10.1063/1.4972269" and r["watching"]
    time.sleep(0.3)
    (dl / "other.pdf").write_bytes(make_pdf("Tokamak divertor detachment", "a different paper"))  # left alone
    time.sleep(0.5)
    assert not store.pdf_path(nid).exists()
    shutil.copy(dl / "old.pdf", dl / "boeuf2017.pdf")  # the old file (there before) isn't it; this new one is
    for _ in range(100):
        if store.pdf_path(nid).exists():
            break
        time.sleep(0.05)
    assert store.pdf_path(nid).exists() and (dl / "boeuf2017.pdf").exists()  # attached; your copy stays
    assert client.get(f"/api/notes/{nid}/library-access").json() == {"watching": False, "attached": True}
