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

## Amendment (2026-09-11): the bare-title tie-break, and the generation form

Two label claimants on one model were a guess only while nothing told
them apart. Censused over the 51 open clusters, two facts the source
itself states do, and neither is a name match.

- **The bare title.** An entity's English sitelink names its article, and
  Wikipedia gives the plain title to the primary topic of a name ("BMW
  M3") while every other page of that name wears a parenthetical ("BMW M3
  (E30)", "Chevrolet Impala (fifth generation)"). Among a model's label
  claimants, the one whose sitelink title equals its label is the
  nameplate, provided it is the only one.
- **The same-name succession.** A chain edge (follows or followed by,
  stated by either side) to another entity whose label, minus a trailing
  parenthetical, is the claimant's own label makes the claimant an era of
  that name. A nameplate is chained to other names (the Impala follows the
  Bel Air); its generations are chained to each other. An edge through a
  different name explains nothing: the European Escort and the American
  one share the Focus as successor and stay two questions.

The cluster resolves when exactly one claimant carries the bare title and
every other claimant is an era. The nameplate attaches, method
`bare_sitelink_title`; the eras wait - no row, no link, no flag, decision
`chain_generation_waits` naming the nameplate and the succession. An era's
only identity key is its own page title, and it adopts the linked
generation wearing that name or code once one exists (ADR 0017 §4, amended
the same day). Anything else keeps the cluster flag: a second bare title
(Saab 9-3 beside Saab 93), an unexplained claimant (the Rapid E beside the
Rapide), no bare title at all (the Pacifica's crossover, minivan and
concept pages). A returning label claimant on a model that already has its
id is an era by the same test, or the second-id question it was; a bare
title returning there is always that question. The cluster flag lives on
the first claimant's record; a copy left on another record by an earlier
run is dismissed, so one cluster is one open question.

Live at adoption: 11 of the 51 resolve (the M3, Mustang, Thunderbird,
Accord, Impala, Liberty, Vantage, 300, XK, GTO, Corniche); 40 stay
flagged - 26 hold a claimant with neither succession nor bare title, 11
have no bare-titled claimant, 2 have two, and one is the Volt's duplicate
flag, dismissed. Those are the concurrent-market and duplicate-nameplate
shapes the duplicates registry (ADR 0012 §7) rules on, and the Fiat 500
and Beetle pairs among them keep waiting for that ruling rather than
taking the original car's page as the nameplate mechanically.

**The generation form.** A label reading `<model> (<parenthetical>)`, exact
under the model's own company after prefix stripping, is a generation of
that model when the parenthetical states what a section heading states:
chassis codes (the heading grammar's token rules, so a market or body word
is not a code) or `<ordinal> generation`. It is a name form below label
and alias, and it never claims the model. Its parenthetical is its
adoption key. A parenthetical of years, a market or prose ("Chevrolet
Trailblazer (2012)", "Toyota Camry (XV40, Asia)") is not the form: no
section can identify it, and it stays a near-miss with candidates. Under a
mint-registry company the form yields to ADR 0012 §7: an era sibling
there is the duplicates ruling's question. Censused over the 1,535 open
near-miss flags: 119 labels carry a parenthetical, 99 name a held model
exactly, 36 by code and 14 by ordinal.

A series member whose label is the bare nameplate ("Ford Mustang", filed
as part of the Mustang) is a generation wearing the model's own name, and
a generation is never named like a model (ruled 2026-08-21): its row takes
its name from its sitelink title, "Mustang (fifth generation)", the page
that carries the parenthetical the label lacks.
