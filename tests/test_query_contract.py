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
    differ between runs, which lands the same entity twice (F4). Every
    aggregate must be order-independent (MIN/MAX) or canonicalized after the
    fact (GROUP_CONCAT, handled by canonicalize())."""
    assert "SAMPLE" not in MAKES_QUERY.upper()


def test_multi_value_vars_cover_every_group_concat():
    """The canonicalized-variable set is derived from the query text; if the
    derivation regex rots, aggregation aliases stop being sorted and hashing
    goes unstable again. Cross-check with an independent count."""
    group_concats = len(re.findall(r"GROUP_CONCAT", MAKES_QUERY))
    assert group_concats > 0, "query no longer aggregates? tests need a rethink"
    assert len(MULTI_VALUE_VARS) == group_concats
    assert {"countries", "websites"} == MULTI_VALUE_VARS


def test_separator_agrees_between_query_and_canonicalizer():
    """canonicalize() splits on the separator the query concatenates with. If
    someone changes one and not the other, sorting silently degrades to a
    no-op on one-element lists."""
    assert f'separator="{land._MULTI_VALUE_SEPARATOR}"' in MAKES_QUERY


def test_both_marque_classes_queried():
    """Q786820 (automobile manufacturer) alone misses Pontiac, Plymouth and
    Datsun, which Wikidata records only as Q10429667 (car brand). Narrowing
    the query back to one class would re-lose 708 brand-only marques."""
    assert "wd:Q786820" in MAKES_QUERY
    assert "wd:Q10429667" in MAKES_QUERY


def test_group_concat_lists_are_distinct():
    """DISTINCT inside GROUP_CONCAT is what collapsed the KINTO Europe
    Cartesian fan-out (360 rows for one entity). Without it the row explosion
    returns on the next OPTIONAL clause added."""
    for clause in re.findall(r"GROUP_CONCAT\([^)]*\)", MAKES_QUERY):
        assert "DISTINCT" in clause
