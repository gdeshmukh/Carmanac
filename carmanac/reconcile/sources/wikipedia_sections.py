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
    parse_span,
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
_INFOBOX_START = re.compile(
    r"\{\{\s*Infobox\s+(?:automobile|electric vehicle|car)\b", re.IGNORECASE
)

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


_ERA_HEADING_YEARS = re.compile(r"\b(?:19|20)\d\d\s*(?:[–—−-]|&ndash;)\s*(?:(?:19|20)\d\d|present)")
_H2 = re.compile(r"^==\s*([^=].*?)\s*==\s*$", re.MULTILINE)


def _infobox_bodies(wikitext: str) -> list[str]:
    """Each `{{Infobox automobile}}` template's own text, brace-balanced so a
    nested `{{convert}}` does not end it early."""
    bodies = []
    for match in _INFOBOX_START.finditer(wikitext):
        depth = 0
        i = match.start()
        while i < len(wikitext) - 1:
            if wikitext[i : i + 2] == "{{":
                depth += 1
                i += 2
                continue
            if wikitext[i : i + 2] == "}}":
                depth -= 1
                i += 2
                if depth == 0:
                    break
                continue
            i += 1
        bodies.append(wikitext[match.start() : i])
    return bodies


def looks_multi_era(wikitext: str) -> bool:
    """Does this article describe several eras this module's heading grammar
    could not read? Either signal is sufficient:

    - **Sequential production spans.** Two sub-infoboxes whose spans start at
      least two years apart are eras (`1984`, `1988`, `1998` on the M5's
      page). Counting infoboxes alone would not do: rebadge twins, market
      variants and body styles all carry their own box over the SAME span -
      the bZ4X's Solterra, the Arkana's China car, the Altea XL.
    - **Dated top-level headings.** `TT Mk1 (Type 8N, 1998-2006)`,
      `Rodeo 4 (1970-1981)` - conventions the strict grammar rejects, needing
      two to distinguish an era list from one `History (1948-1990)` heading.

    Absence of parsed sections is NOT evidence of a single era, and reading
    it as one mints a generation over a nameplate's whole life."""
    starts = set()
    for body in _infobox_bodies(wikitext):
        raw = infobox_field(body, "production")
        if raw:
            span, _reason = parse_span(raw)
            if span is not None:
                starts.add(span.start)
    if len(starts) >= 2 and max(starts) - min(starts) >= 2:
        return True
    return sum(1 for h in _H2.findall(wikitext) if _ERA_HEADING_YEARS.search(h)) >= 2


# --- the widened era grammar (ADR 0017 §4 amendment) --------------------------
#
# Marques that do not use the "<ordinal> generation" convention still list
# their eras in structured headings - `E28 M5 (1984-1988)`, `TT Mk1 (Type 8N,
# 1998-2006)`, `Commodore A (1967-1971)`. Reading them is a PRECISION problem,
# not a parsing one: `Pre-facelift release (2003-2008)` and `Bugatti Veyron
# 16.4 Grand Sport (2009-2015)` wear the identical shape and are a facelift
# and a trim. Censused over 817 landed articles, three conjuncts reach 99%
# precision where the shape alone reaches 71%:
#
#   1. the heading looks era-shaped (code-led, or ending in a dated
#      parenthetical),
#   2. no disqualifying word anywhere in it, and
#   3. THE SECTION CARRIES ITS OWN INFOBOX - the load-bearing gate. Of 33
#      censused trim sections and 14 facelift sections, none had one.
#
# Article-grain gates then run over the survivors, because a lone era-shaped
# heading is a variant far more often than a generation.

