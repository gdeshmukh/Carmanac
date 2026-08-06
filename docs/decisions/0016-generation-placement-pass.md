# ADR 0016 — Generations become company-anchored index entities; placement by unique dated overlap, timed by Wikipedia infoboxes

- Status: Accepted (2026-08-06; the end+1 slack accepted as the general
  rule with the expectation that specific exceptions surface later and
  are addressed by dedicated decision passes, not by loosening the rule)
- Date: 2026-08-06
- Depends on: ADR 0014 (placement is a nullable, evidence-gated fact on
  `configurations`), ADR 0012/0013 (the model sweep and its generation
  attachments), ADR 0011 §4 (external ids are 1:1 correspondence),
  ADR 0003 (raw landing zone), ADR 0004 (retention tiers)
- **Revises a settled invariant** (flagged per the charter's rule):
  `generations` stops being a child of one model.

## Context

Every one of the 23,523 configurations has `generation_id` NULL — the
honest state ADR 0014 chose, and the backlog this pass exists to work.
Before designing, the evidence on hand was measured live (2026-08-06):

- **151 generation rows exist, under 51 models.** Only **4 carry any
  year span**; 60 carry chassis codes; their names came from Wikidata
  labels. 2,683 configurations (11.4% of the total) sit under those 51
  models — the ceiling of what placement can reach with today's
  generation inventory.
- **Wikidata does not hold generation time.** Across all 14,524
  model-sweep records: 936 have any start date (P571/P729), 305 any end
  date. Even the BMW E30 (Q838837) carries no dates. As a time source
  at this level, Wikidata is effectively absent.
- **Wikidata does hold the pointer to where time lives.** 8,131 of the
  14,524 sweep records carry an English Wikipedia sitelink — including
  73 of the 151 held generations and 446 of the 969 series-membership
  (P179) records. A live probe of `BMW 3 Series (E30)` confirms the
  infobox asserts `production = 1982–1994` **and**
  `model_years = 1984–1991 (North America)` — the second field is the
  same axis our vPIC/EPA `model_year` catalogue periods live on —
  plus predecessor/successor and body styles.
- **The structural tension ADR 0012 parked is now blocking.** A
  generation row hangs under exactly one model (`generations.model_id`
  NOT NULL), but real generations cover many as-filed models — one E46
  spans 325i, 330i, M3. The 453 line-case generation entities have
  waited since ADR 0012 precisely because there was no honest single
  parent to hang them under.
- **The series-membership bridge has no feedstock yet.** The
  330i-∈-3-Series inference route needs line membership, and
  `model_line_members` holds 10 rows across 242 lines.
- **Trim strings are not chassis codes.** The code-shaped strings in
  `configurations.trim_name` are Mercedes/GM badges (C300, S600, K15) —
  matching them against `chassis_codes` would be the miscategorization
  machine. Chassis-code corroboration waits for a source that asserts
  codes on the *car* side (vPIC's VIN-decode layer is the candidate).
- **autoevolution.com was evaluated as a time/segmentation source and
  is parked.** Its editors have already segmented most nameplates into
  generations, but its robots.txt disallows AI/data crawlers wholesale
  (ClaudeBot, GPTBot, CCBot, Bytespider, …) and its terms page refuses
  programmatic fetches — under the charter's own rules (respect
  robots.txt; avoid commercial sites without clearly public data) it is
  not a scrape target. It also has no QID join, so ingesting it would
  require name-based matching — the exact surface the Tier-2/3 matcher
  gate guards. It remains legitimate as a *manual* reference when
  adjudicating flags, and a permission/licensing conversation is an
  option if its coverage proves indispensable. (Precision on the
  robots.txt reading: the AI-crawler blocks name specific companies'
  bots and the `*` rules bar only a few paths, so an identified
  CarmanacBot is not literally disallowed — but the charter commits to
  honest identification, which makes the site's *terms* the real
  gate, and those are unverifiable programmatically. A human reading
  of the terms, or permission, is the route back in; a crawler built
  to route around evident intent is not.)

## Decision

### 1. Generations are re-anchored to companies (invariant revision)

Since ADR 0014, generations are not a load-bearing level of the spine —
the mandatory rails are companies → models → catalogue_periods →
configurations, and placement is an evidence-gated fact on the
configuration. What a generation actually is, is an **enthusiast-facing
index and display entity**: a name, chassis codes, a span, a page. One
E46, one row, one page — not three copies under 325i/330i/M3, and not a
child of whichever model happened to be swept first.

- `generations.model_id` is replaced by `generations.company_id`
  (NOT NULL — the E46 is a BMW thing); uniqueness becomes
  `(company_id, slug)`.
- **Which models a generation covers is derived, not declared**: the
  generation page's model list is a query over placed configurations —
  the charter's "aggregation pages are queries over the spine" applied
  to the generation page itself.
- The migration re-parents the 151 existing rows (company reachable
  through their current model) and hands their model links to the new
  table below. Hand-written, with the same refuse-don't-discard
  downgrade posture as `c2d02b91b922`.

### 2. Model↔generation links are source-asserted facts

Dropping the parent FK removes the only thing that told the placement
pass which generations are even candidates for a model's
configurations. That knowledge was always a source assertion (the
Wikidata models pass attached each generation QID to a specific
nameplate), so it becomes an explicit fact-bearing association table,
same species as `model_line_members`:

- `generation_model_links` — (`generation_id`, `model_id`,
  `source_id`, `raw_record_id`, `scraped_at`, `confidence_score`),
  unique per (generation, model, source). A row means "this source
  places this generation in this model's history."
- The Wikidata models pass writes these links where it previously set
  the parent FK; the migration seeds the initial 151 from the current
  FKs, and the pass's next run re-asserts them with full provenance.
- The line-case entities (453 waiting) become representable without a
  schema fight: a company-anchored generation whose links arrive when
  line membership tells us which as-filed models belong to the series.
  Building that membership stays its own design turn — but it is now
  only a data gap, not a schema deadlock.

### 3. Wikipedia enters through the sitelink, not through matching

Wikipedia is Tier 2, and the charter gates Tier 2/3 sources on measured
matcher precision. That gate is about *name matching* — and this fetch
does none. Every article is fetched by the sitelink recorded on a QID
that is already 1:1-attached to one of our rows; identity is inherited,
not inferred. No new match surface opens, so the gate is not crossed.
This is stated here so the first Tier 2 ingestion is a flagged, explicit
non-violation rather than a silent one.

A new lander, `carmanac/ingest/wikipedia/`, fetches **section-0
wikitext** per article (MediaWiki API, polite rate, honest UA) and lands
it untransformed in `raw_scrape.raw_records`, keyed by the QID whose
sitelink named the article (`infobox:<QID>`), with the article title and
revision id in the payload. Parsing happens in the pass, never in the
lander — the raw wikitext is the Tier 2 archival record (ADR 0004: not
re-fetchable at the same revision, so retained).

**v1 fetch scope**: the 73 held-generation articles plus the 446
series-membership-record articles (~520 requests, one-time). Fetch-wide
applies within reason — the remaining ~7,600 sitelinked model-sweep
articles are a later sweep once a consumer exists for nameplate-level
infoboxes.

### 4. The time pass: infobox spans and codes become generation facts

Tier 2 standing, stated plainly (Gaurav's ruling, 2026-08-06): **tier
never quarantines facts** — review queues exist for identity ambiguity,
not source rank. Identity here is settled by the QID sitelink, so
infobox assertions write directly into `generations` through
`field_provenance`, no review gate. For generation coding there is
effectively no first-hand source (manufacturers don't publish chassis-
code tables), so Wikipedia is expected to be the *primary* asserter at
this level, and that is by design.

One narrow precedence exception, documented because the rails would
otherwise get it backwards: the generation facts held so far (names,
the 60 chassis-code arrays) were parsed out of **Wikidata labels** —
extraction from a naming string, not a deliberate claim. Tier-first
arbitration would let those label-parses beat an infobox on conflict
because Wikidata is Tier 1. Ruled: **for generation-level fields,
infobox assertions supersede label-derived ones.** The exception is
field-scoped and provenance-visible, and gets revisited when the
confidence-methodology ADR lands.

A new pass parses each landed infobox and asserts, through
`field_provenance` with raw-record lineage:

- `generations.start_year` / `end_year` from the **production** span —
  the global truth, and what the columns have always meant
  ("null end = still in production" handles `–present`).
- `generations.chassis_codes` from the **article title parenthetical**
  and infobox code fields where present — Wikipedia's per-generation
  pages are titled by manufacturer internal code in a convention that
  holds across marques (`BMW 3 Series (E46)`,
  `Mercedes-Benz C-Class (W205)`; verified by hand across 3 Series and
  C-Class, 2026-08-06). This is the enthusiast index filled from the
  source that actually curates it. Non-code parentheticals ("fourth
  generation") assert nothing.
- The **US model-year span** (`model_years`, when present) is *not*
  stored on the generation row — there is no column for it, and minting
  one for a US-specific reading is premature. It stays in the raw
  payload, where the placement pass reads it at decision time. If a
  second consumer appears, promoting it to a column is a follow-up
  decision.
- Unparseable or contradictory spans (template soup, multi-plant date
  lists that don't reduce to one range) **flag, never guess** —
  `implausible_value`-style, with the raw text in the detail.
- Where Wikidata *does* assert dates (the 936/305), both sources assert
  and the existing tier rails arbitrate; no special casing.

### 5. The placement rule: unique dated overlap within the model

For each configuration under model M, with its catalogue period's year
span:

- **Candidates** = generations linked to M in
  `generation_model_links` whose placement span contains the period.
  The placement span is the infobox `model_years` span (exact
  containment) when the period is `model_year`-kind and the field is
  present; otherwise the production span **with end+1 slack** (a US
  model year routinely outruns production by one calendar year; the
  start boundary gets no slack — a car cannot be catalogued before it
  is built). The slack is fixed, documented, and applied before the
  uniqueness test — if slack creates a second candidate, the
  configuration flags instead of placing.
- **Exactly one candidate** → write `configurations.generation_id`,
  with field-level provenance to the raw record whose span decided it;
  decision logged (`placed_dated_overlap`). **Amendment from the first
  live run (2026-08-06)**: uniqueness counts only when *every*
  generation linked to the model carries a span. One dated match beside
  undated siblings placed a 1991 Celica convertible into
  `celica-gt-four` (the GT-Four page was the only dated candidate) —
  so undated competitors hold the configuration in
  `waits_undated_competitor`, resolving mechanically as spans land.
- **Redirected articles assert nothing** (same live-run amendment): the
  "Honda Civic Hybrid" sitelink resolves to the whole-nameplate "Honda
  Civic" page, whose 1972–present span must not land on one generation.
  A resolved title that differs from the requested one beyond
  case/underscore wobble is a subject change; the record waits and any
  earlier assertion tombstones back to NULL.
- **Zero candidates** → stays NULL, decision logged
  (`waits_no_dated_generation`). Not a flag — it is the normal state
  for the 89% of configurations whose models have no linked generations
  yet.
- **Two or more candidates** → stays NULL, one flag per
  (model, period) cluster (`generation_overlap`) carrying the
  candidates. The 2019 AMG GT — the case that created ADR 0014 —
  lands here by design: C190 and X290 both span 2019, and *the year
  alone must not choose*. Discriminating overlaps by body style
  (infobox `body_style` × configuration body style) is the named
  follow-up, not part of v1.

The pass never creates generations, never modifies spans it did not
assert, and re-runs settle to exact no-ops. Decisions land in
`match_decisions`; open flags refresh per ADR 0013's rule. Reconciler
version bumps to v13.

## Consequences

- **One generation, one row, one page**, for the schema's whole future:
  chassis codes work as the enthusiast index they are ("show me every
  E46" is one GIN-indexed query), filled from article titles by the
  source that actually curates the convention, and the 453 line-case
  entities stop being structurally unrepresentable.
- The charter's Schema Overview needs its `generations` entry amended
  (FK → companies; model coverage derived; links table added) — done in
  the same branch as the implementation.
- The first configurations gain placement, capped at 2,683 (the 51
  linked models) and realistically fewer — bounded by the 73 fetchable
  articles plus 4 existing spans. The number is small and honest; it
  grows as generation inventory, links, and line membership grow, and
  every increment is provenance-carrying.
- Car pages for placed configurations can render
  model → generation → year; everything else keeps degrading gracefully
  exactly as ADR 0014 promised. Nothing lies.
- The proof car (`mercedes-benz-amg-gt-2019-s-coupe-rwd`) stays
  honestly NULL, now with an open `generation_overlap` flag naming both
  candidates — the backlog entry for the body-style follow-up.
- A Wikipedia lander exists, with the QID-keyed pattern any future
  infobox consumer (nameplate specs, multilingual sweeps) will reuse.
- autoevolution is recorded as evaluated-and-parked (robots.txt
  disallows AI crawlers; no QID join → would open the matcher gate);
  manual reference use stays legitimate; a licensing conversation is
  the route back if ever needed.
- Follow-ups named, not begun: body-style discrimination of overlap
  flags; the line-membership turn; promoting the US model-year span to
  a column if a second consumer appears.
