"""Units for measured quantities on idea cards (app/cards.py): parse the value and unit a paper gives, and
convert them to one SI value so the same quantity from two papers can be compared.

The original value and unit are always kept; the canonical value is derived and only used for comparing,
sorting and showing a conversion. pint does the unit algebra. A few things papers write that pint doesn't
read are rewritten first: `cm-3` (cm^-3), `mTorr`, `Torr`, `sccm`, `G`/`Gauss` (1e-4 T), superscripts,
a unicode minus, a leading scale (`10^17 m^-3`, `1e17 m-3`, `×10^17 m^-3`).

Temperatures given as energies (T_e = 20 eV, kT_e = 3 eV) are converted to kelvin with Boltzmann's
constant, so 20 eV and 2.3e5 K are the same electron temperature.
"""
from __future__ import annotations

import math
import re
import threading
import unicodedata

_lock = threading.Lock()
_ureg = None

# canonical units tried in this order for a quantity of matching dimension (first match wins)
PREFERRED = ("K", "V", "W", "N", "Pa", "A", "A/m^2", "1/m^3", "m/s", "kg/s", "T", "Hz", "s", "m", "J", "kg",
             "m^2", "ohm", "C", "mol/s", "m^3/s", "V/m", "W/m^2", "N*s", "1/s", "m^-2")

SUPER = str.maketrans({"⁻": "-", "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6",
                       "⁷": "7", "⁸": "8", "⁹": "9", "⁺": ""})
ALIASES = {  # whole-token rewrites (case-sensitive first, then lower-case)
    "Torr": "torr", "mTorr": "millitorr", "mtorr": "millitorr", "uTorr": "microtorr", "µTorr": "microtorr",
    "G": "gauss_si", "Gauss": "gauss_si", "gauss": "gauss_si", "kG": "kilogauss_si", "mG": "milligauss_si",
    "sccm": "sccm", "SCCM": "sccm", "slm": "slm", "SLM": "slm", "eV": "eV", "keV": "keV", "meV": "meV",
    "Ohm": "ohm", "Ω": "ohm", "kΩ": "kiloohm", "MΩ": "megaohm", "μ": "µ", "amu": "dalton", "u": "dalton",
    "deg": "degree", "°": "degree", "°C": "degC", "℃": "degC", "°F": "degF", "sec": "s", "secs": "s",
    "mins": "min", "hrs": "hour", "hr": "hour", "percent": "%", "pct": "%", "A/cm2": "A/cm^2",
}


def ureg():
    """The shared pint registry (built once, ~0.1 s), with the units plasma papers use that pint lacks."""
    global _ureg
    with _lock:
        if _ureg is None:
            import pint
            r = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
            for d in ("gauss_si = 1e-4 * tesla", "kilogauss_si = 1e-1 * tesla", "milligauss_si = 1e-7 * tesla",
                      # standard cubic centimetre / litre per minute (0 °C, 1 atm): 1 sccm = 4.4615e-5 mol/min
                      "sccm = 7.43583e-7 * mole / second", "slm = 7.43583e-4 * mole / second"):
                try:
                    r.define(d)
                except Exception:  # already defined in a newer pint
                    pass
            _ureg = r
        return _ureg


# ---------------------------------------------------------------- numbers
_NUM = r"[-+]?(?:\d+(?:[.,]\d+)?|\.\d+)"
_SCI = re.compile(rf"^\s*({_NUM})\s*(?:[eE]\s*([-+]?\d+)|(?:[x×*·]|\\times)\s*10\s*\^?\s*\{{?\s*([-+]?\d+)\s*\}}?)?\s*$")
_RANGE = re.compile(rf"^\s*({_NUM}(?:\s*[eE][-+]?\d+)?)\s*(?:-|–|—|to)\s*({_NUM}(?:\s*[eE][-+]?\d+)?)\s*$")
_PM = re.compile(r"\s*(?:±|\+/-|\+-|\\pm)\s*")


def _clean_num_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).translate(SUPER).replace("−", "-").replace("\u2009", "").replace(" ", "")
    s = s.replace("$", "").replace("{", "").replace("}", "").replace("\\,", "")
    s = re.sub(r"^(?:~|≈|≃|∼|about|approx\.?|approximately|roughly|<|>|≤|≥|<=|>=)\s*", "", s.strip(), flags=re.I)
    return s.strip()


