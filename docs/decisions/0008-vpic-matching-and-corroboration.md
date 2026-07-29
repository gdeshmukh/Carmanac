# ADR 0008 — vPIC make matching and cross-source corroboration

- Status: Proposed
- Date: 2026-07-29
- Depends on: ADR 0005 (what a make is), ADR 0007 (reconciler contract,
  QID-exact identity, no fuzzy auto-merge until a labeled set exists)

## Context

247 vPIC passenger-vehicle makes sit in `raw_scrape.raw_records` (the
`GetMakesForVehicleType` union for cars and MPVs) alongside 7,226 companies
reconciled from Wikidata. Connecting them is the project's **first
cross-source entity resolution**: vPIC's `TESLA` (MakeId 441) and Wikidata's
Q124981765 must resolve to one company without creating a duplicate — the
exact failure mode `external_ids` was designed to prevent.

The connection is also the review queue's main hope. Decided 2026-07-29:
**presence in an authoritative source is inherent authentication** — a make
that legally sells vehicles in the US (vPIC is regulatory VIN data, not
crowd-sourced) does not need a human to confirm it is a car company. The
2,347-record quarantine queue shrinks by evidence first, manual review
second, and the review UI stays deferred until corroboration has done its
work.

ADR 0007 §5's constraint binds: no fuzzy auto-merge until matcher precision
is measured on a labeled set — and that labeled set does not exist yet. This
ADR must produce it, not presuppose it.

## Decision

### 1. Matching ladder: exact, registry, flag — never fuzzy-auto

For each vPIC make (ascending MakeId, the determinism convention):

1. **Registry hit** — a curated `VPIC_MATCHES` map (MakeId → Wikidata QID) in
   `policy.py`, the same reviewed-constant pattern as `IDENTITY_MERGES`.
   Human judgments, version-controlled, applied by re-run.
2. **Exact normalized-name match** — casefolded, punctuation-stripped
   equality between the vPIC make name and `companies.name`, accepted only
   when exactly ONE company matches. vPIC names are uppercase legal-ish forms
   ("ASTON MARTIN"), so normalization is required but stays mechanical.
3. **Anything else flags** — zero matches or several (vPIC `TESLA` against a
   multi-Tesla companies table) opens a `match_review` flag on the vPIC raw
   record, carrying trigram-generated candidates in `detail` for the
   reviewer. Trigram similarity **generates candidates only**; it never
   accepts a match. Resolving these flags populates the registry — and is
   the labeled set ADR 0007 requires before any fuzzy automation.

A confirmed match (rungs 1–2, or a registry entry born from rung 3) writes
the `external_ids` row: vPIC MakeId → the company. One company, two source
namespaces, exactly as ADR 0003 intended.

### 2. Corroboration: what a match is evidence OF

On every confirmed match, the reconciler:

- **Asserts the `manufacturer` role from vPIC** (per-source row in
  `company_role_assignments`): appearing as a make in the US VIN system is
  ADR 0005's evidence — manufacturer responsibility — from the source that
  defines it. This is what settles the roleless pinned marques (Peugeot) and
  corroborates or contradicts every Wikidata-asserted role.
- **Auto-resolves open `admission_review` flags** on the matched company's
  quarantined records, with the resolution recorded as
  `corroborated_by_vpic` in `detail` — a machine decision, distinguishable
  from human ones in the future labeled set.
- **Admits matched quarantined entities on re-run**: a vPIC match is
  affirmative car evidence, joining target classes, builder classes, and
  pins as the fourth way in. (Quarantined Wikidata entities are matchable —
  their labels exist in raw payloads even where no company row does.)

No match ≠ demotion: a company vPIC has never heard of is normal (pre-1981,
never sold in the US). Absence of corroboration is silence, not contradiction
(the tombstone principle, one level up).

### 3. Scope of this pass

Make-level only. Models/model-years (`GetModelsForMakeYear`) are the next
ingest after matching holds; WMI-level evidence (`GetWMIsForManufacturer`)
joins when manufacturer-vs-brand arbitration needs it. Both inherit this
ADR's identity plumbing.

## Consequences

- The Peugeot-shaped role gap closes from evidence, not curation — the
  pinned marques that vPIC lists get their roles the honest way.
- The quarantine queue's US-market portion resolves mechanically; what
  remains is genuinely non-US or genuinely questionable — a queue worth a
  human's time, which is when the review UI becomes worth building.
- Every rung-3 resolution builds the labeled set that eventually justifies
  (or refutes) fuzzy matching. The registry is its storage.
- Risk accepted: exact-name matching against 7,226 companies may produce
  false uniques (one company coincidentally named like an unrelated vPIC
  make). Mitigated by the passenger-type scope (247 makes, reviewable in
  bulk) and by every match being visible in `external_ids` with provenance.
