# ADR 0007 — Reconciliation: policy, mechanics, and the companies first pass

- Status: Accepted (2026-07-27)
- Date: 2026-07-27; amended 2026-07-28 after the foundation review (F5, F7)
  and a test-suite discovery: §1 gains the reconciliation unit, processing
  order, temporal semantics (tombstones, disappearance, merges) and the
  supersede operation; §8 turns the association tables into per-source
  assertion stores
- Formalizes: the reconciler policy decisions of 2026-07-24 (PROGRESS.md
  Session Log); applies ADR 0005's classification rule and ADR 0006's roles
- Depends on: ADR 0002 (field provenance), ADR 0003 (raw landing zone,
  external ids), ADR 0004 (retention), ADR 0005/0006 (what a make is)

## Context

7,222 Wikidata entities sit in `raw_scrape.raw_records` and nothing reads them.
The schema is reconciliation-ready (R1–R12): `field_provenance` enforces one
live assertion per (entity, field, source), `external_ids` maps source
identifiers to entities, and entity tables carry no provenance. What is missing
is the process that connects them — the reconciler is the substance of
ingestion, and this ADR is its contract.

Policy decided 2026-07-24 and formalized here: conflicts resolve by **tier
first, then field affinity, then recency, then flag**; flagged conflicts still
project a tentative winner so pages always show data; **`confidence_score`
stays NULL** until a real methodology exists; source is audit trail, never a
query dimension, with exactly two exceptions (external-id namespacing, tier
logic).

Two facts about the landed data shape the first pass:

- **Payloads carry no P31 class.** The query filters on `automobile
  manufacturer` / `car brand` but never selects the class, so a landed record
  cannot say which it is — or what *else* the entity is (KINTO Europe is
  presumably also classed as a mobility service; invisible to us). Class-based
  admission and role assertion therefore require widening the query and
  re-landing — cheap (~10s, hash-keyed, only changed payloads re-land) and
  exactly what the landing pipeline was built for.
- **The builder side of ADR 0005 is absent.** Singer Vehicle Design and
  Gunther Werks are not in the landed set (only Ruf, which holds manufacturer
  status). Wikidata does not class restomodders as manufacturers or brands;
  coachbuilder/tuner classes need their own fetch later. This pass produces
  manufacturers and marques only.

## Decision

### 1. The reconciler is a deterministic, idempotent projection

    raw_records  ──resolve──▶  companies + external_ids
                 ──assert───▶  field_provenance + company_role_assignments
                 ──project──▶  entity columns (the winner per field)

One record at a time: resolve identity, write assertions, project winners.
Re-running over unchanged records is a no-op; re-running over changed records
supersedes. The reconciler **never** modifies `raw_records` (ADR 0004) and
never deletes assertions — an assertion is retired by setting `superseded_by`,
so history is retained and `uq_field_provenance_live` keeps exactly one live
row per (entity, field, source).

Determinism matters because **re-reconciliation is the normal case**, not the
exception: when the admission lists, the matcher, or a field mapping improves,
the whole pipeline re-runs over records already on disk and must converge to
the same state from any starting point. That claim is only true with the
following pinned (amendment, F5):

**The reconciliation unit is the current record** per `(source, external_id)`:
the raw row with the greatest `last_seen_at` (ties broken by greatest id).
Historical rows are audit trail and re-derivation inputs, never reconciled
directly. A full pass processes entities in **ascending external-id order** —
a stated precondition of determinism, which doubles as the slug-collision
tiebreak: the lower QID claims the bare slug, so a from-scratch rebuild
reproduces incremental history exactly (rename semantics stay with the open
slug-strategy question).

**Retraction is a tombstone assertion.** When a source's current record no
longer carries a field that source previously asserted (within the mapper's
coverage), the live assertion is superseded by one with a NULL observed value
— "this source went quiet here." Projection then treats the source as silent,
and the tombstone dates the retraction. No schema change: `observed_value` is
already nullable.

**Disappearance is a flag, never an auto-retirement.** After a completed full
fetch, any current record whose `last_seen_at` predates the run's start was
not returned → a `source_dropped` flag opens on the entity. Deliberately not
automated further: a vanished QID is often a **Wikidata merge/redirect** —
the remaining open route to the duplicate-identity state ADR 0006 declared
impossible (0006 closed only the two-table route). Working the flag includes
checking for a redirect; automatic entity merging is deferred to its own ADR.

**The supersede operation has exactly one order** (discovered by the test
suite, `test_supersession_allows_history_and_one_live_row`): the naive
sequence — insert the new live row, then repoint the old — is impossible
under `uq_field_provenance_live`, which rejects the second live row before
the old can be repointed, while the old cannot reference an id that does not
yet exist. The engine's single supersede helper runs, in one transaction:
(1) retire the old row by pointing `superseded_by` at itself, freeing the
live slot; (2) insert the successor; (3) repoint the old row at it.

### 2. One engine, one mapper per source

