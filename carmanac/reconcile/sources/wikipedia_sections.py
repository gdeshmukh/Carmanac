"""Mapper for landed `article:<QID>` wikitext records (ADR 0017 §4).

A nameplate article's per-generation sections follow a cross-marque
convention - `== First generation (XW10; 1997) ==`, each section carrying
its own infobox - and this module reads exactly that shape. The heading
grammar is strict on purpose: `Second generation models` and prose headings
that merely mention the word must never look like a generation, because a
misread section either mints a phantom or (worse) hides a real competitor
from the placement guards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from carmanac.reconcile.sources.wikipedia_infobox import (
    _COMMENT,
    _REF,
    infobox_field,
    same_subject,
    title_code_tokens,
)

_HEADING = re.compile(r"^(={2,4})\s*(.+?)\s*\1\s*$", re.MULTILINE)

# Cleanup for heading text: maintained anchors, templates ({{anchor|..}},
# {{Update inline|..}}), refs, italics. Templates strip iteratively because
# they nest.
_ANCHOR_SPAN = re.compile(r"</?span[^>]*>")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_ITALICS = re.compile(r"'{2,}")

# Ordinal words as they actually appear in headings. The probe's maximum is
# Toyota Crown's "Sixteenth"; the table runs to thirtieth so a long-lived
# nameplate cannot silently drop a section (a dropped section is an unlinked
# competitor the placement guards cannot see).
ORDINAL_WORDS: list[str] = (  # noqa: SIM905 - positional: index+1 is the ordinal
    "first second third fourth fifth sixth seventh eighth ninth tenth "
    "eleventh twelfth thirteenth fourteenth fifteenth sixteenth "
    "seventeenth eighteenth nineteenth twentieth twenty-first "
    "twenty-second twenty-third twenty-fourth twenty-fifth twenty-sixth "
    "twenty-seventh twenty-eighth twenty-ninth thirtieth"
).split()
_ORDINAL_INDEX = {word: i + 1 for i, word in enumerate(ORDINAL_WORDS)}
_ORDINAL_NUM = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)$", re.IGNORECASE)

# `<ordinal> generation` plus nothing, a dash code, or one parenthetical.
# Anything else ("... models", "... facelift") is a sub-part, not a
# generation.
_GEN_HEADING = re.compile(
    r"^(?P<ord>[A-Za-z]+(?:-[A-Za-z]+)?|\d{1,2}(?:st|nd|rd|th))\s+generation"
    r"(?P<rest>.*)$",
    re.IGNORECASE,
)
_REST = re.compile(r"^\s*(?:[–—-]\s*(?P<dash>[^()]{1,40}?))?\s*(?:\((?P<paren>[^()]*)\))?\s*$")

_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")

# A chassis code inside the heading's `(CODES; YEAR)` convention. Position
# carries the confidence the label extractor lacks (ADR 0017 §4), so
# letters-only codes are legal here - but only ALL-UPPERCASE and short
# (GD, GE, NA, SF), which prose words are not. Tokens with digits may mix
# case (Mk4, 4L, XW10, S230).
_CODE_ALPHA = re.compile(r"^[A-Z]{1,4}$")
_CODE_MIXED = re.compile(r"^[A-Z0-9][A-Za-z0-9-]{0,7}$")
_TYP_PREFIX = re.compile(r"^Typ?e?\s+", re.IGNORECASE)

_MAIN_TEMPLATE = re.compile(r"\{\{\s*[Mm]ain(?:\s+article)?\s*\|([^{}]*)\}\}")
_INFOBOX_START = re.compile(r"\{\{\s*Infobox\s+(?:automobile|car)\b", re.IGNORECASE)

_DOOR_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_DOORS = re.compile(r"\b(2|3|4|5|6|two|three|four|five|six)[-\s]doors?\b", re.IGNORECASE)

# Trim-string body words that imply a low door count. Deliberately one-sided:
# "sedan" is absent because 2-door sedans are real, and nothing here may
# guess a minimum.
_LOW_DOOR_TRIM = re.compile(
    r"\b(coupe|coupé|roadster|convertible|cabriolet|targa|spyder|spider)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class GenerationSection:
    ordinal: int
    heading: str  # cleaned heading text
    codes: tuple[str, ...]
    heading_years: tuple[int, ...]  # detail only - never a span (ADR 0017 §4)
    main_targets: tuple[str, ...]
    has_infobox: bool
    body: str  # raw section wikitext, for infobox fields at decision time


@dataclass(frozen=True)
class ParsedArticle:
    title: str
    sections: tuple[GenerationSection, ...]
    top_wikitext: str  # before the first heading: the nameplate's own infobox


def clean_heading(raw: str) -> str:
    text = _ANCHOR_SPAN.sub(" ", raw)
    for _ in range(3):
        stripped = _TEMPLATE.sub(" ", text)
        if stripped == text:
            break
        text = stripped
    text = _REF.sub(" ", text)
    text = _ITALICS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _ordinal(token: str) -> int | None:
    m = _ORDINAL_NUM.match(token)
    if m:
        return int(m.group(1))
    return _ORDINAL_INDEX.get(token.lower())


def _codes_and_years(
    dash: str | None, paren: str | None
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    codes: list[str] = []
    years: list[int] = []
    for group in (dash, paren):
        if not group:
            continue
        years.extend(int(y) for y in _YEAR.findall(group))
        # `CODES; YEAR`: everything left of the first `;` is the code list;
        # with no `;` the whole group is tried, and year-shaped tokens simply
        # fail code validation.
        code_part = group.split(";")[0]
        for token in re.split(r"[/,]", code_part):
            token = _TYP_PREFIX.sub("", token.strip())
            if not token or _YEAR.fullmatch(token):
                continue
            valid = _CODE_ALPHA.match(token) or (
                _CODE_MIXED.match(token) and any(ch.isdigit() for ch in token)
            )
            if valid and token not in codes:
                codes.append(token)
    return tuple(codes), tuple(years)


def parse_heading(raw: str) -> GenerationSection | None:
    """The strict grammar. None for anything that is not `<ordinal>
    generation` + (nothing | dash code | one parenthetical)."""
    cleaned = clean_heading(raw)
    m = _GEN_HEADING.match(cleaned)
    if not m:
        return None
    ordinal = _ordinal(m.group("ord"))
    if ordinal is None:
        return None
    rest = _REST.match(m.group("rest"))
    if not rest:
        return None
    codes, years = _codes_and_years(rest.group("dash"), rest.group("paren"))
    return GenerationSection(
        ordinal=ordinal,
        heading=cleaned,
        codes=codes,
        heading_years=years,
        main_targets=(),
        has_infobox=False,
        body="",
    )


def section_main_asserts(payload: dict) -> bool:
    """The grain guards on a landed `section-main:` record (ADR 0018 §3):
    a redirected target asserts nothing, and so does a bare-title target -
    per-generation articles carry a trailing parenthetical (`Mazda MX-5
    (NA)`); a bare title is a nameplate/rebadge deferral speaking at the
    wrong grain."""
    resolved = payload.get("title", "")
    if not same_subject(payload.get("requested_title", ""), resolved):
        return False
    return title_code_tokens(resolved) is not None


def parse_article(title: str, wikitext: str) -> ParsedArticle:
    matches = list(_HEADING.finditer(wikitext))
    top = wikitext[: matches[0].start()] if matches else wikitext
    sections: list[GenerationSection] = []
    for i, m in enumerate(matches):
        parsed = parse_heading(m.group(2))
        if parsed is None:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(wikitext)
        body = wikitext[m.end() : end]
        mains = tuple(
            part.strip()
            for hit in _MAIN_TEMPLATE.findall(body[:800])
            for part in hit.split("|")
            if part.strip()
        )
        sections.append(
            GenerationSection(
                ordinal=parsed.ordinal,
                heading=parsed.heading,
                codes=parsed.codes,
                heading_years=parsed.heading_years,
                main_targets=mains,
                has_infobox=bool(_INFOBOX_START.search(body)),
                body=body,
            )
        )
    return ParsedArticle(title=title, sections=tuple(sections), top_wikitext=top)


def door_counts(body_style_raw: str) -> frozenset[int]:
    """Explicit door counts in an infobox `body_style` value. Body words
    without a count ('roadster') contribute nothing - the veto is
    door-count-explicit only (ADR 0017 §4)."""
    cleaned = _COMMENT.sub(" ", _REF.sub(" ", body_style_raw))
    counts = set()
    for token in _DOORS.findall(cleaned):
        count = _DOOR_WORDS.get(token.lower()) or (int(token) if token.isdigit() else None)
        counts.add(count)
    return frozenset(c for c in counts if c)


def generation_doors(*chunks: str | None) -> frozenset[int]:
    """Door counts a generation's bodies carry, read from the first chunk
    whose infobox asserts any. Callers order chunks most-specific-claim
    first: the section's own body, then its fetched `{{Main}}` target
    (ADR 0018 §3), then the article's top infobox - a nameplate-scope claim
    covers every generation in the article (ADR 0017 §4)."""
    for chunk in chunks:
        if not chunk:
            continue
        raw = infobox_field(chunk, "body style") or infobox_field(chunk, "body_style")
        if raw:
            doors = door_counts(raw)
            if doors:
                return doors
    return frozenset()


@dataclass(frozen=True)
class BodySignal:
    """What a configuration's raw evidence says about its doors, as a
    constraint - never a guess. `min_doors`/`max_doors` bound what the body
    could be; None on both sides means no signal."""

    min_doors: int | None = None
    max_doors: int | None = None

    def __bool__(self) -> bool:
        return self.min_doors is not None or self.max_doors is not None

    def contradicts(self, doors: frozenset[int]) -> bool:
        """True when EVERY door count the generation asserts falls outside
        this constraint - a mixed-body generation is compatible with any
        signal that at least one of its bodies satisfies."""
        if not doors:
            return False
        if self.min_doors is not None and max(doors) < self.min_doors:
            return True
        return self.max_doors is not None and min(doors) > self.max_doors


def epa_body_signal(
    vclass: str | None, pv2: str | None, pv4: str | None, lv2: str | None, lv4: str | None
) -> BodySignal:
    """The EPA raw record's body signal (ADR 0017 §4). 'Two Seaters' is a
    seat-count class, mutually exclusive with every other car class; the
    volume fields are filled exactly when EPA measured that body. Wagon/SUV/
    van classes deliberately assert nothing - EPA reclassed the 2025 GT
    4-Door as a station wagon, and a class that can drift must not veto its
    own car."""

    def _filled(value: str | None) -> bool:
        try:
            return float(value or 0) > 0
        except ValueError:
            return False

    four_door = _filled(pv4) or _filled(lv4)
    two_door = _filled(pv2) or _filled(lv2)
    if four_door and two_door:
        return BodySignal()  # the record contradicts itself: no signal
    if four_door:
        return BodySignal(min_doors=4)
    if two_door or (vclass or "").strip().lower() == "two seaters":
        return BodySignal(max_doors=3)
    return BodySignal()


def trim_body_signal(trim_name: str | None) -> BodySignal:
    """Fallback when no EPA record is attached: explicit body words in the
    trim string. One-sided by design - see _LOW_DOOR_TRIM."""
    if trim_name and _LOW_DOOR_TRIM.search(trim_name):
        return BodySignal(max_doors=3)
    return BodySignal()