_ERA_STOPWORDS = re.compile(
    r"\b(?:face-?lift|restyl\w*|refresh\w*|updated?|updates|revision|phase|concept|prototype"
    r"|study|show\s+car|special|editions?|variants?|versions?|trims?|engines?|powertrains?"
    r"|drivetrain|transmission|chassis|suspension|brakes|interior|safety|awards?|sales"
    r"|production|marketing|reception|history|overview|background|development|design"
    r"|specifications?|gallery|legacy|timeline|recalls?|motorsports?|racing|rally|competition"
    r"|models?|range|line-?up|predecessor|successor|based\s+on|launch|name\s+reuse)\b",
    re.IGNORECASE,
)
# Whole-label matches only: a market or body word IS the heading, rather than
# appearing in it ("Japan (1970-1978)", "Hatchback (FK1-3; 2005)").
_ERA_WHOLE_LABEL = re.compile(
    r"^(?:(?:north|south)\s+america|united\s+states|usa?|canada|mexico|brazil|europe|japan|china"
    r"|australia|new\s+zealand|india|korea|south\s+africa|russia|uk|united\s+kingdom"
    r"|hatchback|sedan|saloon|coup[eé]|convertible|cabriolet|roadster|wagon|estate|pick-?up|van"
    r"|suv|liftback|fastback|targa|spyder|tourer)"
    r"(?:\s*(?:and|&|/|,)\s*.+)?$",
    re.IGNORECASE,
)
_ERA_PAREN = re.compile(r"^(?P<head>.*?)\s*\((?P<paren>[^()]*)\)\s*$")
_ERA_YEAR = re.compile(r"\b(?:18[5-9]\d|19\d\d|20[0-4]\d)\b")
_ERA_CODE = re.compile(r"^(?:[A-Z]{1,4}\d{1,4}[A-Z0-9]*|\d{1,2}[A-Z]{1,3}\d{0,2}|[A-Z]{2,4})$")
_ERA_ROMAN = re.compile(r"^(?:I{1,3}|IV|VI{0,3}|IX|XI{0,2}|X)$")
_ERA_LABEL = re.compile(r"^(?:Mk\.?\s?[IVX0-9]+|Mark\s+[IVX]+|Series\s+[IVX0-9]+)$", re.IGNORECASE)
_ANCHOR_ID = re.compile(
    r"\{\{\s*(?:visible\s+)?anchor\s*\|([^{}]*)\}\}|<span[^>]*\bid\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
# An article already at one generation's grain ("Porsche 911 (991)",
# "Honda Civic (eighth generation)") holds body styles under its headings,
# never eras.
_SUB_GRAIN_TITLE = re.compile(
    r"\((?:[A-Z0-9][A-Za-z0-9/.-]*|(?:" + "|".join(ORDINAL_WORDS) + r")\s+generation)\)\s*$",
    re.IGNORECASE,
)


def _anchor_codes(raw_heading: str) -> list[str]:
    """Maintained `{{anchor|E28}}` / `id="LA2"` values, filtered to code
    shapes - they name the era more cleanly than the prose does."""
    out = []
    for template, span_id in _ANCHOR_ID.findall(raw_heading):
        for value in (template or span_id or "").split("|"):
            value = value.strip()
            if value and _ERA_CODE.match(value) and not _ERA_ROMAN.match(value):
                out.append(value)
    return out


def _era_tokens(text: str, title_words: set[str]) -> tuple[list[str], list[str]]:
    """(codes, labels) among a heading fragment's tokens, dropping tokens the
    article title already contributes - the nameplate is not the era."""
    codes, labels = [], []
    for token in re.split(r"[\s,/]+", text.strip()):
        token = token.strip("-–—;:")
        if not token or token.casefold() in title_words:
            continue
        if token.casefold() in {"series", "type", "typ", "model", "models", "and", "the", "of"}:
            continue
        if _ERA_ROMAN.match(token):
            labels.append(token)
        elif _ERA_CODE.match(token) and not _ERA_YEAR.match(token):
            codes.append(token)
        elif _ERA_LABEL.match(token) or re.fullmatch(r"[A-Z]|\d{1,2}", token):
            labels.append(token)
    return codes, labels


def parse_era_heading(
    raw: str, title_words: set[str]
) -> tuple[str, tuple[str, ...], tuple[int, ...]] | None:
    """(name, codes, years) for a non-ordinal era heading, or None.

    Name follows the chassis-code-over-nameplate rule: the first code found
    in the head, then the parenthetical (after an optional `Typ`), then the
    maintained anchor; failing every code, the era's own label (`Mk1`,
    `Series I`, `A`). A heading yielding neither is not named and not read."""
    cleaned = clean_heading(raw)
    if not cleaned or _ERA_STOPWORDS.search(cleaned) or _ERA_WHOLE_LABEL.match(cleaned):
        return None
    match = _ERA_PAREN.match(cleaned)
    head = match.group("head") if match else cleaned
    paren = match.group("paren") if match else ""
    years = tuple(int(y) for y in _ERA_YEAR.findall(cleaned))
    if not years:
        return None
    if not head.strip():
        return None

    head_codes, head_labels = _era_tokens(head, title_words)
    # Everything left of the first `;`/`,` in the parenthetical is the code
    # list; the rest is years and prose.
    paren_codes, paren_labels = _era_tokens(re.split(r"[;,]", paren)[0], title_words)
    codes = head_codes + paren_codes + _anchor_codes(raw)
    labels = head_labels + paren_labels
    name = codes[0] if codes else (labels[0] if labels else "")
    if not name:
        return None
    return name, tuple(dict.fromkeys(codes)), years


def parse_era_sections(title: str, wikitext: str) -> tuple[GenerationSection, ...]:
    """Era sections for an article the ordinal grammar could not read.

    Level 2 only - censused, level 3 is model-year and trim sections. The
    article-grain gates: at least two candidates, each carrying its own
    infobox, start years strictly increasing in document order, and the
    article's own title not already at one generation's grain."""
    if _SUB_GRAIN_TITLE.search(title):
        return ()
    title_words = {w.casefold() for w in re.split(r"[\s()/-]+", title) if w}
    marks = [m for m in _HEADING.finditer(wikitext) if len(m.group(1)) == 2]
    candidates: list[tuple[GenerationSection, int]] = []
    for i, match in enumerate(marks):
        parsed = parse_era_heading(match.group(2), title_words)
        if parsed is None:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(wikitext)
        body = wikitext[match.end() : end]
        if not _INFOBOX_START.search(body):
            continue
        name, codes, years = parsed
        span, _reason = parse_span(infobox_field(body, "production") or "")
        start = span.start if span is not None else years[0]
        candidates.append(
            (
                GenerationSection(
                    ordinal=0,  # assigned below, once the cohort is admitted
                    heading=name,
                    codes=codes,
                    heading_years=years,
                    main_targets=tuple(
                        part.strip()
                        for hit in _MAIN_TEMPLATE.findall(body[:800])
                        for part in hit.split("|")
                        if part.strip()
                    ),
                    has_infobox=True,
                    body=body,
                ),
                start,
            )
        )
    if len(candidates) < 2:
        return ()
    starts = [start for _s, start in candidates]
    if any(b <= a for a, b in zip(starts, starts[1:], strict=False)):
        # Not a sequence of eras: parallel siblings, or a list of variants.
        return ()
    if all(section.main_targets for section, _start in candidates):
        return ()  # a family index page, deferring every entry elsewhere
    if len({section.heading for section, _s in candidates}) != len(candidates):
        return ()  # a repeated name is the nameplate, not the era
    return tuple(
        GenerationSection(
            ordinal=i,
            heading=section.heading,
            codes=section.codes or (section.heading,),
            heading_years=section.heading_years,
            main_targets=section.main_targets,
            has_infobox=section.has_infobox,
            body=section.body,
        )
        for i, (section, _start) in enumerate(candidates, start=1)
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
