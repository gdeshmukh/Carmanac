"""Wikidata mapper: one landed SPARQL binding -> a `MappedRecord` (ADR 0007 §7).

Pure translation, no database access. Transformations that are *policy* live
here deliberately - "founded is the EARLIEST claim" is a decision about
Wikidata's data, not engine mechanics - while everything shared (identity,
supersession, projection, flags) stays in the engine.

The payload is the canonicalized SPARQL binding land.py stored: each variable
a `{"type": ..., "value": ...}` cell, multi-valued variables GROUP_CONCAT'd
with "|" and sorted (see queries.py for why). An absent property is an empty
string, not a missing key.
"""

from __future__ import annotations

import re

from carmanac.reconcile.types import Assertion, FlagRequest, MappedRecord

SOURCE_NAME = "Wikidata"

# The company fields this mapper can assert. The engine tombstones a live
# assertion only for fields inside this coverage - a field the mapper never
# speaks about is silence, not retraction (ADR 0007 §1).
COVERAGE: tuple[str, ...] = (
    "name",
    "summary",
    "founded_year",
    "defunct_year",
    "country_id",
    "website",
)

_ENTITY_PREFIX = "http://www.wikidata.org/entity/"
_SEPARATOR = "|"

# Wikidata time literals: "1916-03-07T00:00:00Z", sometimes negative years.
# Only the year is projected (companies carry founded_year/defunct_year).
_YEAR = re.compile(r"^(-?\d{1,4})")


def _cell(payload: dict, var: str) -> str:
    return (payload.get(var) or {}).get("value") or ""


def _split(payload: dict, var: str) -> list[str]:
    """A GROUP_CONCAT'd cell -> its distinct non-empty parts (already sorted
    by canonicalization; de-duplicated defensively anyway)."""
    parts = [p for p in _cell(payload, var).split(_SEPARATOR) if p]
    return sorted(set(parts))


def _year_of(date_literal: str) -> int | None:
    m = _YEAR.match(date_literal)
    return int(m.group(1)) if m else None


def map_record(payload: dict) -> MappedRecord | None:
    """Translate one payload. Returns None for a record with no QID - not
    expected from our query, but an unkeyable record cannot be reconciled."""
    item = _cell(payload, "item")
    if not item.startswith(_ENTITY_PREFIX):
        return None
    qid = item.removeprefix(_ENTITY_PREFIX)

    classes = frozenset(c.removeprefix(_ENTITY_PREFIX) for c in _split(payload, "classes"))

    # A label equal to the bare QID means the label service found nothing in
    # any fallback language - there is no usable name (queries.py measured 67
    # such entities; quarantine's job, which the engine handles on name=None).
    label = _cell(payload, "itemLabel")
    name = label if label and label != qid else None

    assertions: list[Assertion] = []
    flags: list[FlagRequest] = []

    if name:
        assertions.append(Assertion("name", name, name))

    description = _cell(payload, "itemDescription")
    if description:
        assertions.append(Assertion("summary", description, description))

    # Dates: every claim landed (sorted); projection picks earliest founding /
    # latest dissolution (ADR 0007 §7). Multiple distinct claims are worth a
    # human look - Rising Auto carries two founding dates - so they also flag.
    for var, field_name, pick in (
        ("inceptions", "founded_year", min),
        ("dissolutions", "defunct_year", max),
    ):
        dates = _split(payload, var)
        years = [y for d in dates if (y := _year_of(d)) is not None]
        if not years:
            continue
        assertions.append(Assertion(field_name, _SEPARATOR.join(dates), pick(years)))
        if len(dates) > 1:
            flags.append(FlagRequest("multi_value", field_name, {"claims": dates}))

    # Country: exactly one ISO code projects; several is a real modeling
    # question (which country is "the" country of a multinational?) -> flag,
    # no projection; zero -> no assertion (ADR 0007 §7).
    codes = _split(payload, "countryCodes")
    if len(codes) == 1:
        assertions.append(Assertion("country_id", codes[0], codes[0]))
    elif len(codes) > 1:
        flags.append(
            FlagRequest(
                "multi_value",
                "country_id",
                {"codes": codes, "countries": _split(payload, "countries")},
            )
        )

    websites = _split(payload, "websites")
    if len(websites) == 1:
        assertions.append(Assertion("website", websites[0], websites[0]))
    elif len(websites) > 1:
        flags.append(FlagRequest("multi_value", "website", {"websites": websites}))

    # Role (ADR 0007 §4): any target class asserts `manufacturer`, citing the
    # classes as evidence. The policy module owns the target list; importing
    # it here would invert the layering, so the engine passes it in - the
    # mapper just reports the class set and the engine decides. Kept simple:
    # the mapper flags nothing about roles.
    return MappedRecord(
        external_id=qid,
        classes=classes,
        name=name,
        assertions=tuple(assertions),
        flag_requests=tuple(flags),
    )