There is **one reconciliation engine**, source-agnostic, and a **thin mapper
per source**. The engine owns everything hard and shared: the identity ladder,
admission, conflict resolution, projection, supersession, flags, and state
tracking. It never inspects a payload.

A mapper answers exactly three questions about one raw record:

    external identity   which (source, external_id) is this about?
    admission signals   e.g. the P31 class set (§3)
    field assertions    [(field_name, observed_value, typed_value), ...]

Adding a source is therefore writing one mapper plus registering its field
affinities in `policy.py` — the engine is untouched. When NHTSA vPIC lands,
`vpic.py` is a small translation module, not a second reconciler; the
resolution rules automatically arbitrate between it and Wikidata because both
speak in assertions. This is also what keeps the system learnable as it grows:
each source is a small reviewable unit, and the engine is understood once.

Layout: `carmanac/reconcile/` — `engine.py`, `policy.py` (registries),
`sources/wikidata.py` (v1's only mapper).

### 3. Admission: strict by default, branching outwards

**The cars are the priority; the frontier expands from them by deliberate
review.** A raw record produces a `companies` row only when its P31 class set
is affirmatively understood:

- **Admit** — every class in the set is either a target class (`automobile
  manufacturer`, `car brand`) or on the **vetted co-class allow-list**
  (business, public company, brand, division, marque, …).
- **Exclude** — any class on the **deny-list** (mobility/car-sharing services,
  assembly plants, holding companies, importers/distributors, …): not
  admitted, waits in raw.
- **Quarantine** — any class we have not classified yet: not admitted, and an
  `admission_review` flag opens for the record. Reviewing quarantine flags
  grows the allow- or deny-list; a re-run then applies the verdict.

Unknowns quarantining rather than admitting is the deliberate polarity.
Under-admission is the cheap error — edit a list, re-run, the entity appears;
nothing was ever discarded. Over-admission is the expensive one: entity rows
to unwind after other data references them. So `companies` starts as the
clean car core and only grows outward on purpose.

This requires the query widening (context above): land each entity's full P31
class list. The same widening also lands each country's **ISO 3166-1 alpha-2
code** (P297), replacing fragile label matching ("United States of America" vs
"United States") with a mechanical join to the `countries` lookup.

The allow/deny lists and the field-affinity registry live as reviewed
constants in `carmanac/reconcile/policy.py` — version-controlled, changed via
PR, applied via re-run. The lookup-table pattern is well established here if
they ever need runtime editing; nothing requires it yet.

### 4. Roles: asserted from Wikidata classes, arbitrated by vPIC later

Admitted entities classed `automobile manufacturer` (Q786820) **or** `car
brand` (Q10429667) get a `manufacturer` role assertion in
`company_role_assignments`, with provenance pointing at Wikidata.

Both classes deliberately map to one role. Wikidata's company/marque split is
its own modeling artifact — in its model the "manufacturer" is the corporate
legal entity (General Motors), so Pontiac is only ever a "car brand" there.
Yet Pontiac held WMI `1G2`, its cars were titled as Pontiacs, and ADR 0005's
own pass-list names it a make. Our definition (manufacturer responsibility,
ADR 0005) with their evidence; splitting hairs the source cannot support would
just misfile the 708 brand-only entities.

The assertion is **tentative the way every assertion is**: when NHTSA vPIC
lands WMI data, agreement corroborates and disagreement raises a flag
(ADR 0006: "a reconciliation flag, not a silent overwrite"). Roles are never
inferred from catalogue structure (ADR 0005 as amended).

### 5. Identity resolution: exact only, in v1

The ladder for "which entity is this record about":

1. **`(source_id, external_id)` hit in `external_ids`** → that entity. For
   Wikidata this is the QID, and it is authoritative.
2. **Miss** → create the entity (if admitted, §3) and write the `external_ids`
   row in the same transaction.

**No fuzzy auto-matching in v1.** The trigram indexes exist for candidate
generation, but nothing auto-merges on name similarity until matcher precision
is measured on a labeled set — the risk register's own rule ("do not add
Tier 2/3 sources until matcher precision is measured"). Within a single source
the external id is sufficient; fuzzy matching only becomes load-bearing at the
second source, which is when the labeled set must exist. Resolved
`admission_review` and conflict flags accumulate exactly that labeled data.

### 6. Conflict resolution per (entity, field)

Collect live assertions for the field, then:

1. **Tier** — lowest tier number wins (Tier 1 beats Tier 2).
2. **Field affinity** — same tier: the field's registered authoritative
   domain wins (EPA owns fuel economy, NHTSA owns body/safety, Wikidata owns
   identity and historical facts). Registered per field in `policy.py`, not
   hard-coded in resolution logic.
3. **Recency** — still tied: latest `scraped_at` wins.
4. **Flag** — different sources, same tier, no affinity, values disagree:
   project the recency winner anyway (tentative — pages always show data)
   and open a flag for review.

The projected column is always derivable from live assertions plus this
ordering. `confidence_score` is written NULL throughout (review #6 stays
open); no tier-restated-as-a-decimal placeholders.

### 7. Field mapping for the companies pass

| payload | assertion field | projection |
| --- | --- | --- |
| `itemLabel` | `name` | as-is |
| `itemDescription` | `summary` | as-is (`description` stays NULL — needs a real prose source) |
| `inception` | `founded_year` | year component; `observed_value` keeps the full date |
| `dissolved` | `defunct_year` | year component |
| `countries` + ISO codes | `country_id` | joined via `countries.code`; **exactly one** country projects, several → no projection + flag, zero → no assertion |
| `websites` | `website` | first after canonical sort if one; several → flag (new column, see Consequences) |

Slugs are **derived identity, not asserted facts**: `slugify(name)`, with the
QID appended on collision (`singer-motors`, `singer-q2001596`) — deterministic
across re-runs, no source writes them. Slug immutability/redirect policy stays
with the open slug-strategy question; v1 does not retroactively re-slug.

### 8. Reconciliation state and flags (new schema)

- **`reconciled_records`** — sidecar, not columns on `raw_records` (the
  landing zone stays untransformed source data, ADR 0003):
  `raw_record_id` PK/FK, `reconciled_at`, `reconciler_version` (text — which
  policy/code version processed it, so re-reconciliation can target stale
  records mechanically).
- **`reconciliation_flags`** — the review queue's storage (review #9, first
  version): exclusive-arc entity FKs (same seven-column idiom as
  `field_provenance`), nullable `field_name`, `kind`
  (`field_conflict`, `multi_value`, `role_disagreement`, `admission_review`,
  `source_dropped`), `detail` JSONB, `status` (`open` / `resolved` /
  `dismissed`), `created_at` / `resolved_at`, plus `source_id` /
  `raw_record_id` where a specific assertion triggered the flag.
  **Entity-scoped kinds set exactly one arc column; `admission_review` is
  record-scoped** — no entity exists yet, so the arc is all-NULL and
  `raw_record_id` is required instead (the CHECK encodes both shapes).
