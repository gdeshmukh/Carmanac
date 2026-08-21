"""Mapper for the per-variant engine/transmission tables inside model
articles (ADR 0020 amendment).

A table row is a claim: "the variant with these years, this displacement,
this fuel came with this engine (and this gearbox), making this much power."
This module reduces wikitable markup to those claims and nothing else -
anchoring rows to configurations, minting, and assertion all live in the
pass. Trim/variant name columns are deliberately never read: the census
showed trim agreement resolves nothing (our filings say "AWD" where the
table says "T5").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from carmanac.reconcile.sources.wikipedia_infobox import _COMMENT, _REF

# Table boundaries are line-anchored; nested tables (rare layout tricks)
# stay inside their parent's body and are not descended into.
_TABLE_OPEN = re.compile(r"^[ \t]*\{\|", re.MULTILINE)

_LINK = re.compile(r"\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|[^\]]*)?\]\]")
_CONVERT = re.compile(r"\{\{\s*(?:convert|cvt|cvrt)\s*\|([^{}]*)\}\}", re.IGNORECASE)
_YEAR_SPAN = re.compile(
    r"\b(\d{4})\s*(?:[–—−-]|&ndash;)\s*(\d{4}|present|current|date)", re.IGNORECASE
)
_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")

# Bare-number value shapes, the non-{{convert}} half of each unit family.
_CC = re.compile(r"([\d,]{3,6})\s*cc\b", re.IGNORECASE)
_LITRES = re.compile(r"\b(\d\.\d)\s*(?:L|litre|liter)\b")
_POWER = re.compile(r"([\d,]+(?:\.\d+)?)\s*(hp|bhp|PS|kW)\b")
_TORQUE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(N[⋅·&sdot;]?m|Nm|lb[⋅·]?[\s-]?ft|lbft|ft[⋅·]?[\s-]?lbf?)\b",
    re.IGNORECASE,
)

_POWER_FACTORS = {"hp": 1.0, "bhp": 1.0, "ps": 0.9863, "kw": 1.34102}
_TORQUE_FACTORS = {"nm": 1.0, "n⋅m": 1.0, "n·m": 1.0, "lbft": 1.35582}

_FUEL_WORDS = re.compile(r"\b(petrol|gasoline|diesel)\b", re.IGNORECASE)

# What a header cell means. Order matters: "engine displacement" must read
# as displacement, not engine; "engine code" is still the engine column.
_ROLES = (
    ("displacement", re.compile(r"displacement|capacity|^cc$")),
    ("years", re.compile(r"year|production|availability|period|produced")),
    ("power", re.compile(r"power|output")),
    ("torque", re.compile(r"torque")),
    ("transmission", re.compile(r"transmission|gearbox")),
    ("fuel", re.compile(r"^fuel")),
    ("engine", re.compile(r"engine|^code|designation|^motor")),
)


def family_key(title: str) -> str:
    """The dedup key for a family-article title: casefolded, underscores to
    spaces, ONE trailing ' engine'/' transmission' stripped - 'Mercedes-Benz
    OM642' and 'Mercedes-Benz OM642 engine' are one family, one row."""
    key = re.sub(r"\s+", " ", title.replace("_", " ")).strip().casefold()
    for tail in (" engine", " transmission"):
        if key.endswith(tail):
            return key[: -len(tail)]
    return key


@dataclass(frozen=True)
class EngineRow:
    """One table row's claim, physical keys only. `titles` keeps the
    first-seen article title per family key - entity names wear the page's
    own case, the key is only identity."""

    engines: tuple[tuple[str, str | None], ...]  # (family key, variant code)
    transmissions: tuple[tuple[str, str | None], ...]
    titles: tuple[tuple[str, str], ...]  # (family key, article title)
    years: tuple[int, int | None] | None  # None end = open
    displacement_cc: int | None
    fuel: str | None  # 'petrol' | 'diesel'
    power_hp: int | None
    power_observed: str | None
    torque_nm: int | None
    torque_observed: str | None


@dataclass
class _Table:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    row_fuels: list[str | None] = field(default_factory=list)


def _clean(cell: str) -> str:
    return _COMMENT.sub("", _REF.sub("", cell)).strip()


def _split_cells(line: str, sep: str) -> list[str]:
    """Cells on one line, respecting template/link nesting - `||` inside
    `{{convert|110|kW||hp}}` is arguments, not a cell break."""
    cells: list[str] = []
    depth = 0
    current: list[str] = []
    i = 0
    while i < len(line):
        two = line[i : i + 2]
        if two in ("{{", "[["):
            depth += 1
            current.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            current.append(two)
            i += 2
            continue
        if depth == 0 and two == sep:
            cells.append("".join(current))
            current = []
            i += 2
            continue
        current.append(line[i])
        i += 1
    cells.append("".join(current))
    return cells


