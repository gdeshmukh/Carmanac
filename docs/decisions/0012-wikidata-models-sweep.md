# ADR 0012 — The Wikidata models sweep: the ladder, lines, and generations

- Status: Accepted (2026-07-30, PR #24, as amended - expansion tabled, E46-page-as-view); amended 2026-08-19 (§7, the mint registry); amended 2026-08-20 (§7, duplicate rulings resolve through a registry)
- Date: 2026-07-30
- Depends on: ADR 0007 (reconciler contract), ADR 0008 (match-pass
  precedent), ADR 0010 (the models pass), ADR 0011 (as-filed models;
  series are lines; external ids are 1:1 correspondence)

## Context

The as-filed models exist (1,735, vPIC), their year lists are landing, and
EPA's per-variant rows are landed. The missing middle is **generations** —
nothing asserts them yet, and every catalogue period needs one — plus the
**line relation** ADR 0011 §2 deferred here. Wikidata is the only landed
source that speaks at model level globally.

Surveyed live before designing (2026-07-30):

- **13,733** `car model` entities, **385** `automobile model series`,
  **489** `vehicle model` — the sweep's population.
- **83%** carry P176 (manufacturer), pointing at 1,130 distinct makers —
  the attach point to our companies.
- Structure is inconsistent: P179 series membership on only **7%**;
  follows/followed-by chains on **37%** (the richest generation signal);
  dates are sparse (inception on 6%, discontinued on 2%) — so Wikidata
  contributes generation *identity* while vPIC/EPA contribute the *time*.
- **Class does not encode level.** One 3-Series lineage holds a plain
  `car model` (the E21-shaped entity), a series-classed generation (the
  E36-shaped one), the series proper, and a classless stub — four entities
  labelled "BMW 3 Series". Meanwhile **"BMW 330i" does not exist**, and
  Mercedes' C-Class entity is series-classed yet corresponds one-to-one
  with our as-filed model.
- The platform property recorded in earlier notes (P4243) returns zero
  uses — the pid must be re-verified before the platforms ADR.

## Decision

### 1. One maximal sweep, bare QIDs, a sweep marker

Fetch the union of the three model-shaped classes, paginated, pulling per
entity: all P31 classes, label + aliases + description (en), P176, P179,
P361, P155/P156, every date property (P571, P576, P729, P730, P2669), and
the enwiki sitelink title (the pointer the Wikipedia-infobox wave will
need). Same canonicalization as the makes sweep (sorted GROUP_CONCAT).

Raw `external_id` stays the **bare QID** — QIDs are globally unique, so
the vPIC-style kind prefix solves a collision that cannot happen here.
Kind *selection* cannot rely on payload shape (the vPIC lesson) or on
classes (company records legitimately carry model classes — that is why
they DENY there), so the landing stamps a `sweep: models` marker in the
payload, the same fetch-metadata species as the vPIC `vehicle_types`
merge.

### 2. Level is decided per make, against the as-filed models

The pass resolves each entity through its P176 company (via the external-
id map), then descends:

1. **Existing external id** → refresh (re-runs).
2. **P176 unresolvable** (maker we don't hold, or absent) → the entity
   waits, unflagged — ADR 0010 §1's posture: models of unheld makers are
   one question, not thousands of copies. A P176 pointing at a *held but
   wrong-level* company (Mercedes-Benz Group vs the marque) surfaces as a
   candidates flag when the name rungs disagree with it.
3. **Exact-normalized name/alias match** against that company's as-filed
   models — including the **make-prefix-stripped** form ("Toyota 4Runner"
   ↔ `4Runner`; still mechanical, never fuzzy). A unique hit is a model
   correspondence: write the QID onto the model row, assert facts. This
   rung is what makes level per-make: C-Class matches Mercedes' as-filed
   row and is a *model*; "BMW 3 Series" matches nothing of BMW's and
   falls through.
4. **Line evidence** — P179 members pointing at it, or series class with
   chain-linked children → a **line** (§4), never a model row.
5. **Generation evidence** — chained to (or P179-member of) a matched
   model or a line → **generation structure** (§5).
6. **Otherwise** → `match_review` flag with candidates only when the
   entity had a resolvable company and near-miss candidates; else it
   waits in raw.

### 3. The global expansion: approved in principle, TABLED — v1 corroborates, never creates

The mission is every production car globally, and Wikidata's ~11k
manufacturer-linked model entities are the road there. **Approved in
principle, deliberately not in v1** (ruled 2026-07-30): the vPIC+EPA
base gives the US cars deep, multi-source data the reconciler can prove
itself on; single-source global creation would mint thousands of rows
with exactly one assertion behind each. The pass therefore **matches and
enriches only**. A model-shaped entity under a held company with no
as-filed match is marked reconciled-seen and waits — no row, no flag
(ADR 0010 §1's posture, one level over). The sweep still lands
*everything* (fetch-wide): raw is the stocked warehouse the expansion
draws from the day it turns on, by re-run, with no re-fetch.

The expansion turns on by explicit decision when either trigger fires:
a second global-capable source lands to corroborate (Wikipedia
infoboxes, Euro type approval), or the US set is reconciled deeply
enough that the machinery's precision is demonstrated.

`FIELD_AFFINITY` in v1 is simple: `models.name` stays with vPIC (the
filing source — Wikidata labels prefix the make, and "Toyota 4Runner"
must never rename `4Runner`); Wikidata asserts `summary` on every match.

### 4. Lines are grouping rows, not entities

Two new tables: `model_lines` (id, company_id, slug, name — a grouping
key with a display name) and `model_line_members` (line_id, model_id,
plus the standard fact provenance columns). Lines hold **no external
ids** (ADR 0011 §4: the series QID stays on the raw record; memberships
carry `raw_record_id` provenance), are FK'd by nothing else in the
hierarchy, and render as browse views ("/makes/bmw/3-series" is a page
over members, not an entity). Membership arrives from P179 and from
rung-3 matches of member-shaped entities; it is a fact like any other —
superseded, flagged, never unique-constrained across sources.

### 5. Generations: identity from Wikidata, time from vPIC

- **Direct case**: a generation-shaped entity attached to a *matched
  model* creates a `generations` row under it — name from the label,
  chassis codes extracted from label/alias parentheticals ("(E46)");
  ambiguous extractions flag rather than guess. Span years only when the
  entity asserts dates; span-less generations are legal (identity now,
  time later).
- **Line case** (the BMW shape): generation entities of a *line* wait in
  raw. Instantiating them under each member model (E46 under `330i` by
  year overlap) is **deferred to the year-pass ADR**, where vPIC's year
  lists exist to overlap against — that inference needs the time axis
  this sweep doesn't carry, and doing it with sparse Wikidata dates
  would guess. Chains (P155/P156) order generations even where dates
  are absent.
- **The shared "all E46 cars" page is a view, not an entity** — the same
  species as a line, one level down (the review's framing: membership-shaped
  and far less load-bearing than the FK spine). Under as-filed models
  the E46 concept spans several per-model generation rows (`330i`/E46,
  `M3`/E46, …), and the page renders over `generations.chassis_codes @>
  ['E46']` — the GIN-indexed array built for exactly this query on day
  one. ADR 0009's "the generation page stays the one shared page"
  carries forward with its mechanism updated: aggregation pages are
  queries over the spine (lines over models, code pages over
  generations); only the five-level FK chain is load-bearing.

### 6. What this ADR does NOT do

- No catalogue periods, no configurations — the year-pass ADR consumes
  the generations this one creates.
- No Wikipedia scraping; the sweep stores the sitelink pointer only.
- No fuzzy matching anywhere; every ambiguity is a flag with candidates.
- No platforms (pid unverified — zero P4243 uses; re-probe first).

## Consequences

- A migration for `model_lines` / `model_line_members` (provenance
  columns per the charter's fact-table rule).
- `RECONCILER_VERSION` bumps; the sweep lands ~14.6k raw records
  (bare-QID, `sweep: models`). In v1 `models` does **not** grow — the
  pass enriches matched US models (summary, QID), builds lines and
  memberships, and creates direct-case generations; the ~9k unmatched
  model entities wait in raw as the expansion's stocked warehouse.
- The duplicate-entity disease (four "BMW 3 Series") meets the same cure
  as companies: curated `IDENTITY_MERGES`, model-level registry, grown by
  resolving flags.
- The year-pass ADR unblocks the day this lands: periods hang under
  §5's generations, and the line-case instantiation happens there with
  vPIC years in hand.


---

## Amendment (2026-08-19): §7 — the mint registry

- Extends: the decision above, whose rule this narrows ("match and enrich,
  never create"); ADR 0010 §1 (one open question must not fan out into
  model-shaped copies); ADR 0011 §4 (external ids on 1:1 correspondence)
- Ruled 2026-08-18: fill the European marques, so their pages hold real
  catalogues instead of an empty table under a real company.

### The problem

Every model row is vPIC-born — 1,735 of 1,735 carried a vPIC filing id when
this was censused — and vPIC and EPA are registries of the United States
market. A marque that never sold there can never earn a model row from any
landed source, however many queues are worked. That is why 7,072 of 7,201
companies hold no models, and why Citroën, Škoda, SEAT and Dacia render as
empty catalogues while the sweep already holds their nameplates: 8,217
`waits_unmatched` entities resolve their maker to a company we hold (Citroën
126, Škoda 146, SEAT 92, Renault 203, Opel 104, Lancia 56, Dacia 34, ...).

The two obvious mechanical rules both fail the same census:

- **"Maker resolves to a held company" over-mints.** 519 entities resolve to
  General Motors and 318 to Ford Motor Company — group entities whose models
  live under their brands. A rule keyed on maker resolution alone would mint
  Chevrolets under the group.
- **"The company holds zero models" is time-asymmetric.** True on the run
  that fills the company, false on every run after, so the same rule admits
  an entity today and refuses its sibling tomorrow.

What a mechanical rule cannot carry, a registry can: which companies deserve
the fill is a judgment, recorded per company, with the census reviewed before
each addition.

Terms. To **mint** is to create a `models` row. **Label duplicates** are distinct
entities sharing one label under one maker — usually different-era cars
sharing a nameplate (the census found four "Škoda Rapid" and three
"Citroën C6").

### Decision

1. **A mint registry.** `WIKIDATA_MINT_COMPANIES` (company QID → slug, the
   value documentation only) names the companies whose sweep entities may
   mint. Resolution goes through `external_ids` like every maker, so an alias
   QID of a listed company gates the same. Grown only by ruling.
2. **Per-entity conditions**, each an under-admission (a skipped entity keeps
   waiting; widening costs one review): sole asserted maker; a real label,
   not the bare-QID fallback; no P179/P361 membership evidence — level is
   structural, never label, and such an entity may be a generation of
   something unheld; no excluded word (`WIKIDATA_MINT_EXCLUDE`) — concept,
   prototype and race-only cars are the charter's open scope question and
   wait for its ruling; the label does not wear another held marque
   ("Fiat 850" files under Abarth with a label that says whose car it is);
   the stripped name is not the company name itself.
3. **Contested slugs never mint.** Label duplicates flag as a group — unlike the
   vPIC collision rule (ADR 0010 §2.3, lower filing keeps the slug), because
   there the colliding records are one nameplate seen twice, while duplicates are
   different cars: minting any one would enthrone an arbitrary era at the
   plain address. Which duplicate deserves the plain name, and what the others are
   called, is one naming ruling per group. A slug worn by an existing model
   the entity did not name-match flags with the occupant as candidate — the
   accent-divergence case (vPIC "Mehari", label "Méhari") is either the same
   nameplate for `WIKIDATA_MODEL_MATCHES` or a genuine duplicate.
4. **A minted model's name is the prefix-stripped label**, asserted with
   provenance under `MINTED_MODEL_COVERAGE` — the one case Wikidata names a
   model, because here it is the filing source. The QID becomes the model's
   filing id for registry purposes (`_model_key` falls back to it), so minted
   models can be curated and negated like any other.
5. **Minting creates identity only.** No catalogue periods, no generations,
   no configurations: time still arrives from catalogue-bearing sources, and
   a minted model legitimately renders with an em-dash year range until one
   covers it.

### Consequences

- The pilot registry lists twelve companies: nine empty European marques
  (Citroën, Škoda, SEAT, Dacia, Abarth, Cupra, DS, Alpine, Vauxhall) and
  three deliberately mixed ones (Renault, Opel, Lancia) as the US/EU overlap
  probes — their vPIC-born filings and Wikidata-born mints now share a
  catalogue, which is where naming and identity issues will first surface.
- The duplicates queue is new review work with a naming-ruling shape; resolutions
  land as era-qualified names or `WIKIDATA_MODEL_MATCHES` entries.
- Un-listing a company stops future mints but keeps existing rows (they hold
  QID external ids and refresh like any matched model). Retiring minted rows
  is a decision script plus registry entries, the demote-non-generations
  shape, if ever needed.
- The empty-company page copy stays honest either way: it names our coverage
  limit, not a claim about the marque.

---

## Amendment (2026-08-20): §7 — duplicate rulings resolve through a registry

The 2026-08-19 ruling: same-name different-era cars share ONE model row
under the plain nameplate, and the eras separate as dated generations —
never as era-forked names. `WIKIDATA_DUPLICATE_NAMEPLATES` records the per-
entity verdicts, reviewed as named cars:

- `model:<company>/<slug>` — this entity IS the nameplate: it mints or
  adopts the model row and attaches at model grain; its own single era
  arrives through the lead-era mint (ADR 0017 amendment of the same date).
- `era:<company>/<slug>` — this entity is one era: it becomes a generation
  under that nameplate, named by its article's era parenthetical (or its
  span), linked and attached 1:1. Time itself is asserted by the Wikipedia
  pass from the era's own article — this rung sets identity only. The
  nameplate model is created bare when no member owns it (no entity means
  the nameplate; ADR 0011 §4). An era whose only distinguisher is its span
  lands **unaddressed** — production time is a fact, not identity
  (ADR 0019 §4) — and an era with no article span and no Wikidata date
  waits visibly at `duplicate_era_awaits_span`.

The rung runs before the mint groups, so a ruled member never contests;
unregistered members keep contesting exactly as the pinned tests demand,
which is how concurrent-market duplicates (Kamiq China, Rapid China/India) and
unresolved identities stay open for their own rulings. Flags dismiss with
recorded resolutions. Applied 2026-08-20: 27 entities resolved across 21
nameplates; 48 ruled eras await evidence; 52 flags remain open.
