"""Obsidian compatibility: notes whose frontmatter Obsidian has re-serialized still parse identically.

When you edit a property in Obsidian it rewrites the whole frontmatter block in its own style:
block-style lists (`- item`), double-quoted strings where needed, empty values for nulls, `[]` for
empty lists, and (for "Date & time" properties) unquoted ISO timestamps, which YAML would otherwise
read as datetime objects. None of that may change what the app sees.
"""
import json
import re
from pathlib import Path

import pytest

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.store import Store  # noqa: E402

PLAIN = re.compile(r"^[A-Za-z][A-Za-z0-9 ._/-]*$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)?)?$")


def _scalar(v, dates_unquoted: bool) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if dates_unquoted and TIMESTAMP.match(s):
        return s
    if PLAIN.match(s) and s.lower() not in ("true", "false", "null", "yes", "no", "on", "off"):
        return s
    return json.dumps(s, ensure_ascii=False)  # a valid YAML double-quoted scalar


def obsidian_dump(meta: dict, dates_unquoted: bool = False) -> str:
    """Approximates how Obsidian writes frontmatter after a property edit."""
    lines = []

    def emit(key, value, indent=""):
        if isinstance(value, list):
            if not value:
                lines.append(f"{indent}{key}: []")
                return
            lines.append(f"{indent}{key}:")
            for item in value:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        prefix = f"{indent}  - " if first else f"{indent}    "
                        lines.append(f"{prefix}{k}: {_scalar(v, dates_unquoted)}".rstrip())
                        first = False
                else:
                    lines.append(f"{indent}  - {_scalar(item, dates_unquoted)}")
        elif isinstance(value, dict):
            lines.append(f"{indent}{key}:")
            for k, v in value.items():
                emit(k, v, indent + "  ")
        else:
            lines.append(f"{indent}{key}: {_scalar(value, dates_unquoted)}".rstrip())

    for k, v in meta.items():
        emit(k, v)
    return "\n".join(lines) + "\n"


def _split(raw: str):
    assert raw.startswith("---\n")
    end = raw.index("\n---\n", 3)
    return raw[4:end + 1], raw[end + 5:]


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "vault")


def _rich(store):
    other = store.create({"type": "note", "title": "Debye shielding"})
    note = store.create({
        "type": "paper", "title": "Attention: is it \"all\" you need? [v2]", "authors": ["Ashish Vaswani", "Noam Shazeer"],
        "year": 2017, "venue": "NeurIPS", "doi": "10.48550/arXiv.1706.03762", "arxiv": "1706.03762",
        "url": "https://arxiv.org/abs/1706.03762", "abstract": "Line one.\nLine two with $x^2$ and a colon: here.",
        "summary": "Uses # and - and 'quotes'. See [[Debye shielding]] and [[%s|alias]]." % other["id"],
        "tags": ["ml", "attention", "2017"], "body": "Body with [[Debye shielding]]\n\n$$E = mc^2$$",
    })
    store.link(note["id"], other["id"])
    return store.get(note["id"])


@pytest.mark.parametrize("dates_unquoted", [False, True], ids=["quoted-dates", "obsidian-datetime"])
def test_obsidian_reserialized_frontmatter_parses_identically(store, dates_unquoted):
    before = _rich(store)
    path = store.note_path(before["id"])
    front, body = _split(path.read_text(encoding="utf-8"))
    import yaml
    meta = yaml.safe_load(front)
    rewritten = "---\n" + obsidian_dump(meta, dates_unquoted) + "---\n" + body
    assert rewritten != path.read_text(encoding="utf-8")
    assert "tags:\n  - ml\n" in rewritten and "links:\n  - " in rewritten  # block-style lists
    if dates_unquoted:
        assert re.search(r"^created: \d{4}-\d{2}-\d{2}T", rewritten, re.M)
    path.write_text(rewritten, encoding="utf-8")
    after = store.get(before["id"])
    assert after == before


def test_obsidian_empty_and_null_properties(store):
    n = store.create({"type": "note", "title": "Plain"})
    path = store.note_path(n["id"])
    front, body = _split(path.read_text(encoding="utf-8"))
    import yaml
    meta = yaml.safe_load(front)
    meta["year"] = None
    meta["page"] = None
    path.write_text("---\n" + obsidian_dump(meta) + "---\n" + body, encoding="utf-8")
    assert "authors: []" in path.read_text() and re.search(r"^year:$", path.read_text(), re.M)
    assert store.get(n["id"]) == store._parse(n["id"], store._dump(store.get(n["id"])))
    got = store.get(n["id"])
    assert got["year"] is None and got["authors"] == [] and got["tags"] == []


def test_obsidian_added_properties_and_aliases_are_tolerated(store):
    n = store.create({"type": "note", "title": "Keeps working"})
    path = store.note_path(n["id"])
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace("---\n", "---\naliases:\n  - KW\ncssclasses:\n  - wide\n", 1)
    path.write_text(raw, encoding="utf-8")
    got = store.get(n["id"])
    assert got["title"] == "Keeps working" and "aliases" not in got


def test_unknown_frontmatter_survives_app_edits(tmp_path):
    """Properties added in Obsidian (aliases, cssclasses, custom keys) must not be lost when the app saves."""
    from app.store import Store
    s = Store(tmp_path)
    n = s.create({"type": "note", "title": "Sheath notes"})
    p = s.note_path(n["id"])
    raw = p.read_text()
    p.write_text(raw.replace("---\n", "---\naliases:\n  - Bohm sheath\nrating: 4\nreviewed: 2026-11-09\n", 1))
    s.update(n["id"], {"body": "edited in the app"})
    out = p.read_text()
    assert "Bohm sheath" in out and "rating: 4" in out and "reviewed:" in out
    got = s.get(n["id"])
    assert got["body"] == "edited in the app" and got["_extra"]["rating"] == 4
