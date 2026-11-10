"""Active recall ("Review"): spaced repetition over the user's own writing.

Items come from the library, nothing is generated:
  - every paper/note with a non-empty summary  -> kind "summary"
      prompt: title + authors/year,   answer: the user's summary
  - every fragment with a quote and a non-empty note -> kind "fragment"
      prompt: the quote + its paper's title and locator,   answer: the user's note
An item's id is the id of the note it comes from (each note yields at most one item).

Schedule state lives in vault/state/review.json (backed up with the vault):
  {"items": {id: {"kind", "reps", "ease", "interval", "due": "YYYY-MM-DD", "history": [{"at", "grade"}]}},
   "days":  {"YYYY-MM-DD": {"new": int, "reviewed": int}}}
State for notes that no longer exist is dropped on every load, so deleted sources disappear.

Scheduling is SM-2 (grades 0-5; < 3 is a lapse) with two common refinements so the four buttons
give different intervals: "Hard" grows the interval by 1.2x instead of the ease factor, and "Easy"
gets a 1.3x bonus. The UI maps Again/Hard/Good/Easy to grades 1/3/4/5.
"""
from __future__ import annotations

import datetime as dt
import threading

from .store import Store, now_iso

FILE = "review.json"
NEW_PER_DAY = 5
START_EASE = 2.5
MIN_EASE = 1.3
BUTTONS = {"again": 1, "hard": 3, "good": 4, "easy": 5}
_lock = threading.RLock()


def _today() -> dt.date:  # monkeypatched in tests
    return dt.date.today()


