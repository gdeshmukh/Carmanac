"""SPARQL queries against Wikidata - the source contract.

Kept apart from the client so "what we ask" is reviewable on its own, and
because these get edited far more often than the transport code around them.

Two rules hold across every query here, both paid for by live failures:

1. **Three fetch axes** (class, industry, pinned QIDs). No single axis covers
   the marques - each one exists because a real marque was invisible without
   it.
2. **Every aggregate must be order-independent or canonicalized.** SPARQL's
   `SAMPLE()` is non-deterministic across runs and `MIN()` crashes the
   endpoint, so `GROUP_CONCAT` + `canonicalize()` is the only usable shape.
   Aggregation is also what keeps one entity to one row: unaggregated, the
   OPTIONAL blocks multiply into 360 rows for a single company.

Both rules are pinned by `tests/test_query_contract.py`, which records what
each one cost when it was missing.
"""

from __future__ import annotations

import re

MAKES_QUERY = """
SELECT ?item ?itemLabel ?itemDescription
       (GROUP_CONCAT(DISTINCT ?inception; separator="|") AS ?inceptions)
       (GROUP_CONCAT(DISTINCT ?dissolved; separator="|") AS ?dissolutions)
       (GROUP_CONCAT(DISTINCT ?class;        separator="|") AS ?classes)
       (GROUP_CONCAT(DISTINCT ?countryLabel; separator="|") AS ?countries)
       (GROUP_CONCAT(DISTINCT ?countryCode;  separator="|") AS ?countryCodes)
       (GROUP_CONCAT(DISTINCT ?website;      separator="|") AS ?websites)
WHERE {
  {
    # Axis 1: by class - manufacturer, brand, historical manufacturer.
    VALUES ?targetClass { wd:Q786820 wd:Q10429667 wd:Q112865922 }
    ?item wdt:P31 ?targetClass .
  } UNION {
    # Axis 2: by industry - carmakers whose P31 is generic boilerplate
    # (Tesla Inc, Li Auto, Automobili Pininfarina...).
    ?item wdt:P452 wd:Q190117 .
  } UNION {
    # Axis 3: pinned - known marques both axes miss. Q6742 Peugeot,
    # Q55633247 Singer Vehicle Design; 2026-07-29, from the vPIC no-match
    # review: Ineos Automotive (P31: business only), Solectria (no P31 at
    # all), Moke International and Moke America (manufacturer/brand
    # boilerplate). Grown by coverage-fixture triage.
    VALUES ?item { wd:Q6742 wd:Q55633247 wd:Q108757682 wd:Q97353704
                   wd:Q57079249 wd:Q117381875 }
  }
  OPTIONAL { ?item wdt:P31 ?class . }       # the FULL class set - admission
                                            # classifies on everything the
                                            # entity is (ADR 0007 §3)
  OPTIONAL { ?item wdt:P571 ?inception. }   # inception (founded)
  OPTIONAL { ?item wdt:P576 ?dissolved. }   # dissolved / abolished
  OPTIONAL {
    ?item wdt:P17 ?country.                 # country
    OPTIONAL { ?country wdt:P297 ?countryCode. }   # its ISO 3166-1 alpha-2
  }
  OPTIONAL { ?item wdt:P856 ?website. }     # official website
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "en,mul,de,ja,fr,it".
    ?item    rdfs:label         ?itemLabel.
    ?item    schema:description ?itemDescription.
    ?country rdfs:label         ?countryLabel.
  }
}
GROUP BY ?item ?itemLabel ?itemDescription
"""

# Derived from the query text rather than kept as a parallel list in land.py:
# when the two had to agree by hand, land.py canonicalized the vars someone
# remembered to list, not the vars the query actually aggregates.
#
# Non-greedy `.+?` because aggregated expressions may nest parens.
_GROUP_CONCAT_ALIAS = re.compile(r"GROUP_CONCAT\(.+?\)\s+AS\s+\?(\w+)")


def multi_value_vars(query: str) -> frozenset[str]:
    """The GROUP_CONCAT'd variable names of a query - what canonicalization
    must sort. Derived per query now that there are two sweeps."""
    found = frozenset(_GROUP_CONCAT_ALIAS.findall(query))
    if not found:
        raise RuntimeError(
            "No GROUP_CONCAT aliases found - the derivation regex is broken, "
            "and canonicalization would silently stop hashing stably."
        )
    return found