def parse_number(v) -> float | None:
    """A finite float from a number or text such as "2.3e5", "2.3 × 10^5", "2.3x10^{5}", "~20", "1,200"."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    s = _clean_num_text(v)
    if not s or len(s) > 60:
        return None
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    m = _SCI.match(s)
    if not m:
        return None
    try:
        base = float(m.group(1).replace(",", "."))
        exp = m.group(2) or m.group(3)
        f = base * (10.0 ** int(exp)) if exp else base
    except (ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def parse_value(v) -> tuple[float | None, float | None]:
    """(value, uncertainty) from what a model or a user wrote: 20, "20 ± 2", "20-30" (midpoint, half range)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return parse_number(v), None
    if not isinstance(v, str):
        return None, None
    s = unicodedata.normalize("NFKC", v).replace("−", "-").strip()
    parts = _PM.split(s, maxsplit=1)
    if len(parts) == 2:
        return parse_number(parts[0]), _abs(parse_number(parts[1]))
    m = _RANGE.match(s.replace(" ", ""))
    if m:
        a, b = parse_number(m.group(1)), parse_number(m.group(2))
        if a is not None and b is not None and b > a >= 0:
            return (a + b) / 2, (b - a) / 2
    return parse_number(s), None


def _abs(x):
    return abs(x) if x is not None else None


# ---------------------------------------------------------------- units
_SCALE = re.compile(r"^\s*(?:[x×*·]\s*)?(?:(\d+(?:\.\d+)?)\s*[eE]\s*([-+]?\d+)|10\s*\^\s*\{?\s*([-+]?\d+)\s*\}?)\s*")


def clean_unit(u: str) -> tuple[float, str]:
    """(scale factor, pint-readable unit expression) for a unit as a paper writes it."""
    s = unicodedata.normalize("NFKC", str(u or "")).translate(SUPER)
    s = s.replace("−", "-").replace("·", "*").replace("⋅", "*").replace("\\cdot", "*").replace("\\,", " ")
    s = s.replace("$", "").replace("\\mathrm", "").replace("\\text", "").replace("{", "").replace("}", "")
    s = s.replace("\\mu", "µ").replace("μ", "µ").replace("\\Omega", "Ω").replace("\\%", "%").replace("\\", "")
    s = s.strip()
    scale = 1.0
    m = _SCALE.match(s)
    if m:
        if m.group(1):
            scale = float(m.group(1)) * 10.0 ** int(m.group(2))
        else:
            scale = 10.0 ** int(m.group(3))
        s = s[m.end():].strip()
    toks = re.split(r"(\s+|\*|/|\(|\))", s)
    out = []
    for t in toks:
        if not t or t.isspace():
            out.append(" " if t else "")
            continue
        if t in "*/()":
            out.append(t)
            continue
        m2 = re.fullmatch(r"([A-Za-zµΩ°%℃]+)\^?(-?\d+)?", t)
        if m2:
            base, exp = m2.group(1), m2.group(2)
            base = ALIASES.get(base, base)
            out.append(base + (f"^{exp}" if exp else ""))
        else:
            out.append(ALIASES.get(t, t))
    expr = re.sub(r"\s+", " ", "".join(out)).strip()
    expr = re.sub(r"(?<=[A-Za-zµ0-9)]) (?=[A-Za-zµ(])", "*", expr)  # "kg m s^-1" -> kg*m*s^-1
    return scale, expr


def parse_unit(u: str):
    """(scale, pint Unit) or (scale, None) when the unit can't be read. "" is dimensionless."""
    scale, expr = clean_unit(u)
    if not expr:
        return scale, ureg().dimensionless
    if len(expr) > 60:
        return scale, None
    try:
        q = ureg().parse_units(expr)
        return scale, q
    except Exception:
        try:
            q = ureg()(expr)
            if hasattr(q, "units"):
                return scale * float(q.magnitude), q.units
        except Exception:
            pass
    return scale, None


def norm_symbol(sym: str) -> str:
    """T_{e}, $T_e$, \\mathrm{T_e}, kT_e -> "te"; n_e -> "ne"; \\phi_p -> "phip"; I_{sp} -> "isp"."""
    s = unicodedata.normalize("NFKC", str(sym or "")).lower()
    s = re.sub(r"\\(mathrm|text|mathit|rm|it|bf|mathbf|left|right)\b", "", s)
    s = s.replace("\\", "")
    s = re.sub(r"[^a-z0-9α-ω]", "", s)
    if re.fullmatch(r"k(t[a-z]{0,3})", s):
        s = s[1:]
    return s[:20]


def is_temperature_like(name: str = "", symbol: str = "") -> bool:
    if "temperature" in (name or "").lower():
        return True
    return bool(re.fullmatch(r"t[a-z]{0,3}", norm_symbol(symbol))) and norm_symbol(symbol) not in ("t", "ts", "tw")


