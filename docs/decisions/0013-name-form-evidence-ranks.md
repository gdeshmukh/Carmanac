# ADR 0013 — Name-form evidence ranks: labels over aliases, and the cross-badge guard

- Status: Accepted (2026-07-31, direction approved in review; refined by the
  attached-match audit)
- Date: 2026-07-31
- Depends on: ADR 0011 (as-filed models; 1:1 external ids), ADR 0012 (the
  models sweep and its ladder), ADR 0005 (`vehicle_derivations`, the
  `rebadged` type)

## Context

Working PR #25's cluster queue surfaced a second phenomenon hiding inside the
shared-match flags: **badge engineering**. Wikidata records a car's rebadges
and market names as *aliases* on one entity — measured live:

- `Q85872511` label "Toyota Raize", P176 → Daihatsu (they build it), aliases
  "Daihatsu Rocky" / "Perodua Ativa" / "Subaru Rex". Its **alias** joined it
  to Daihatsu's as-filed `rocky` cluster.
- Of the 47 shared-match clusters, only **14 are label duplicates** (several
  entities carrying the bare nameplate label — the four-"BMW X5" species).
  **33 are alias-contaminated**: the true nameplate hit via its *label*, the
  extra claimants only via *aliases* — rebadges (Raize, FR-S, GR86, Solterra,
  Lexus LX) and market names (Hilux Surf, Kluger, Navara, Harrier).

The ruling (review, 2026-07-31): rebadges are **different cars** — same
underneath, sold under another badge (the VW-group shape) — and must resolve
into their own brand's model buckets, with the relationship recorded as a
`vehicle_derivations` `rebadged` fact once both sides exist, never as a
shared model row.

An audit of the 387 already-attached matches then **refined the rule**. 37
attached via alias only, and they are three species, not one:

1. **Stripping artifacts (~17, benign)**: "Audi A3" → `audi-ag/a3` counts as
   alias-only ONLY because the company row is named "Audi AG" and prefix
   stripping used `companies.name` alone — the label hit was there all along.
2. **US-market names (~15, correct)**: the alias IS the as-filed US name of
   the same car — Renault 5 → `lecar`, Toyota Yaris (XP10) → `echo`, Hilux →
   `pick-up`, Sunny → `sentra`, Carens → `rondo`. Blocking aliases outright
   would flag ~30 true matches into the review queue for ~1 true negative.
3. **Cross-badge rebadges (1 live)**: `Q133885141` "Subaru Trailseeker"
   attached to `toyota/bz-woodland` — a Subaru-badged car on a Toyota row,
   the Raize mechanism landing uncontested.

## Decision

### 1. Prefix stripping uses the company's recorded names, plural

The make-prefix-stripped form (ADR 0012 §2.3) strips any of: the company's
`name`, and the **vPIC make name(s)** attached to it via `make:` external ids
("AUDI" for "Audi AG", "VOLVO" for "Volvo Cars"). Both are recorded data we
hold — this is more mechanical evidence, not fuzz. Display names for lines
and generations use the same prefixes, which retires the "Audi Q3"-line wart
for matched makes.

### 2. Name forms rank: label evidence outranks alias evidence

Rung 3 tracks which form carried each hit. **Label forms** (label, stripped
label) are the entity saying what it is; **alias forms** are the entity
listing what it is also called — including its rebadges, which is exactly why
they cluster.

- **Cluster resolution**: among a model's claimants, if exactly ONE hit via a
  label form, it is the 1:1 correspondence and attaches. Alias-form claimants
  no longer cluster — each gets its own `match_review` flag, reason
  `market_name_or_rebadge`, naming the model, the alias that hit, and its
  co-claimants. Two or more label claimants → the label-duplicate cluster flag as
  before (now the only thing `shared_model_match` means). Zero label
  claimants → every claimant flags `market_name_or_rebadge` (the
  Feroza/Rugger shape: both are "aka Rocky"; a human picks via the registry).
- **Uncontested alias-only hits attach** — the Echo/LeCar species is real and
  common — with the method recorded (`alias`/`alias-stripped` in the decision
  log), so "every alias-carried attachment" stays one query, repeatable as
  the standing audit.

### 3. The cross-badge guard

An alias-form hit whose entity **label carries a different held company's
brand prefix** than the matched model's company (Trailseeker → Subaru vs
`toyota/…`; Raize → Toyota vs `daihatsu/…`) never attaches, contested or
not — it flags `market_name_or_rebadge` with `cross_badge: true`. A company
whose name extends the model company's own prefix (BMW → BMW M) is the same
brand family, not foreign. Resolving a cross-badge flag grows the negative
registry, and the pair is the future `vehicle_derivations` `rebadged` fact
(ADR 0005) once both sides exist as rows.

### 4. The decision log preserves how a match was made

Rung-1 refreshes no longer overwrite `match_decisions.method` with
`external_id`: a refresh keeps the method that made the original match (the
audit had to reconstruct it offline; recorded once is better).

### 5. One-time derived-state refresh

Applied with this change, while generations and lines have no consumers (the
year pass is unbuilt — deliberately before it):

- The cross-badge attachments the audit found (today: the Trailseeker) are
  unwound — external id and this pass's assertions removed, the entity
  re-processed under the new rule into a flag.
- Lines and generations are rebuilt by re-run so their names and slugs pick
  up §1's stripping ("audi-a3-8v" → "a3-8v"). Raw records and the labeled
  set are untouched; this is reconciler-derived state, rebuilt from raw.

## Consequences

- `RECONCILER_VERSION` → 10.
- The cluster queue becomes what it says: ~14 label-duplicate questions. The
  market-name/rebadge queue is new, explicit, and sorted by `cross_badge`.
- Matcher recall is preserved (market-name matches keep attaching);
  cross-badge precision is enforced by construction.
- The market-name pairs the flags accumulate are the feedstock for two parked
  questions: model-level curated merges (ADR 0011 §5) and rebadge derivation
  facts (ADR 0005).
