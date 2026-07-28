"""SPARQL queries against Wikidata.

Kept apart from the client so the "what we ask" is reviewable on its own - these
are the closest thing this project has to a source contract, and they will be
edited far more often than the transport code around them.

A short glossary, since SPARQL is its own language:

- `wd:Q786820`   a specific Wikidata *entity*: "automobile manufacturer".
- `wdt:P31`      a *property*: "instance of". `wdt:` is the simple-value form.
- `?item`        a variable. The engine finds every binding that fits.
- `OPTIONAL {}`  include the value if present, keep the row if absent (a LEFT
                 JOIN). Without it, a manufacturer missing a founding date
                 would vanish from the results entirely.
- `SERVICE wikibase:label`  Wikidata stores QIDs, not names; this resolves
                 `Q246` into "Volkswagen" in a chosen language.

### Why the aggregation is not optional

Measured against the live endpoint: the naive form of this query - plain
OPTIONAL blocks, one row per result - returns **7,074 rows for 6,514
manufacturers**, and a single entity ("KINTO Europe", Q127773218) accounts for
360 of them on its own. Multiple OPTIONAL blocks each multiply the result set,
so ~24 countries x ~15 websites yields 360 permutations of the same entity.

That fan-out is an artifact of *our query shape*, not a claim Wikidata makes.
Landing it verbatim would file our own noise in the permanent raw store as
though it were source data. So the multi-valued properties are aggregated
server-side with GROUP_CONCAT, giving exactly one row per QID - which is also
less data on the wire and measurably faster (9.6s vs 14.3s for all 6,514).

### Why the scope is deliberately loose

`wdt:P31 wd:Q786820` catches a lot that is not a car marque: assembly plants,
mobility-services subsidiaries, defunct holding companies. That is intentional.
Filtering at fetch time discards data we cannot get back without re-scraping;
filtering at reconcile time is reversible, because the raw record is still
there. Land generously, decide later.
"""

from __future__ import annotations

import re

# Two classes, not one. Wikidata separates the *company* from the *marque*:
#
#   Q786820   automobile manufacturer  - the company that builds cars
#   Q10429667 car brand                - the nameplate cars are sold under
#
# `makes` in this schema is the marque, so querying only Q786820 silently loses
# brands whose manufacturing entity is recorded under a parent. Measured: it
# misses **Pontiac, Plymouth, and Datsun** outright - Pontiac is a "division"
# and a "car brand" but never an "automobile manufacturer". Saab and Oldsmobile
# happen to carry both classes and survived; that is luck, not a rule.
#
# Counts: 6,514 manufacturers + 777 brands = 7,222 union (708 brands are not
# also manufacturers). The overlap is handled by GROUP BY, which collapses an
# entity matching both classes back to one row.
#
# Direct `wdt:P31` only, not the transitive `wdt:P31/wdt:P279*`. Measured: the
# transitive form widens 6,514 -> 9,960 on Q786820 alone by walking subclass
# chains into general industrial companies. Widening later is a query change
# and a re-run - and, because raw records are keyed by content hash, a re-run
# adds only what is genuinely new.
MAKES_QUERY = """
SELECT ?item ?itemLabel ?itemDescription
       (MIN(?inception) AS ?inception)
       (MIN(?dissolved) AS ?dissolved)
       (GROUP_CONCAT(DISTINCT ?countryLabel; separator="|") AS ?countries)
       (GROUP_CONCAT(DISTINCT ?website;      separator="|") AS ?websites)
WHERE {
  VALUES ?class { wd:Q786820 wd:Q10429667 }
  ?item wdt:P31 ?class .
  OPTIONAL { ?item wdt:P571 ?inception. }   # inception (founded)
  OPTIONAL { ?item wdt:P576 ?dissolved. }   # dissolved / abolished
  OPTIONAL { ?item wdt:P17  ?country. }     # country
  OPTIONAL { ?item wdt:P856 ?website. }     # official website
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "en".
    ?item    rdfs:label         ?itemLabel.
    ?item    schema:description ?itemDescription.
    ?country rdfs:label         ?countryLabel.
  }
}
GROUP BY ?item ?itemLabel ?itemDescription
"""

# MIN, not SAMPLE, for the date aggregates. SAMPLE() picks an ARBITRARY value
# when the property is multi-valued, and the pick can differ between runs -
# proven live: Q112162285 ("Rising Auto") carries two truthy P571 claims, and
# two fetches 49 minutes apart landed two "different" payloads varying only in
# which inception SAMPLE happened to return, falsifying idempotency for the
# ~84 landed entities with multiple inception dates (foundation review F4).
# MIN is deterministic, and "earliest founding" is the conventional reading.
# The same instability class as the GROUP_CONCAT ordering bug above - every
# aggregate in this query must be order-independent or canonicalized.

# The GROUP_CONCAT aliases, derived from the query text itself rather than
# maintained as a parallel list in land.py. The two previously had to agree by
# hand across files, which is exactly how the SAMPLE gap slipped: land.py
# canonicalized the vars someone remembered to list, not the vars the query
# actually aggregates.
_GROUP_CONCAT_ALIAS = re.compile(r"GROUP_CONCAT\([^)]*\)\s+AS\s+\?(\w+)")

MULTI_VALUE_VARS: frozenset[str] = frozenset(_GROUP_CONCAT_ALIAS.findall(MAKES_QUERY))

if not MULTI_VALUE_VARS:  # pragma: no cover - guards the regex, not the data
    raise RuntimeError(
        "No GROUP_CONCAT aliases found in MAKES_QUERY - the derivation regex is "
        "broken, and canonicalization would silently stop hashing stably."
    )