def _to_canonical(r, mag: float, un, temp_like: bool):
    q = r.Quantity(mag, un)
    if temp_like and q.dimensionality == r.Quantity(1, "eV").dimensionality:
        return (q / r.boltzmann_constant).to("K")
    if q.dimensionality == r.Quantity(1, "K").dimensionality:
        return q.to("K")
    for cand in PREFERRED:
        try:
            if r.Quantity(1, cand).dimensionality == q.dimensionality:
                return q.to(cand)
        except Exception:
            continue
    return q.to_base_units()


def canonical(value, unit: str, name: str = "", symbol: str = "", uncertainty=None) -> dict | None:
    """{"value", "uncertainty", "unit", "dim"} in SI (or None when value or unit can't be read).
    `dim` is the pint dimensionality as text, used to tell whether two quantities can be compared."""
    v = parse_number(value)
    if v is None:
        return None
    unc = parse_number(uncertainty) if uncertainty not in (None, "") else None
    scale, un = parse_unit(unit)
    if un is None:
        return None
    r = ureg()
    temp_like = is_temperature_like(name, symbol)
    try:
        c = _to_canonical(r, v * scale, un, temp_like)
        mag = float(c.magnitude)
        cu = None
        if unc is not None:  # through the same conversion (works for offset units such as degC too)
            cu = abs(float(_to_canonical(r, (v + abs(unc)) * scale, un, temp_like).magnitude) - mag)
        if not math.isfinite(mag) or (cu is not None and not math.isfinite(cu)):
            return None
        return {"value": mag, "uncertainty": cu, "unit": unit_text(c.units), "dim": str(c.dimensionality)}
    except Exception:
        return None


def unit_text(u) -> str:
    try:
        s = f"{u:~P}"
    except Exception:
        s = str(u)
    return s or "1"


def same_dim(a: dict | None, b: dict | None) -> bool:
    return bool(a and b and a.get("dim") == b.get("dim"))


def disagree(a: dict, b: dict, rel: float = 0.30) -> bool:
    """Two canonical values disagree beyond their combined uncertainty (missing ones count as 0), or, when
    neither has an uncertainty, by more than `rel` of the larger magnitude."""
    va, vb = float(a["value"]), float(b["value"])
    ua, ub = a.get("uncertainty"), b.get("uncertainty")
    if ua is not None or ub is not None:
        return abs(va - vb) > math.hypot(ua or 0.0, ub or 0.0)
    big = max(abs(va), abs(vb))
    return big > 0 and abs(va - vb) > rel * big


def fmt(v: float | None) -> str:
    if v is None:
        return ""
    if v == 0:
        return "0"
    a = abs(v)
    return f"{v:.3g}" if 1e-3 <= a < 1e5 else f"{v:.3e}".replace("e+0", "e").replace("e+", "e").replace("e-0", "e-")


# ---------------------------------------------------------------- conditions
_COND_PARAM = re.compile(r"([-+]?\d+(?:\.\d+)?(?:\s*[eE][-+]?\d+)?)\s*([A-Za-zµΩ°%][A-Za-z0-9µΩ°%^/\-*]{0,12})")
_WORD = re.compile(r"[a-zα-ω][a-zα-ω\-]{2,}")
_STOP = {"the", "and", "with", "for", "from", "at", "of", "in", "on", "under", "using", "was", "were", "measured",
         "conditions", "condition", "operating", "operation", "point", "approximately", "about", "near"}


def condition_params(text: str) -> list[dict]:
    """Numbers with units in a conditions string, e.g. "300 V, 5 mg/s xenon" -> two canonical values."""
    out = []
    for m in _COND_PARAM.finditer(text or ""):
        c = canonical(m.group(1), m.group(2))
        if c and c["dim"] != "dimensionless":
            out.append(c)
    return out[:8]


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def comparable_conditions(a: str, b: str) -> bool:
    """Whether two papers' conditions are close enough that their values should agree.
    Not comparable: a parameter of the same kind (e.g. a voltage) that differs by more than 20%, or two
    descriptions with nothing in common. Comparable: either side says nothing, or they share words, or a
    shared parameter agrees."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return True
    pa, pb = condition_params(a), condition_params(b)
    shared_agree = False
    for x in pa:
        same = [y for y in pb if y["dim"] == x["dim"]]
        if not same:
            continue
        if any(abs(x["value"] - y["value"]) <= 0.2 * max(abs(x["value"]), abs(y["value"]), 1e-300) for y in same):
            shared_agree = True
        else:
            return False
    wa, wb = _words(a), _words(b)
    jac = len(wa & wb) / len(wa | wb) if wa | wb else 0.0
    return shared_agree or jac >= 0.2 or (not wa and not wb)
