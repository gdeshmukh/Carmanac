# Wikidata fetch — background

Companion to `carmanac/ingest/wikidata/queries.py`. The queries there are the
closest thing this project has to a source contract; this is the reasoning
behind their shape.

## SPARQL, briefly

| Token | Means |
| --- | --- |
| `wd:Q786820` | a specific *entity* — here, "automobile manufacturer" |
| `wdt:P31` | a *property* — "instance of". `wdt:` is the simple-value form |
| `?item` | a variable; the engine finds every binding that fits |
| `OPTIONAL {}` | include the value if present, keep the row if absent (a LEFT JOIN) |
| `UNION {}` | rows matching either block — used for the fetch axes |
| `SERVICE wikibase:label` | resolves `Q246` into "Volkswagen" in a chosen language |

Without `OPTIONAL`, a manufacturer missing a founding date vanishes from the
results entirely.

## Three fetch axes

Wikidata's classification of carmakers is entropy, not taxonomy. Each axis
exists because a real marque was silently invisible without it.

**1. By class** (`wdt:P31`) — `automobile manufacturer` (Q786820), `car brand`
(Q10429667), `historical car manufacturer` (Q112865922).

Two lessons paid for that list. Q786820 alone missed Pontiac, Plymouth and
Datsun, all recorded only as brands. Q786820 + Q10429667 together still missed
**TVR** — classed only "privately held company" + "historical car
manufacturer" — along with ~50 other defunct marques (foundation review F6).

**2. By industry** (`wdt:P452` = automotive industry, Q190117) — catches real
manufacturers whose P31 is only generic corporate boilerplate. Measured
2026-07-28: Tesla Inc (corporation/business), Li Auto (company), Automobili
Pininfarina (trademark/enterprise), Auburn, Prince, Hispano-Suiza, Praga,
Gordon Murray Automotive.

This axis also pulls suppliers and tuners, which is fine — landing generously
while admitting strictly is the intended polarity (ADR 0007 §3).

**3. Pinned entities** — QIDs known to belong that both axes miss, fetched
unconditionally. Peugeot has P31 `organization` and P452 "trade in cars and
vehicles" (adding *that* industry would pull in car dealers). Singer Vehicle
Design has no automotive class and no industry at all. Grown by the
coverage-fixture triage in `coverage.py`, so a fixture miss either fixes an
axis or lands here — which makes the pin list a running record of Wikidata's
modelling holes.

## Why the full class set comes back

The query returns each entity's complete P31 set (`?classes`, `OPTIONAL` so
pinned entities without any P31 still land). Admission classifies on
everything an entity *is* — a "mobility service" co-class is what excludes
KINTO — and role assertions cite the target classes as evidence.

## Why the label chain is not just "en"

46% of the first landed set had no English label and came back as bare QID
strings ("Q288696", a French carmaker) — names the companies pass would have
minted verbatim. `mul` is Wikidata's language-neutral label and the usual home
of brand names. After the chain: 67 bare-QID labels remain out of 7,274, which
are genuinely no-label-anywhere entities and quarantine's problem.

## Why direct P31, not transitive

`wdt:P31/wdt:P279*` widens Q786820 alone from 6,514 to 9,960 by walking
subclass chains into general industrial companies. Widening later is a query
change plus a re-run, and since raw records are keyed by content hash, a re-run
adds only what is genuinely new.

## Why the aggregation is not optional

Measured live: the naive query shape — plain `OPTIONAL` blocks, one row per
result — returns **7,074 rows for 6,514 manufacturers**, and a single entity
("KINTO Europe", Q127773218) accounts for 360 of them alone. Multiple
`OPTIONAL` blocks each multiply the result set, so ~24 countries × ~15 websites
yields 360 permutations of the same entity.

That fan-out is an artifact of *our query shape*, not a claim Wikidata makes.
Landing it verbatim would file our own noise in the permanent raw store as
though it were source data. Aggregating the multi-valued properties
server-side with `GROUP_CONCAT` gives exactly one row per QID — also less data
on the wire, and measurably faster.

## GROUP_CONCAT for dates too — not SAMPLE, not MIN

Both alternatives were tried and both failed:

- **`SAMPLE()`** picks an arbitrary value when a property is multi-valued, and
  the pick can differ between runs. Proven live: Q112162285 ("Rising Auto")
  carries two truthy P571 claims, and two fetches 49 minutes apart landed two
  "different" payloads varying only in which inception `SAMPLE` returned —
  falsifying idempotency for the ~84 entities with multiple inception dates
  (foundation review F4).
- **`MIN()`** over these date literals crashes Blazegraph outright. Measured
  2026-07-28: `java.lang.StackOverflowError`, HTTP 500, reproducible with the
  otherwise-working query shape and gone the moment `MIN` reverts to `SAMPLE`.

Concatenating every claim is also the more honest option: the landing zone
stores what the source said (both of Rising Auto's founding dates), the
canonical sort makes it deterministic, and picking "earliest" is reconciler
policy (ADR 0007 §7) — a transformation, which never belonged in the fetch.

The rule that follows: **every aggregate in these queries must be
order-independent or canonicalized.** `GROUP_CONCAT` + `canonicalize()` is the
one shape satisfying that *and* surviving the endpoint. `tests/test_query_contract.py`
pins it.

## The models sweep

Two request shapes rather than the makes sweep's one, both probed live
2026-07-30 before building:

1. **The QID list** — the union of the three model-shaped classes.
   Deliberately unordered: `ORDER BY ?item` pushed the same query from 0.5s to
   ~90s, because WDQS sorts URIs before applying the LIMIT. Determinism comes
   from sorting client-side, where 14.5k strings cost nothing.
2. **Detail batches** — `VALUES ?item { ... }` with a page of QIDs, aggregated
   to exactly one row per entity (verified live: 300 in, 300 out, no
   duplicates). Batching makes the sweep resumable and keeps each request far
   from the endpoint's 60s timeout. A single 14.5k-entity aggregation would
   gamble the whole fetch on one long query against a service that 504s under
   load.

The three model classes are **not** a level taxonomy — one 3-Series lineage
holds all three shapes plus a classless stub. They are the fetch net; level is
decided per make by the reconciler, structurally (ADR 0012).
