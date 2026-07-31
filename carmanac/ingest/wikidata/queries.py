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
- `UNION {}`     rows matching either block. Used for the fetch axes below.
- `SERVICE wikibase:label`  Wikidata stores QIDs, not names; this resolves
                 `Q246` into "Volkswagen" in a chosen language.

### Three fetch axes, because no single one covers the marques

Wikidata's classification of carmakers is entropy, not taxonomy, and each axis
below exists because a real marque was silently invisible without it:

1. **By class** (`wdt:P31`): `automobile manufacturer` (Q786820), `car brand`
   (Q10429667), `historical car manufacturer` (Q112865922). Two lessons paid
   for this list: Q786820 alone missed Pontiac/Plymouth/Datsun (recorded only
   as brands), and both together missed **TVR** - classed only "privately held
   company" + "historical car manufacturer", along with 50 other defunct
   marques (foundation review F6).
2. **By industry** (`wdt:P452` = automotive industry, Q190117): catches real
   manufacturers whose P31 is only generic corporate boilerplate - measured
   2026-07-28: **Tesla Inc** (corporation/business), **Li Auto** (company),
   **Automobili Pininfarina** (trademark/enterprise), Auburn, Prince,
   Hispano-Suiza, Praga, Gordon Murray Automotive. Also pulls suppliers and
   tuners; that is fine - admission quarantines strictly (ADR 0007 SS3), and
   landing generously while admitting strictly is the intended polarity.
3. **Pinned entities**: QIDs we know belong but that BOTH axes miss, fetched
   unconditionally. Peugeot (P31: organization; P452: "trade in cars and
   vehicles" - adding that industry would pull car dealers). Singer Vehicle
   Design (no automotive class, no industry at all). Maintained by the
   coverage-fixture triage in coverage.py: a fixture miss either fixes an
   axis or lands here, so the pin list documents Wikidata's modeling holes.

The query also returns each entity's FULL P31 class set (`?classes`,
OPTIONAL so pinned entities without P31 still land): the reconciler's
admission rule classifies on everything an entity is - a "mobility service"
co-class is what excludes KINTO - and the role assertions cite the target
classes as evidence (ADR 0007 SS3/SS4).

The label service takes a fallback chain, not "en" alone: 46% of the first
landed set had NO English label and came back as bare QID strings ("Q288696" -
a French carmaker), which the companies pass would have minted as names.
"mul" is Wikidata's language-neutral label, the usual home of brand names.
Measured after the chain: 67 bare-QID labels remain of 7,274 - real
no-label-anywhere entities, quarantine's job.

Direct `wdt:P31` only, not the transitive `wdt:P31/wdt:P279*`. Measured: the
transitive form widens 6,514 -> 9,960 on Q786820 alone by walking subclass
chains into general industrial companies. Widening later is a query change
and a re-run - and, because raw records are keyed by content hash, a re-run
adds only what is genuinely new.

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
less data on the wire and measurably faster.

### GROUP_CONCAT for the dates too - not SAMPLE, not MIN. Both were tried:

- SAMPLE() picks an ARBITRARY value when the property is multi-valued, and
  the pick can differ between runs. Proven live: Q112162285 ("Rising Auto")
  carries two truthy P571 claims, and two fetches 49 minutes apart landed
  two "different" payloads varying only in which inception SAMPLE returned,
  falsifying idempotency for the ~84 entities with multiple inception dates
  (foundation review F4).
- MIN() over these date literals crashes Blazegraph outright - measured
  2026-07-28: java.lang.StackOverflowError, HTTP 500, reproducible with the
  otherwise-working query shape and gone the moment MIN reverts to SAMPLE.

Concatenating every claim is also more honest than either: the landing zone
stores what the source said (both of Rising Auto's founding dates), the
canonical sort makes it deterministic, and picking "earliest" is reconciler
policy (ADR 0007 SS7) - a transformation, which never belonged in the fetch.
Every aggregate in this query must be order-independent or canonicalized;
GROUP_CONCAT + canonicalize() is the one shape that satisfies that AND the
endpoint.
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
                                            # entity is (ADR 0007 SS3)
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

# The GROUP_CONCAT aliases, derived from the query text itself rather than
# maintained as a parallel list in land.py. The two previously had to agree by
# hand across files, which is exactly how the SAMPLE gap slipped: land.py
# canonicalized the vars someone remembered to list, not the vars the query
# actually aggregates.
#
# Non-greedy `.+?` rather than `[^)]*`: aggregated expressions may nest parens,
# and the first `) AS` after GROUP_CONCAT( is always the aggregate's own close.
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
# Two request shapes instead of the makes sweep's one, both probed live
# 2026-07-30 before building:
#
# 1. The QID list: the union of the three model-shaped classes. Deliberately
#    UNORDERED - `ORDER BY ?item` pushed the same query from 0.5s to ~90s
#    (WDQS sorts URIs before the LIMIT), so determinism comes from sorting
#    client-side, where 14.5k strings cost nothing.
# 2. Detail batches: `VALUES ?item { ... }` with a page of QIDs, aggregated
#    to exactly one row per entity (verified live: 300 in, 300 out, no dupes).
#    Batching is what makes the sweep resumable and keeps each request far
#    from the endpoint's 60s timeout; a single 14.5k-entity aggregation would
#    gamble the whole fetch on one long query against a service that 504s
#    under load.
#
# The classes are NOT a level taxonomy (ADR 0012: one 3-Series lineage holds
# all three shapes plus a classless stub) - they are the fetch net. Level is
# decided per make by the reconciler, structurally.

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

# Everything ADR 0012 §1 lists, per entity: the full P31 class set, label +
# English aliases + description, P176 manufacturer (the attach point), P179
# series / P361 part-of (line and generation evidence), P155/P156 chains (the
# richest generation signal), every date property (P571 inception, P576
# dissolved, P729 service entry, P730 service retirement, P2669 discontinued),
# and the enwiki sitelink title (the pointer the Wikipedia-infobox wave needs).
# Same fallback label chain and GROUP_CONCAT + canonicalize discipline as the
# makes query - every aggregate order-independent or canonicalized.
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
