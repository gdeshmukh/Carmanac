# ADR 0017 — Generation evidence: Wikipedia time, dated-overlap placement, and where existence comes from

- Status: §1–3 Accepted and implemented (2026-08-06, same branch as
  ADR 0016 — the machinery ran live and its first run's two lessons are
  folded in as amendments); **§4 accepted and implemented**
  (2026-08-07, its own branch, per the approved-in-principle ruling:
  probe-first on the full-article fetch and the section parse, with the
  keying/dedup/naming questions settled against real pages — the probe
  verdict and the settled rules are recorded in §4 below).
- Date: 2026-08-06 (§4 settled 2026-08-07)
- Depends on: ADR 0016 (company-anchored generations,
  `generation_model_links` as the candidate gate), ADR 0014 (placement
  is configuration-level and evidence-gated), ADR 0013 (evidence ranks),
  ADR 0012 (whose §existence rule §4 proposes to revise), ADR 0003/0004
  (raw landing and retention)

## Context

Every generation fact ultimately answers two questions: **does this
generation exist** (identity), and **when was it** (time). The sources
answer them very unevenly, measured live (2026-08-06):

- **Wikidata cannot supply generation time.** 936 of 14,524 model-sweep
  records carry any start date; 305 an end date; even the BMW E30 has
  none. Of the 151 generations it minted, 4 had spans.
- **Wikidata's generation *existence* is a thin mirror of Wikipedia's.**
  Its generation entities exist mostly where a per-generation Wikipedia
  article exists (they are sitelink shadows); where Wikipedia keeps
  generations as sections of the nameplate page, Wikidata usually has
  nothing — the Mercedes-AMG GT has zero generation entities despite a
  fully sectioned article (`#First_generation_(C190/R190)`).
- **Wikipedia holds the actual curation.** Per-generation articles carry
  infoboxes with `production` AND `model_years` (the US axis vPIC/EPA
  periods live on); nameplate articles carry per-generation *sections*
  whose headings follow a cross-marque convention — ordinal + chassis
  code + year, `First generation (XW10; 1997)` — each with its own
  infobox, often with deliberately maintained anchor spans
  (`<span class="anchor" id="S13">`).
- **The coverage funnel says existence is the choke point**: 1,114
  models carry configurations; 432 of them have a 1:1-matched QID with
  an enwiki nameplate article; only **51** have any linked generation.
  A 36-article sample of the 381 articled-but-generation-less models
  found ~1/3 with per-generation section infoboxes — extrapolating to
  roughly 125 more models whose generations are mineable today.
- autoevolution.com was evaluated as a segmentation source and is
  **parked**: robots.txt walls off AI/data crawlers wholesale and its
  terms page refuses programmatic fetches; a human read of the terms
  found them social-layer only, so the route back is a
  permission ask, not a differently-named crawler. It also has no QID
  join, so ingesting it would open the name-matching surface the
  Tier-2/3 gate guards. Manual reference use in flag review stays
  legitimate.

## Decision

### 1. Wikipedia enters through inherited identity, never through matching

Wikipedia is Tier 2, and the charter gates Tier 2/3 sources on measured
matcher precision. That gate is about *name matching* — and this
ingestion does none. Every article is reached through the sitelink
recorded on a QID that is already 1:1-attached to one of our rows;
identity is inherited, not inferred. Stated here so the first Tier 2
ingestion is a flagged, explicit non-violation.

The lander (`carmanac/ingest/wikipedia/`) fetches **section-0 wikitext**
per article (MediaWiki API, polite rate, honest UA) and lands it
untransformed as `infobox:<QID>` records — kinds readable from the
namespaced id, parsing kept in the pass, raw wikitext retained as the
Tier 2 archival record (a revision is not re-fetchable once the page
moves on). v1 scope: the 446 generation-relevant sitelinks (all landed,
zero dead links).

**Tier standing (ruled in review): tier never quarantines facts.**
Review queues exist for identity ambiguity, not source rank. Identity
here is settled by the sitelink, so infobox assertions write directly
through `field_provenance` — no review gate. For generation coding there
is effectively no first-hand source, so Wikipedia is the *primary*
asserter at this level by design.

### 2. The time-and-codes pass

