"""Wikidata models-sweep mapper: one landed binding -> a `ModelEntity`.

Pure translation, no database access - the sibling of sources/wikidata.py for
the models sweep (ADR 0012). The pass consumes `ModelEntity` and decides
level per make; this module only answers what the record SAYS: its labels,
its structural edges (P176/P179/P361/P155/P156), its dates, and its classes.

Level is structural, never nominal (probed 2026-07-30): four entities share
the bare label "BMW 3 Series" across nameplate, generation, and series
levels, and P31 is `car model` for generation and nameplate alike - so
nothing here interprets labels or classes as a level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SOURCE_NAME = "Wikidata"

_ENTITY_PREFIX = "http://www.wikidata.org/entity/"
_SEPARATOR = "|"

_YEAR = re.compile(r"^(-?\d{1,4})")

# A chassis/development code, strictly: letters then digits, optionally more
# alphanumerics - E46, W205, XV70, N280, ZN6, AE86 - or Porsche's all-digit
# codes (964, 992), which must not swallow years (1994) or displacements
# shaped like years. Tokens that look code-ish but fail this (checked with
# the LOOSE pattern) are AMBIGUOUS: flagged, never guessed (ADR 0012 §5).
_CODE_STRICT = re.compile(r"^[A-Z]{1,4}\d{1,4}[A-Z0-9]*$")
_CODE_DIGITS = re.compile(r"^(?!19\d\d$)(?!20\d\d$)\d{3}$")
_CODE_LOOSE = re.compile(r"^[A-Za-z0-9/-]{2,8}$")

_PARENTHETICAL = re.compile(r"\(([^)]*)\)")


@dataclass(frozen=True)
class ModelEntity:
    """What one models-sweep record asserts, untranslated into any level."""

    qid: str
    label: str | None  # None: the label service found nothing anywhere
    description: str | None
    aliases: tuple[str, ...]
    classes: frozenset[str]
    makers: tuple[str, ...]  # P176 QIDs
    series_of: tuple[str, ...]  # P179 QIDs
    part_of: tuple[str, ...]  # P361 QIDs
    follows: tuple[str, ...]  # P155 QIDs
    followed_by: tuple[str, ...]  # P156 QIDs
    start_years: tuple[int, ...]  # P571 inception + P729 service entry
    end_years: tuple[int, ...]  # P576 dissolved + P730 retired + P2669 discontinued
    article: str | None  # enwiki sitelink URL, the Wikipedia-wave pointer


def _cell(payload: dict, var: str) -> str:
    cell = payload.get(var)
    return (cell.get("value") or "") if isinstance(cell, dict) else ""


def _split(payload: dict, var: str) -> list[str]:
    parts = [p for p in _cell(payload, var).split(_SEPARATOR) if p]
    return sorted(set(parts))


def _qids(payload: dict, var: str) -> tuple[str, ...]:
    return tuple(
        p.removeprefix(_ENTITY_PREFIX) for p in _split(payload, var) if p.startswith(_ENTITY_PREFIX)
    )


def _years(payload: dict, *variables: str) -> tuple[int, ...]:
    years: set[int] = set()
    for var in variables:
        for literal in _split(payload, var):
            m = _YEAR.match(literal)
            if m:
                years.add(int(m.group(1)))
    return tuple(sorted(years))


def map_record(payload: dict) -> ModelEntity | None:
    """Translate one models-sweep payload. None for a record with no QID."""
    item = _cell(payload, "item")
    if not item.startswith(_ENTITY_PREFIX):
        return None
    qid = item.removeprefix(_ENTITY_PREFIX)

    label = _cell(payload, "itemLabel")
    return ModelEntity(
        qid=qid,
        label=label if label and label != qid else None,
        description=_cell(payload, "itemDescription") or None,
        aliases=tuple(_split(payload, "aliases")),
        classes=frozenset(_qids(payload, "classes")),
        makers=_qids(payload, "makers"),
        series_of=_qids(payload, "seriesOf"),
        part_of=_qids(payload, "partsOf"),
        follows=_qids(payload, "followsIds"),
        followed_by=_qids(payload, "followedByIds"),
        start_years=_years(payload, "inceptions", "prodStarts"),
        end_years=_years(payload, "dissolutions", "prodEnds", "discontinueds"),
        article=_cell(payload, "article") or None,
    )


def strip_prefix(name: str, prefix_norm: str, normalize) -> str:
    """The make-prefix-stripped form (ADR 0012 §2.3): 'Toyota 4Runner' with
    company 'Toyota' -> '4Runner'. Mechanical: the NORMALIZED name must start
    with the normalized company name and keep a non-empty remainder; the cut
    happens in the raw string at the shortest prefix whose normalization
    matches, so the remainder keeps its own casing and punctuation."""
    name_norm = normalize(name)
    if not prefix_norm or not name_norm.startswith(prefix_norm) or name_norm == prefix_norm:
        return name
    for cut in range(1, len(name) + 1):
        if normalize(name[:cut]) == prefix_norm:
            return name[cut:].lstrip(" -:").strip() or name
    return name


def extract_chassis_codes(label: str | None, aliases: tuple[str, ...], stripped_label: str | None):
    """Chassis codes from label/alias parentheticals, plus the bare
    prefix-stripped label when it IS a code ('BMW E30' -> 'E30').

    Returns (codes, ambiguous): strictly-matching tokens, and code-ish tokens
    that failed the strict pattern. The caller flags ambiguity rather than
    guessing (ADR 0012 §5); prose-shaped tokens ('sedan') are neither.
    """
    codes: set[str] = set()
    ambiguous: set[str] = set()

    def consider(token: str) -> None:
        token = token.strip()
        if not token:
            return
        if _CODE_STRICT.match(token) or _CODE_DIGITS.match(token):
            codes.add(token)
        elif _CODE_LOOSE.match(token) and any(ch.isdigit() for ch in token):
            ambiguous.add(token)

    for text in (label, *aliases):
        if not text:
            continue
        for group in _PARENTHETICAL.findall(text):
            for token in re.split(r"[/,]", group):
                consider(token)

    if stripped_label and (
        _CODE_STRICT.match(stripped_label) or _CODE_DIGITS.match(stripped_label)
    ):
        codes.add(stripped_label)

    return sorted(codes), sorted(ambiguous)