# ---------------------------------------------------------------- SM-2
def schedule(state: dict | None, grade: int) -> dict:
    """Return the new scheduling fields (reps, ease, interval) after a review with `grade` (0-5)."""
    s = state or {}
    reps, ease, interval = int(s.get("reps", 0)), float(s.get("ease", START_EASE)), int(s.get("interval", 0))
    q = max(0, min(5, int(grade)))
    new_ease = max(MIN_EASE, ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    if q < 3:  # lapse: start the item over; ease still drops
        return {"reps": 0, "ease": round(new_ease, 3), "interval": 1}
    if reps == 0:
        nxt = {3: 1, 4: 1, 5: 4}[q]
    elif reps == 1:
        nxt = {3: 3, 4: 6, 5: 8}[q]
    else:
        factor = 1.2 if q == 3 else new_ease * (1.3 if q == 5 else 1.0)
        nxt = round(interval * factor)
        if q >= 4:
            nxt = max(nxt, interval + 1)
    return {"reps": reps + 1, "ease": round(new_ease, 3), "interval": max(1, int(nxt))}


def preview(state: dict | None) -> dict:
    """Next interval in days for each button, for labelling them."""
    return {name: schedule(state, g)["interval"] for name, g in BUTTONS.items()}


# ---------------------------------------------------------------- items
def items_from(notes: list[dict]) -> dict[str, dict]:
    by_id = {n["id"]: n for n in notes}
    out: dict[str, dict] = {}
    for n in notes:
        if n["type"] in ("paper", "note") and n["summary"].strip():
            out[n["id"]] = {
                "id": n["id"], "kind": "summary", "source": n["id"], "created": n["created"],
                "prompt": {"title": n["title"], "type": n["type"], "authors": n["authors"][:6], "year": n["year"]},
                "answer": n["summary"],
            }
        elif n["type"] == "fragment" and n["quote"].strip() and n["body"].strip():
            p = by_id.get(n["parent"])
            out[n["id"]] = {
                "id": n["id"], "kind": "fragment", "source": n["id"], "created": n["created"],
                "prompt": {"quote": n["quote"], "locator": n["locator"] or (f"p. {n['page']}" if n["page"] else ""),
                           "paper_title": p["title"] if p else "", "paper_id": p["id"] if p else "",
                           "title": n["title"]},
                "answer": n["body"],
            }
    return out


class Review:
    def __init__(self, store: Store):
        self.store = store

    # ---------- persistence ----------
    def load(self, notes: list[dict] | None = None) -> tuple[dict, dict[str, dict]]:
        """(state, current items). Prunes state for notes that are gone."""
        notes = self.store.all() if notes is None else notes
        data = self.store.load_json(FILE)
        data.setdefault("items", {})
        data.setdefault("days", {})
        alive = {n["id"] for n in notes}
        gone = [k for k in data["items"] if k not in alive]
        for k in gone:
            del data["items"][k]
        if gone:
            self.store.save_json(FILE, data)
        return data, items_from(notes)

    # ---------- queue ----------
    def _new_left(self, data: dict, today: dt.date) -> int:
        return max(0, NEW_PER_DAY - int(data["days"].get(today.isoformat(), {}).get("new", 0)))

    def _ordered(self, data: dict, items: dict, today: dt.date) -> tuple[list[str], list[str]]:
        t = today.isoformat()
        due = [i for i in items if i in data["items"] and data["items"][i].get("due", t) <= t]
        due.sort(key=lambda i: (data["items"][i].get("due", t), (data["items"][i].get("history") or [{}])[-1].get("at", "")))
        new = [i for i in items if i not in data["items"]]
        new.sort(key=lambda i: (items[i]["created"], i))
        return due, new[: self._new_left(data, today)]

    def queue(self, limit: int = 10) -> dict:
        with _lock:
            today = _today()
            data, items = self.load()
            due, new = self._ordered(data, items, today)
            picked = (due + new)[: max(0, limit)]
            out = []
            for i in picked:
                st = data["items"].get(i)
                it = dict(items[i])
                it["is_new"] = st is None
                it["due"] = st["due"] if st else today.isoformat()
                it["intervals"] = preview(st)
                it["last"] = (st.get("history") or [None])[-1] if st else None
                out.append(it)
            return {"items": out, "due": len(due), "new": len(new), "total": len(items)}

    # ---------- grading ----------
    def get_item(self, item_id: str) -> dict:
        data, items = self.load()
        if item_id not in items:
            raise KeyError(item_id)
        return items[item_id]

    def grade(self, item_id: str, grade: int, recalled_text: str = "") -> dict:
        with _lock:
            today = _today()
            data, items = self.load()
            if item_id not in items:
                raise KeyError(item_id)
            prev = data["items"].get(item_id)
            nxt = schedule(prev, grade)
            st = dict(prev or {"history": []})
            st.update(nxt)
            st["kind"] = items[item_id]["kind"]
            st["due"] = (today + dt.timedelta(days=nxt["interval"])).isoformat()
            entry = {"at": now_iso(), "date": today.isoformat(), "grade": int(grade)}
            if recalled_text.strip():
                entry["recalled"] = recalled_text.strip()[:4000]
            st["history"] = list(st.get("history") or []) + [entry]
            data["items"][item_id] = st
            day = data["days"].setdefault(today.isoformat(), {"new": 0, "reviewed": 0})
            day["reviewed"] = int(day.get("reviewed", 0)) + 1
            if prev is None:
                day["new"] = int(day.get("new", 0)) + 1
            self.store.save_json(FILE, data)
            return {"id": item_id, "reps": st["reps"], "ease": st["ease"], "interval": st["interval"], "due": st["due"]}

    # ---------- stats ----------
    def stats(self) -> dict:
        with _lock:
            today = _today()
            data, items = self.load()
            due, new = self._ordered(data, items, today)
            days = data["days"]
            reviewed_today = int(days.get(today.isoformat(), {}).get("reviewed", 0))
            # streak: consecutive days with a review, ending today (or yesterday, if today isn't done yet)
            streak, d = 0, today if reviewed_today else today - dt.timedelta(days=1)
            while int(days.get(d.isoformat(), {}).get("reviewed", 0)) > 0:
                streak += 1
                d -= dt.timedelta(days=1)
            return {"due_today": len(due) + len(new), "due": len(due), "new": len(new),
                    "reviewed_today": reviewed_today, "streak": streak, "total": len(items)}