- **Association facts become per-source assertion stores** (amendment, F7).
  `company_role_assignments`, `configuration_engines`,
  `configuration_transmissions` and `vehicle_derivations` reproduced, at row
  level, the defect ADR 0002 fixed for entity columns: one row per fact with
  a single `source_id`, so a second source *corroborating* the fact had
  nowhere to land — structurally blocking §4's vPIC arbitration. Each gains a
  surrogate id, `superseded_by`, and a partial live-unique index over
  (fact columns, `source_id`), NULLS NOT DISTINCT, `WHERE superseded_by IS
  NULL` — `field_provenance`'s exact shape. Presence-shaped facts need no
  winner projection: the fact holds if ANY live row exists (`EXISTS` /
  `DISTINCT` at read time), corroboration is simply multiple live rows, and
  per-source retraction is supersession. All four change together while they
  hold only seed data. **EAV stays winner-shaped**: a value-shaped fact needs
  one displayed answer, so `configuration_attributes` keeps its single live
  row per (configuration, attribute) as the projected winner, and its
  multi-source assertions land in `field_provenance` (configuration arc,
  attribute key as `field_name`) — §6's projection machinery, unchanged.

Flags never block projection (§6.4) and resolving one records a human
decision — which later feeds the matcher's labeled set.

## Consequences

- **Order of work:** per-source association stores (F7 migration, done with
  this amendment) → widen the Wikidata query (P31 classes, country ISO
  codes) and prune + re-land → migration (`reconciled_records`,
  `reconciliation_flags`, `companies.website`) → `carmanac/reconcile/`
  (engine + wikidata mapper) → run the companies pass → verify a
  hand-checked sample (~50 marques, mainstream + defunct + edge cases) before
  touching models.
- `companies` gains a `website` column — same justification as R12's prose
  columns: Wikidata already returns it, entity pages want it, and discarding
  at reconcile time is the only alternative.
- Expected output: the clean car core — admitted companies each with
  name/summary/founded/defunct/country where Wikidata has them, a QID in
  `external_ids`, a `manufacturer` role assertion, and every field traceable
  to its raw record. A likely-substantial quarantine queue is the *intended*
  result of the strict polarity, and working it down is how the allow/deny
  lists — and eventually the matcher's labeled set — get built.
- KINTO-shaped entities that survive admission carry a wrong tentative
  `manufacturer` role until vPIC arbitrates. Accepted: strict admission is
  the primary defence, the flag machinery is the correction path, and the
  alternative (roleless site for weeks) was rejected.
- The first pass produces no builders (Wikidata class gap). A
  coachbuilder/tuner-class fetch is future ingest work; ADR 0005's derivation
  schema sits ready for it.
- Re-reconciliation is now cheap and normal: policy changes are PRs followed
  by a re-run, and `reconciler_version` makes staleness queryable.
- Deferred, unchanged: fuzzy matching (needs labeled set), `confidence_score`
  methodology (#6), multilingual labels, the `platforms` entity (ADR 0005 §5).