- `generations.start_year` / `end_year` from the infobox **production**
  span (open end = still in production). The **US model-year span** is
  deliberately not stored — no column for a US-specific reading — and is
  parsed from raw at placement time.
- `generations.chassis_codes` from the **article title parenthetical**
  through the same strict extractor the label pass uses (`BMW 3 Series
  (E30)` → E30); prose parentheticals assert nothing.
- **Precedence, with mechanical teeth**: the generation facts Wikidata
  supplied were parsed out of *labels* — extraction from a naming
  string, not a deliberate claim — and tier-first arbitration would let
  them beat an infobox. Ruled: for generation-level fields, infobox
  assertions supersede label-derived ones. The wd-models pass keeps its
  assertions current in `field_provenance` but skips projecting fields
  the infobox pass asserts (`assert_field_facts(skip_projection=…)`).
  Field-scoped, provenance-visible, revisited when the
  confidence-methodology ADR lands.
- A span that does not reduce to exactly one range **flags
  (`implausible_value`) and asserts nothing** — multi-plant and
  per-body date lists are the reviewer's question, never a hull guess.
  Parser tuned against the real corpus: 280/375 production spans parse;
  74 multi-range fields flag.
- **Redirected articles assert nothing** (first live run's lesson): the
  "Honda Civic Hybrid" sitelink resolves to the whole-nameplate Civic
  page, whose 1972–present span must not land on one generation. A
  resolved title differing from the requested one beyond case/underscore
  wobble is a subject change; the record waits and earlier assertions
  tombstone back to NULL.

### 3. The placement pass: unique dated overlap

Candidates for a configuration = generations linked to its model
(`generation_model_links`) whose placement span contains the
configuration's catalogue period:

- The infobox `model_years` span wins **exact** when the period is
  `model_year`-kind and the field is present. Otherwise the production
  span with **end+1 slack** — a US model year routinely outruns
  production by one calendar year; the start gets no slack (a car cannot
  be catalogued before it is built). Slack applies *before* the
  uniqueness test, so it can only add flags, never force placements.
  Accepted as the general rule; exceptions get dedicated decision
  passes, not a looser rule.
- **Uniqueness counts only when every linked generation is dated**
  (first live run's lesson): one dated match beside undated siblings
  placed a 1991 Celica convertible into `celica-gt-four` because the
  GT-Four page was the only dated candidate. Undated competitors hold
  the configuration in `waits_undated_competitor`, resolving
  mechanically as spans land.
- Exactly one candidate → place, with field-level provenance to the raw
  record whose span decided it. Two or more → NULL + one
  `generation_overlap` flag carrying the candidates. Zero → the normal
  waiting state, logged, unflagged.
- **Body evidence is a candidate VETO, not an overlap tiebreaker**
  (ruled in review, 2026-08-06). The C190 and X290 are vastly different
  cars sharing a badge — the 4-Door is not a coupe — and the overlap
  flag was only ever the fallback under year-only evidence, never the
  goal state. Where a generation's infobox asserts body styles and the
  configuration carries a contradicting body signal (trim strings today;
  EPA's ~100%-filled size class in raw, unconsumed, is the stronger
  candidate), the generation is **not a candidate** regardless of year.
  Where candidates differ in body but the configuration carries no body
  signal, placement waits rather than guesses. Follow-up implementation,
  same rule discipline as the rest of §3.
- The pass is the **sole placer** (ADR 0015's sole-source posture): a
  recomputed answer supersedes the old one, including back to NULL, with
  the trail kept.

Live state at acceptance: 197 configurations placed (the Camry's whole
eight-generation run via model_years exact; Legacy's seven via
production+slack), 354 overlap flags, 616 undated-competitor waits,
22,356 waiting on inventory; 650 wrong-or-premature placements from the
pre-amendment run withdrawn with their supersession trail. Re-runs are
exact no-ops.

### 4. Existence comes from Wikipedia too — section-minted generations

The funnel shows the binding constraint is not time but *existence*:
placement can only reach the 51 models Wikidata gave generations. This
section proposes revising ADR 0012's rule that **only P179 membership
creates a generation**:

- For a model whose QID carries an enwiki sitelink (432 today), the
  nameplate article's **per-generation sections** mint generations: the
  heading supplies ordinal, chassis codes, and start year
  (`First generation (XW10; 1997)`); the section's own infobox supplies
  the spans. Identity is still inherited — the article is reached
  through the model's 1:1-matched QID, and sections are structural
  parsing inside that scope, not name matching.
- Wikidata is **demoted from gatekeeper to contributor**: its
  generation entities keep minting where they exist (their QIDs remain
  the best join keys), its P179/chain structure becomes corroborating
  evidence, and Wikipedia sections mint where Wikidata is silent.
- **Conflated filings and link completeness** (the AMG GT ruling):
  vPIC files ONE `AMG GT` whose catalogue holds two genuinely
  different cars — the C190/C192 sports car and the X290 4-Door — an
  artifact of Mercedes' naming, not of the sources: Wikipedia and
  Wikidata keep them fully separate (own articles, own entities;
  Q50368653 is in the sweep). Section-minting the sports car's article
  yields C190/R190 and C192 *only* — it can never mint the X290 into
  the wrong car. But that leaves a trap this section must close: with
  only C190 linked, a 2019 4-door configuration would *uniquely*
  year-match it, and the undated-competitor guard cannot fire against a
  car that is not linked at all. The answer is twofold: the §3 body
  veto (a 4-door is never a C190 candidate), and representing the
  sibling honestly — the X290 minted as its **own** company-anchored
  generation from its **own** article, linked to the as-filed model
  because the link means "this filing's catalogue contains this
  generation's cars" (true of vPIC/EPA). That link is never fabricated
  by section parsing; it arrives from the sibling's own article plus a
  curated judgment. End state: coupes place into C190, 4-doors into
  X290, and `generation_overlap` fires only for rows carrying no body
  signal.
**Probe verdict (2026-08-07, 108 of the 432 fetched — every 4th by QID
order — plus the AMG GT pair):** 43% of articles carry at least one
generation-shaped section (182 sections total). Ordinals are nearly
universal (99%, one "Sixteenth" beyond the common tables), years in the
heading 97%, own infobox per section 77%, maintained anchors 79%.
Chassis codes in headings pass the strict label extractor only 9% of
the time — but that is the extractor's letters-then-digits bias, not
absence: headings follow a `(CODES; YEAR)` convention where letters-only
codes (GD/GE, NA, SF, VA) are common and positionally unambiguous.
Ordinal collisions appeared in 3/108 articles, all one benign shape —
an `=== Nth generation models ===` subsection under the real section.
`{{Main}}` sub-article pointers exist on only 18% of sections and
resolve to attached generations even less often, so dedup cannot lean
on them alone. 7/108 sitelinks resolve through redirects. The open
questions settle as follows:

- **Keying**: nameplate QID + ordinal, written as
  `section:<QID>#<ordinal>` in `external_ids` under the Wikipedia
  source. The ordinal comes from a **strict heading grammar**: after
  stripping anchors, templates, refs and italics, the heading must be
  `<ordinal> generation` followed by nothing, a dash code, or one
  parenthetical — `Second generation models` fails the grammar and is
  never a generation. Inside the parenthetical, tokens before `;` are
  chassis codes (letters-only accepted here — the position carries the
  confidence the label extractor lacks), years are extracted separately.
  Codes and heading text are **facts, never identity**. Heading years
  are recorded as detail only and never become spans: a start year with
  a fabricated open end would contain every later period — the Civic
  redirect lesson in another shape.
- **All-or-nothing per article**: a generation-shaped section that
  fails the grammar (no ordinal, or a duplicate ordinal) flags
  `section_generation_review` and the article mints **nothing**.
  Minting the parseable sections while skipping one is the
  link-completeness trap generalized: the skipped generation would be
  an unlinked competitor the undated-competitor guard cannot see.
- **Dedup against existing inventory**: for a model that already has
  linked generations, every section must reconcile to one of them —
  by `{{Main}}` target matching an attached QID's sitelink title, or by
  unique intersection of heading codes with a linked generation's
  `chassis_codes`. Reconciled sections corroborate the link (a second
  live sourced row) and assert no facts — the generation's own article
  is the richer source. If any section fails to reconcile, the article
  flags and mints nothing. Models with zero linked generations mint
  freely; that is the 381-model population §4 exists for. When Wikidata
  later grows an entity for a section-born generation, the same
  correspondence (its sitelink title against the section's `{{Main}}`
  target) is the adoption key; the ordinal keying keeps the case
  detectable, and the adoption pass is built when the case first
  arises.
- **Naming/slugs**: with codes, `<model name> (C190/R190)` — the shape
  the Wikidata-born generations already wear; without,
  `<model name> (first generation)`. Slugs are company-scoped as ever;
  a collision flags and never auto-suffixes (the models-pass rule).
- **Fetch shape**: full-article wikitext lands as `article:<QID>`
  records beside the section-0 `infobox:` records — same lander module,
  same polite client, same resumable commit cadence (~430 requests).
- **Facts on section-born generations**: `name`, `chassis_codes` (from
  the heading), `start_year`/`end_year` from the **section's own
  infobox** production span through the same flag-never-guess parser as
  §2. The section's `model_years` and body styles are parsed from the
  landed article at placement decision time, like §3 already does for
  `infobox:` records.
- **Curated article routing**: `SECTION_ARTICLE_MODELS` (policy
  registry, the `WIKIDATA_MODEL_MATCHES` species) maps a nameplate QID
  to a model where the article's generations belong in that filing's
  catalogue but the QID is not 1:1-attached — each entry a recorded
  human judgment. The AMG GT pair is the founding case: Q18011551
  (classified a line by the wd-models pass — its P179 children are
  trims) and Q50368653 (the 4-Door, unmatched) both route to
  `mercedes-benz/amg-gt`. This is the ruled "link curated" mechanism:
  the probe found the 4-Door page is itself a two-section nameplate
  article (X290 2018, C590 2026), so both articles flow through the
  same section machinery and nothing is minted by hand.

**§2 amendment (from the probe, same rule discipline): labeled
subranges defer to a single unlabeled range.** The C190's production
field is one unlabeled range (`October 2014 – September 2022`) beside
variant-labeled lines (`2021–2023 (AMG GT Black Series; …)`). The
unlabeled line is the field's own claim; the labeled lines annotate
sub-series, markets or plants. When exactly one segment carries exactly
one range and no label, that range is the span; per-body and per-plant
lists label every line and keep flagging, so this is not the hull guess
§2 rejected. A previously-flagged field that now parses dismisses its
open `implausible_value` flag with the amendment recorded.

**§3 body-veto implementation (the rule §3 already states, now with
its signals):** the configuration's body signal comes from its attached
EPA raw record — `VClass` "Two Seaters" (a seat-count class, mutually
exclusive with every other car class), and the volume fields `pv4`/
`lv4` (four-door interior volumes, filled exactly when EPA measured a
four-door body) and `pv2`/`lv2` (two-door) — with explicit body words
in the trim string as fallback. The generation's bodies come from its
infobox `body_style` (its section's, else the article's top infobox —
a nameplate-scope claim covers every generation in the article).
Contradiction is **door-count-explicit only**: a two-seater or two-door
signal vetoes a generation whose every body carries `4-door`/`5-door`;
a four-door signal vetoes one whose every body carries `2-door`/
`3-door`. Wagon/SUV/van size classes deliberately assert nothing in
v1 — EPA reclassed the 2025 GT 4-Door as a station wagon, and a class
that can drift must not veto its own car.

## Consequences

- Time and placement machinery is live and self-correcting; its
  coverage ceiling is generation inventory, which §4 raises (the probe
  puts articles with mineable sections at ~43% of the 432, against 51
  models covered through Wikidata today).
- With §4 plus the body veto, the proof car resolves: coupes place into
  C190 and the 4-doors into X290 — no overlap flag, because they were
  never each other's candidates (the two-seater signal vetoes the
  5-door X290; the four-door signal vetoes the all-2-door C190). The
  overlap flag remains only for rows with no body signal — the 2024
  coupes EPA classes "Subcompact Cars" with zero volume fills are the
  live example, honestly flagged between C192 and X290.
- Wikipedia's demotion of Wikidata here is evidence-driven, recorded,
  and reversible per field through provenance — no raw data is
  discarded either way (ADR 0004).