MULTI_VALUE_VARS: frozenset[str] = multi_value_vars(MAKES_QUERY)

# --- the models sweep (ADR 0012 §1) ------------------------------------------
#
# Two request shapes: an unordered QID list, then detail batches. Unordered
# because `ORDER BY ?item` costs ~90s against 0.5s (WDQS sorts URIs before the
# LIMIT) - client-side sorting gives the same determinism for free. Batched
# because one 14.5k-entity aggregation would gamble the whole fetch on a
# single long query against a service that 504s under load.
#
# These classes are the fetch NET, not a level taxonomy - one 3-Series lineage
# holds all three plus a classless stub. Level is decided per make by the
# reconciler, structurally (ADR 0012).

MODEL_CLASSES: dict[str, str] = {
    "Q3231690": "car model",
    "Q59773381": "automobile model series",
    "Q29048322": "vehicle model",
}

MODELS_QID_QUERY = """
SELECT DISTINCT ?item WHERE {{
  VALUES ?cls {{ {classes} }}
  ?item wdt:P31 ?cls .
}}
""".format(classes=" ".join(f"wd:{qid}" for qid in MODEL_CLASSES))

# Everything ADR 0012 §1 lists, per entity. The opaque property numbers:
# P176 manufacturer (the attach point), P179 series / P361 part-of (line and
# generation evidence), P155/P156 chains (the richest generation signal),
# P571/P576/P729/P730/P2669 the date properties, plus the enwiki sitelink
# title that the Wikipedia-infobox wave will need.
MODELS_DETAIL_QUERY = """
SELECT ?item ?itemLabel ?itemDescription ?article
       (GROUP_CONCAT(DISTINCT ?alias;        separator="|") AS ?aliases)
       (GROUP_CONCAT(DISTINCT ?class;        separator="|") AS ?classes)
       (GROUP_CONCAT(DISTINCT ?maker;        separator="|") AS ?makers)
       (GROUP_CONCAT(DISTINCT ?series;       separator="|") AS ?seriesOf)
       (GROUP_CONCAT(DISTINCT ?partOf;       separator="|") AS ?partsOf)
       (GROUP_CONCAT(DISTINCT ?follows;      separator="|") AS ?followsIds)
       (GROUP_CONCAT(DISTINCT ?followedBy;   separator="|") AS ?followedByIds)
       (GROUP_CONCAT(DISTINCT ?inception;    separator="|") AS ?inceptions)
       (GROUP_CONCAT(DISTINCT ?dissolved;    separator="|") AS ?dissolutions)
       (GROUP_CONCAT(DISTINCT ?prodStart;    separator="|") AS ?prodStarts)
       (GROUP_CONCAT(DISTINCT ?prodEnd;      separator="|") AS ?prodEnds)
       (GROUP_CONCAT(DISTINCT ?discontinued; separator="|") AS ?discontinueds)
WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P31  ?class . }}
  OPTIONAL {{ ?item wdt:P176 ?maker . }}
  OPTIONAL {{ ?item wdt:P179 ?series . }}
  OPTIONAL {{ ?item wdt:P361 ?partOf . }}
  OPTIONAL {{ ?item wdt:P155 ?follows . }}
  OPTIONAL {{ ?item wdt:P156 ?followedBy . }}
  OPTIONAL {{ ?item wdt:P571 ?inception . }}
  OPTIONAL {{ ?item wdt:P576 ?dissolved . }}
  OPTIONAL {{ ?item wdt:P729 ?prodStart . }}
  OPTIONAL {{ ?item wdt:P730 ?prodEnd . }}
  OPTIONAL {{ ?item wdt:P2669 ?discontinued . }}
  OPTIONAL {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = "en") }}
  OPTIONAL {{
    ?article schema:about ?item ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en,mul,de,ja,fr,it".
    ?item rdfs:label ?itemLabel.
    ?item schema:description ?itemDescription.
  }}
}}
GROUP BY ?item ?itemLabel ?itemDescription ?article
"""

MODELS_MULTI_VALUE_VARS: frozenset[str] = multi_value_vars(MODELS_DETAIL_QUERY)