def _cell_value(cell: str) -> tuple[str, int, int]:
    """(text, colspan, rowspan). Attributes ride before a lone `|`:
    `rowspan=3 style="..." | B5254T3`."""
    colspan = rowspan = 1
    if "|" in cell:
        attrs, _, rest = cell.partition("|")
        # Attribute-shaped only: an `=` with no wiki markup before the pipe.
        if "=" in attrs and "[[" not in attrs and "{{" not in attrs:
            cell = rest
            m = re.search(r"colspan\s*=\s*\"?(\d+)", attrs)
            if m:
                colspan = int(m.group(1))
            m = re.search(r"rowspan\s*=\s*\"?(\d+)", attrs)
            if m:
                rowspan = int(m.group(1))
    return cell.strip(), colspan, rowspan


def _parse_table(body: str) -> _Table:
    """Wikitable markup to an aligned grid.

    Cells accumulate per row until the `|-` break - headers arrive both as
    `! A !! B !! C` and one `! A` per line. Rowspans carry their value down
    (engine names routinely span several year rows). A banner row - every
    laid cell identical, the 'Petrol engines' colspan idiom, whether marked
    header or data - becomes fuel context for the rows under it. Rows whose
    laid width disagrees with the header row are dropped, never guessed
    into columns."""
    table = _Table()
    pending: dict[int, tuple[str, int]] = {}  # column -> (value, rows left)
    fuel_context: str | None = None
    row_cells: list[tuple[str, bool]] = []  # (raw cell, is_header)

    def emit() -> None:
        nonlocal row_cells, fuel_context
        if not row_cells:
            return
        header_row = all(is_header for _, is_header in row_cells)
        laid: list[str] = []

        def fill_pending() -> None:
            col = len(laid)
            while col in pending:
                value, left = pending[col]
                laid.append(value)
                if left <= 1:
                    del pending[col]
                else:
                    pending[col] = (value, left - 1)
                col += 1

        fill_pending()
        for raw, _is_header in row_cells:
            value, colspan, rowspan = _cell_value(raw)
            value = _clean(value)
            for _ in range(colspan):
                col = len(laid)
                laid.append(value)
                if rowspan > 1:
                    pending[col] = (value, rowspan - 1)
            fill_pending()
        row_cells = []

        if len(set(laid)) == 1 and (len(laid) > 1 or not header_row or not table.headers):
            hit = _FUEL_WORDS.search(laid[0])
            if hit:
                fuel_context = (
                    "petrol" if hit.group(1).lower() in ("petrol", "gasoline") else "diesel"
                )
            return
        if header_row:
            if not table.headers:
                table.headers = laid
            return  # a later header row (units, group labels) never re-grids
        if table.headers and len(laid) == len(table.headers):
            table.rows.append(laid)
            table.row_fuels.append(fuel_context)

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("{|", "|+")):
            continue
        if stripped.startswith("|}"):
            break
        if stripped.startswith("|-"):
            emit()
            continue
        if stripped.startswith("!"):
            row_cells.extend((cell, True) for cell in _split_cells(stripped[1:], "!!"))
            continue
        if stripped.startswith("|"):
            row_cells.extend((cell, False) for cell in _split_cells(stripped[1:], "||"))
            continue
        if row_cells:
            # Continuation of a multi-line cell value.
            raw, is_header = row_cells[-1]
            row_cells[-1] = (raw + "\n" + line, is_header)
    emit()
    return table


def _extract_tables(wikitext: str) -> list[str]:
    out: list[str] = []
    for m in _TABLE_OPEN.finditer(wikitext):
        depth = 0
        i = m.start()
        lines = wikitext[i:].splitlines(keepends=True)
        taken: list[str] = []
        for line in lines:
            if line.lstrip().startswith("{|"):
                depth += 1
            taken.append(line)
            if line.lstrip().startswith("|}"):
                depth -= 1
                if depth == 0:
                    break
        if depth == 0 and taken:
            out.append("".join(taken))
    return out


def _norm_header(cell: str) -> str:
    text = _CONVERT.sub(" ", cell)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    return re.sub(r"\s+", " ", text.replace("&nbsp;", " ")).strip().casefold()


def _roles(headers: list[str]) -> dict[int, str]:
    roles: dict[int, str] = {}
    for i, header in enumerate(headers):
        text = _norm_header(header)
        for role, pattern in _ROLES:
            if pattern.search(text):
                roles[i] = role
                break
    return roles


def _convert_value(text: str, factors: dict[str, float]) -> int | None:
    for hit in _CONVERT.finditer(text):
        positional = [a.strip() for a in hit.group(1).split("|") if "=" not in a]
        if not positional or not re.fullmatch(r"[\d,]+(?:\.\d+)?", positional[0]):
            continue
        unit = next(
            (a for a in positional[1:] if not re.fullmatch(r"[\d,.]+|-|–", a)), ""
        ).casefold()
        factor = factors.get(unit)
        if factor is not None:
            return round(float(positional[0].replace(",", "")) * factor)
    return None


