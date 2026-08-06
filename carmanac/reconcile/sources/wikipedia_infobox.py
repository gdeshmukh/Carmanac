"""Mapper for landed `infobox:<QID>` wikitext records (ADR 0017 §2).

Parsing is deliberately narrow: the pass wants two year spans (`production`,
`model_years`) and the title's code parenthetical - not a wikitext AST. The
rule everywhere is flag-never-guess: a field that does not reduce to exactly
one span asserts nothing and surfaces for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A value line belongs to its field until the next top-level `|field=` or the
# template's closing braces. Section-0 wikitext nests templates inside values
# ({{Start date|1982}}, {{ubl|...}}), so field starts are recognised only at
# line heads - nested pipes stay inside the captured value.
_FIELD_LINE = re.compile(r"^\s*\|\s*([a-zA-Z_ ]+?)\s*=\s*(.*)$")

_REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Range endpoints are routinely dated to the month ("June 1953–July 1962",
# "October 1, 1996 – July 2, 2004"); the optional prefix keeps those parsing
# while the year stays the only captured part.
_DATEISH = (
    r"(?:(?:\d{1,2}\s+)?"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?)?"
)
_YEAR_RANGE = re.compile(
    rf"\b{_DATEISH}(\d{{4}})\s*(?:[–—−-]|&ndash;|&mdash;)\s*{_DATEISH}(\d{{4}}|present|current|date)\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_TITLE_PARENTHETICAL = re.compile(r"\(([^()]*)\)\s*$")


@dataclass(frozen=True)
class Span:
    start: int
    end: int | None  # None = present/ongoing

    def contains(self, year_start: int, year_end: int | None, *, end_slack: int = 0) -> bool:
        """Whole-period containment, closed start, `end_slack` extra years on
        the end (the model-year-outruns-production allowance, ADR 0017 §3)."""
        if year_start < self.start:
            return False
        if self.end is None:
            return True
        upper = self.end + end_slack
        return (year_end if year_end is not None else year_start) <= upper


@dataclass(frozen=True)
class ParsedInfobox:
    title: str
    production: Span | None = None
    model_years: Span | None = None
    # (field, reason, raw value) for every field that had years but did not
    # reduce to one span - the flag payload.
    failures: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)


def infobox_field(wikitext: str, name: str) -> str | None:
    """The raw multi-line value of `|name=` in the article's lead wikitext."""
    lines = wikitext.splitlines()
    value: list[str] | None = None
    for line in lines:
        m = _FIELD_LINE.match(line)
        if m:
            if value is not None:
                break
            if m.group(1).strip().lower() == name:
                value = [m.group(2)]
            continue
        if value is not None:
            if line.strip().startswith("}}"):
                break
            value.append(line)
    if value is None:
        return None
    return "\n".join(value).strip() or None


def parse_span(raw: str) -> tuple[Span | None, str | None]:
    """(span, failure_reason). Exactly one range - or one bare year - parses;
    anything else is the reviewer's problem, not a guess."""
    cleaned = _COMMENT.sub(" ", _REF.sub(" ", raw))
    ranges = _YEAR_RANGE.findall(cleaned)
    if len(ranges) == 1:
        start_s, end_s = ranges[0]
        end = int(end_s) if end_s.isdigit() else None
        if end is not None and end < int(start_s):
            return None, "end_before_start"
        return Span(int(start_s), end), None
    if len(ranges) > 1:
        distinct = {(s, e.lower() if not e.isdigit() else e) for s, e in ranges}
        if len(distinct) == 1:
            start_s, end_s = ranges[0]
            end = int(end_s) if end_s.isdigit() else None
            return Span(int(start_s), end), None
        return None, "multiple_ranges"
    years = _YEAR.findall(cleaned)
    if len(set(years)) == 1:
        year = int(years[0])
        return Span(year, year), None
    if years:
        return None, "years_without_range"
    return None, None  # no years at all: nothing to assert, nothing to flag


def parse_infobox(title: str, wikitext: str) -> ParsedInfobox:
    production = model_years = None
    failures: list[tuple[str, str, str]] = []
    for name in ("production", "model years"):
        raw = infobox_field(wikitext, name) or (
            infobox_field(wikitext, "model_years") if name == "model years" else None
        )
        if raw is None:
            continue
        span, reason = parse_span(raw)
        if span is None and reason is not None:
            failures.append((name, reason, raw[:500]))
        elif span is not None:
            if name == "production":
                production = span
            else:
                model_years = span
    return ParsedInfobox(
        title=title,
        production=production,
        model_years=model_years,
        failures=tuple(failures),
    )


def title_code_tokens(title: str) -> str | None:
    """The title's trailing parenthetical - `BMW 3 Series (E30)` -> `E30` -
    for the shared chassis-code extractor. Prose parentheticals ('fourth
    generation') go through the same strict pattern and assert nothing."""
    m = _TITLE_PARENTHETICAL.search(title)
    return m.group(1) if m else None
