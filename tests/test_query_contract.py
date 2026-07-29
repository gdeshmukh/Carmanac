"""The SPARQL query as a contract.

These tests pin the properties of MAKES_QUERY that were each, at some point,
violated for real. They are cheap string checks, but every one guards a bug
that reached the live landing zone or silently lost data:

- SAMPLE() landed a spurious duplicate raw record (foundation review F4).
- Querying one class instead of two silently dropped Pontiac, Plymouth and
  Datsun (the 2026-07-24 coverage bug).
- The canonicalization list and the query's aggregates drifting apart is how
  the SAMPLE gap escaped canonicalization in the first place.
"""

from __future__ import annotations

import re

from carmanac.ingest.wikidata import land
from carmanac.ingest.wikidata.queries import MAKES_QUERY, MULTI_VALUE_VARS


def test_no_nondeterministic_aggregates():
    """SAMPLE() picks arbitrarily among multi-valued properties; the pick can
    differ between runs, which lands the same entity twice (F4). And MIN/MAX
    over the date literals crashes Blazegraph outright (StackOverflowError,
    measured 2026-07-28). GROUP_CONCAT + canonicalize() is the one aggregate
    shape that is deterministic AND survives the endpoint - so it is the ONLY
    aggregate this query may use."""
    assert "SAMPLE" not in MAKES_QUERY.upper()
    assert re.findall(r"\b(MIN|MAX)\s*\(", MAKES_QUERY) == []


def test_multi_value_vars_cover_every_group_concat():
    """The canonicalized-variable set is derived from the query text; if the
    derivation regex rots, aggregation aliases stop being sorted and hashing
    goes unstable again. Cross-check with an independent count."""
    group_concats = len(re.findall(r"GROUP_CONCAT", MAKES_QUERY))
    assert group_concats > 0, "query no longer aggregates? tests need a rethink"
    assert len(MULTI_VALUE_VARS) == group_concats
    assert {
        "inceptions",
        "dissolutions",
        "classes",
        "countries",
        "countryCodes",
        "websites",
    } == MULTI_VALUE_VARS


def test_separator_agrees_between_query_and_canonicalizer():
    """canonicalize() splits on the separator the query concatenates with. If
    someone changes one and not the other, sorting silently degrades to a
    no-op on one-element lists."""
    assert f'separator="{land._MULTI_VALUE_SEPARATOR}"' in MAKES_QUERY


def test_all_three_fetch_axes_present():
    """Every axis was paid for by a silently missing real marque, twice over
    (F6): the class axis misses Pontiac with one class and TVR with two; the
    industry axis is what catches Tesla Inc / Li Auto / Automobili
    Pininfarina (P31 = generic corporate classes only); the pins catch
    Peugeot (organization + dealer-industry) and Singer (nothing automotive
    at all). Removing any axis re-loses real marques."""
    # axis 1: classes
    assert "wd:Q786820" in MAKES_QUERY
    assert "wd:Q10429667" in MAKES_QUERY
    assert "wd:Q112865922" in MAKES_QUERY
    # axis 2: industry
    assert "wdt:P452 wd:Q190117" in MAKES_QUERY
    # axis 3: pins (Peugeot, Singer Vehicle Design)
    assert "wd:Q6742" in MAKES_QUERY
    assert "wd:Q55633247" in MAKES_QUERY


def test_label_service_has_fallback_chain():
    """With "en" alone, 46% of landed entities had a bare QID as their label
    ("Q288696", a French carmaker) - names the companies pass must never
    mint. The chain must include "mul", Wikidata's language-neutral label,
    where brand names usually live."""
    chain = re.search(r'wikibase:language "([^"]+)"', MAKES_QUERY)
    assert chain is not None
    languages = chain.group(1).split(",")
    assert languages[0] == "en"
    assert "mul" in languages


def test_full_class_set_is_selected():
    """Admission (ADR 0007 SS3) classifies on everything an entity IS - the
    unconstrained `?item wdt:P31 ?class` triple is what lands the co-classes
    ("mobility service") that exclude KINTO-shaped entities. OPTIONAL, so a
    pinned entity with no P31 at all still lands (and quarantines on its
    empty class set - the correct polarity)."""
    assert re.search(r"OPTIONAL \{ \?item wdt:P31 \?class \. \}", MAKES_QUERY)
    assert "?classes" in MAKES_QUERY


def test_group_concat_lists_are_distinct():
    """DISTINCT inside GROUP_CONCAT is what collapsed the KINTO Europe
    Cartesian fan-out (360 rows for one entity). Without it the row explosion
    returns on the next OPTIONAL clause added."""
    for clause in re.findall(r"GROUP_CONCAT\([^)]*\)", MAKES_QUERY):
        assert "DISTINCT" in clause