def _power(text: str) -> int | None:
    value = _convert_value(text, _POWER_FACTORS)
    if value is not None:
        return value
    m = _POWER.search(_CONVERT.sub(" ", text))
    if m:
        return round(float(m.group(1).replace(",", "")) * _POWER_FACTORS[m.group(2).casefold()])
    return None


def _torque(text: str) -> int | None:
    value = _convert_value(
        text,
        {
            "n.m": 1.0,  # the convert template's own code for newton-metres
            "n⋅m": 1.0,
            "n·m": 1.0,
            "nm": 1.0,
            "lb.ft": 1.35582,
            "lbft": 1.35582,
            "lb·ft": 1.35582,
            "lb⋅ft": 1.35582,
        },
    )
    if value is not None:
        return value
    m = _TORQUE.search(_CONVERT.sub(" ", text))
    if m:
        unit = re.sub(r"[⋅·\s&sdot;-]", "", m.group(2)).casefold()
        factor = 1.0 if unit.startswith("n") else 1.35582
        return round(float(m.group(1).replace(",", "")) * factor)
    return None


def _displacement(text: str) -> int | None:
    value = _convert_value(text, {"cc": 1.0, "l": 1000.0, "litre": 1000.0, "liter": 1000.0})
    if value is not None and 250 <= value <= 14000:
        return value
    plain = _CONVERT.sub(" ", text)
    m = _CC.search(plain)
    if m:
        value = int(m.group(1).replace(",", ""))
        return value if 250 <= value <= 14000 else None
    m = _LITRES.search(plain)
    if m:
        return round(float(m.group(1)) * 1000)
    return None


def _years(text: str) -> tuple[int, int | None] | None:
    """The row's availability hull. A hull is legal here because it is a
    MATCHING key, never an asserted fact - a variant sold 2007-2010 and
    2012-2014 should still catch a 2013 filing."""
    spans = _YEAR_SPAN.findall(text)
    if spans:
        starts = [int(a) for a, _ in spans]
        ends = [None if not b.isdigit() else int(b) for _, b in spans]
        return min(starts), None if None in ends else max(e for e in ends if e is not None)
    years = [int(y) for y in _YEAR.findall(text)]
    if years:
        return min(years), max(years)
    return None


def _link_keys(text: str) -> tuple[tuple[str, str | None, str], ...]:
    keys: list[tuple[str, str | None, str]] = []
    for target, anchor in _LINK.findall(text):
        target = target.strip()
        if not target or target.startswith(("File:", "Image:", "wikt:")):
            continue
        code = anchor.replace("_", " ").strip() or None if anchor else None
        triple = (family_key(target), code, target.replace("_", " "))
        if triple[:2] not in [k[:2] for k in keys]:
            keys.append(triple)
    return tuple(keys)


def parse_engine_tables(wikitext: str) -> list[EngineRow]:
    """Every claim row from every engine/transmission table in the article.
    A table qualifies when a column names engines or transmissions AND one
    names displacement, power, or years - the census's 'model+engine table'
    shape; sales, safety, and rally-results tables never qualify."""
    rows: list[EngineRow] = []
    for body in _extract_tables(wikitext):
        table = _parse_table(body)
        roles = _roles(table.headers)
        have = set(roles.values())
        if not ({"engine", "transmission"} & have) or not (
            {"displacement", "power", "years"} & have
        ):
            continue
        for cells, row_fuel in zip(table.rows, table.row_fuels, strict=True):
            engines: list[tuple[str, str | None]] = []
            transmissions: list[tuple[str, str | None]] = []
            titles: dict[str, str] = {}
            years = displacement = power = torque = None
            power_observed = torque_observed = None
            fuel = row_fuel
            for i, cell in enumerate(cells):
                role = roles.get(i)
                if role in ("engine", "transmission"):
                    bucket = engines if role == "engine" else transmissions
                    for key, code, title in _link_keys(cell):
                        if (key, code) not in bucket:
                            bucket.append((key, code))
                        titles.setdefault(key, title)
                elif role == "years" and years is None:
                    years = _years(cell)
                elif role == "displacement" and displacement is None:
                    displacement = _displacement(cell)
                elif role == "power" and power is None:
                    power = _power(cell)
                    if power is not None:
                        power_observed = " ".join(cell.split())[:120]
                elif role == "torque" and torque is None:
                    torque = _torque(cell)
                    if torque is not None:
                        torque_observed = " ".join(cell.split())[:120]
                elif role == "fuel" and fuel is None:
                    hit = _FUEL_WORDS.search(cell)
                    if hit:
                        fuel = (
                            "petrol" if hit.group(1).lower() in ("petrol", "gasoline") else "diesel"
                        )
            if engines or transmissions:
                rows.append(
                    EngineRow(
                        engines=tuple(engines),
                        transmissions=tuple(transmissions),
                        titles=tuple(titles.items()),
                        years=years,
                        displacement_cc=displacement,
                        fuel=fuel,
                        power_hp=power,
                        power_observed=power_observed,
                        torque_nm=torque,
                        torque_observed=torque_observed,
                    )
                )
    return rows
