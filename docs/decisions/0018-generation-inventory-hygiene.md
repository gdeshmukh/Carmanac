# ADR 0018 — Generation inventory hygiene: wrong-grain demotion and the Main-target fetch

- Status: Accepted (2026-08-07)
- Date: 2026-08-07
- Depends on: ADR 0017 (section-minted generations, the placement pass and
  its guards — both halves here extend its machinery), ADR 0016
  (`generation_model_links` as the candidate gate), ADR 0007 §5 (curated
  registries are recorded human judgments), ADR 0004 (distrust never
  justifies deletion)

## Context

Two defects in the generation inventory, both censused on the 2026-08-07
branch review of PR #35:

- **Wikidata's P179 minted trim/body lineages as generations.** Six rows
  of the species: `911-gt2` (span 1993–2019 — wide enough to drive 213 of
  the 480 open `generation_overlap` flags), `911-targa`, `celica-gt-four`
  (the undated-competitor lesson's own culprit), `civic-hybrid`,
  `civic-type-r-fl5`, `5-turbo`. Ruled 2026-08-07: **the 911 GT2
  (Q1752875) and 911 Targa (Q124935918) are not generations** — one is a
  trim lineage across five real generations, the other a body style. The
  other four are not ruled. Verified live before this ADR: each of the six
  holds exactly one live `generation_model_links` row, all
  Wikidata-asserted; zero placements cite any of them; zero open flags sit
  on the rows themselves.
- **69 section-born generations are undated**, so their models' placements
  wait. 17 of them (including all four MX-5s) have sections that carry a
  `{{Main}}` pointer to a per-generation article — the article shape where
  the nameplate page defers content to sub-articles instead of carrying
  section infoboxes. The dates exist on Wikipedia; the sections pass just
  never fetched the page they live on. The other 52 are thin sections —
  they wait for sources, not code.

A live probe of the 17 targets' shapes (2026-08-07) found two grain traps
the fetch must close: `spectra-first-generation` defers to **Kia Sephia**,
a whole-nameplate page whose `production` (1992–2003, both Sephia
generations) parses cleanly and would land wrongly — the Civic Hybrid
redirect lesson one hop over, arriving without a redirect; and `q60-v36`
defers to `Infiniti G Line#G37`, a fragment pointer into another nameplate
article. Also found: per-generation targets routinely write their ranges
with dash *templates* (`October 2010 {{nbndash}} September 2017`), which
the span parser read as `years_without_range`.

## Decision

### 1. `NOT_A_GENERATION` — a policy registry, because scripts cannot outvote passes

A registry + script **pair**, never a script alone: the wd-models pass
re-asserts links from P179 on every run, so a script that only retired the
live rows would see them lawfully resurrected at the next re-run. The
policy gate is what makes the retirement stick.

- `NOT_A_GENERATION: dict[str, str]` in `carmanac/reconcile/policy.py`
  (the `IDENTITY_MERGES` / `WIKIDATA_MODEL_NEGATIVES` species): QID →
  verdict slug (`trim_lineage`, `body_style`). Each entry is a recorded
  human judgment. Seeded with the two ruled QIDs **only**; the unruled
  four enter if and when ruled, never by implication.
- **The wd-models pass** stops asserting `generation_model_links` for
  registered QIDs and stops refreshing them as generations (decision
  outcome `held_not_a_generation`). Their assertions stay live in
  `field_provenance` and their records stay in raw — nothing is deleted;
  distrust of *grain* is not distrust of the facts (ADR 0004's rule,
  applied one level down).
- **The sections pass** excludes registered generations from
  code-intersection reconciliation: a section must never resolve onto a
  row ruled not-a-generation, and a wrong-grain row must not block a real
  section from minting.

### 2. The demotion script, behind the standing dry-run gate

`scripts/decisions/demote_non_generations.py`, dry-run by default:

- The dry-run prints, per registry entry AND per unruled member of the
  censused species (presented as a proposal with the same evidence, no
  verdict): the generation row, its span and codes, every live link with
  its source, every placement citing it, every open flag on the row.
- `--execute` applies **registry entries only**: live links retire by
  self-supersession (`superseded_by = id`, the role-retraction pattern);
  open flags on those generation rows resolve with the verdict recorded.
  The run happens only after the dry-run list is reviewed — the standing
  merge-gate procedure.
- **The rows, their external ids, and their facts all stay.** They are
  real entities of a kind not yet modeled (trim lines / derivations —
  future ADR, explicitly not this one). Demotion is link retirement, not
  deletion.
- **Overlap flags are not touched.** With the links retired, the placement
  pass's existing `candidates_no_longer_overlap` path dismisses them
  mechanically on its next run — the flags close because the world
  changed, not because a script edited them.

### 3. The Main-target fetch: `section-main:<QID>#<ordinal>`

For **minted section-born generations only** (never reconciled sections —
those defer to an attached generation whose own article already feeds §2),
whose section carries a `{{Main}}` pointer: fetch the target page's
section-0 wikitext and land it as `section-main:<QID>#<ordinal>` beside
the `article:`/`infobox:` records — kind readable from the namespaced id,
never from payload shape. Identity stays inherited: the pointer is
structural parsing inside an article already reached through the model's
QID; no name matching is introduced.

- **Fetch eligibility**: the section's `{{Main}}` targets reduce to
  exactly one distinct title, containing no `#` fragment (a fragment
  points into another article's body — the target subject is not the
  page). Undated generations are fetched; a generation with an existing
  `section-main` record stays in the target set so refresh runs keep it
  current. Same lander module family, `PoliteClient`, honest UA,
  resumable commits (~16 requests today).
- **Grain guards, both applied at read time (pass and placement loaders
  alike)**: a redirected target asserts nothing (§2's rule verbatim), and
  a target asserts nothing unless its **resolved title carries a trailing
  parenthetical** — the per-generation-article convention §2 already
  treats as an assertion (`Mazda MX-5 (NA)`, `Toyota Prius (XW10)`,
  `Nissan Leaf (first generation)`). A bare-title target (`Kia Sephia`,
  `Kia Cerato`) is a nameplate/rebadge deferral whose section-0 speaks at
  the wrong grain; its record stays archival and the refusal is logged.
- **Fact sourcing**: the section's own infobox remains first; the landed
  target supplies what the section itself lacks — the production span
  through the same flag-never-guess parser (labeled-defer amendment
  included; parse failures flag `implausible_value` as ever), chassis
  codes via the title-parenthetical extractor where the heading gave
  none. Provenance on every target-sourced assertion points at the
  `section-main` record — per-field, so the trail says exactly which
  record asserted what. Heading years still never become spans.
- **Placement's decision-time loaders extend the same way**: `model_years`
  and body doors for these generations read from the `section-main`
  record when the section itself has none (door precedence: section body,
  then target, then the article's top infobox — most specific claim
  wins).
- **Dash templates normalize before span parsing**: `{{ndash}}`,
  `{{nbndash}}`, `{{mdash}}`, `{{snd}}` rewrite to the dash they render
  as. This is typography, not interpretation — the flag-never-guess rule
  is untouched.
- **Adoption correspondence recorded, identity unchanged**: per fetched
  target, whether a sweep QID's sitelink matches the target title is
  noted in the decision detail — feedstock for the future Wikidata
  adoption pass (ADR 0017 §4's correspondence key). The wd
  `flagged_candidates` label-twin flags (where the MX-5's four
  per-generation QIDs sit) are queue work with their own arc and are not
  resolved here.

## Consequences

- After the reviewed `--execute`: GT2 and Targa hold zero live links, the
  wd-models re-run does not resurrect them, and the overlap queue
  collapses mechanically on the next placement run (the 911 alone carries
  213 of 480) — with some 911 rows starting to place into 964/997/991/992.
- The MX-5's NA/NB/NC/ND (and the Prius, R8, X1, Leaf species) gain spans
  with provenance to their `section-main` records; placement reaches
  their configurations through the existing §3 machinery unchanged.
- `scripts/status.py` gains the coverage funnel — models with
  configurations → QID-attached → landed article → linked generations →
  placed configurations — so placement coverage always shows its
  denominator.
- Reconciler v15 (policy and pass behavior change what runs produce).
- No schema change; no new flag kinds; alembic head unchanged.
