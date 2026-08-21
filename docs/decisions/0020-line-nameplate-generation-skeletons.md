# ADR 0020 — Generation skeletons read from Wikipedia articles

- Status: Proposed; amended 2026-08-14 (configuration–engine links from the
  article tables; the first engine and transmission entities); amended
  2026-08-20 (spec defaults at model/generation grain; power and torque
  unparked; the tables and family passes land inside the unified
  Wikipedia pass)
- Date: 2026-08-13
- Depends on: ADR 0011 (models are as-filed; lines are not models), ADR 0016
  (generations belong to a company, not a model), ADR 0017/0018 (Wikipedia
  section minting and the Main-target fetch), ADR 0007 §5 (approvals live in
  registries)

## The problem

A generation can only be created today when a structured source says "this
is a generation of X" and X is one of our models. That rule quietly fails
for most of the catalogue's famous nameplates.

Our models are the manufacturers' filings: whatever vPIC files is the model
(ADR 0011). Some nameplates file under their own name — Ford files
"Mustang", BMW files "X3" — and for those the rule works. But many
nameplates file as badges: BMW files 330i and 328i, never "3 Series". For
those, the nameplate exists in our database only as a **line** — a named
group over models, deliberately not a model itself. Wikidata's generation
entries for such nameplates point at the line when they point anywhere, so
the rule never fires and the generations are never created. No flag is
raised; the entries land in silent "wait" pools that no page or queue shows.

Measured 2026-08-13, across the whole database — this is general, not a
BMW quirk:

| where they wait | count | includes |
|---|---|---|
| generation entries whose nameplate is a line | 453, across 190 nameplates | Corvette (all 10), Golf (10), Passat (10), Jetta, Polo, S-Class, C-Class, 7 Series, Fiesta, Taurus, Mustang, Astra, Mégane |
| unmatched pool, titled "nameplate" or "nameplate (code)" | 43 | six of the seven 3 Series generations |
| several entries titled identically, collided into flags | 38 | the four "BMW X5" entries |

Net effect on coverage: 531 generations exist, only 147 of the 1,114
models that have cars carry a linked generation, and 2,652 of 23,523 cars
are placed into one.

Worked example — the 3 Series, seven generations (E21 through G20), all
fetched, none landed:

- One (the E30) states "part of the 3 Series" and waits, because the
  3 Series is a line.
- Six state only "comes after / comes before" its neighbours. The matcher
  records those chains but never follows them. Three of the six are titled
  just "BMW 3 Series", identical to the nameplate itself, so titles decide
  nothing.
- None of the seven carries a production year in Wikidata, and the
  standing rule (ADR 0014) is that a line's generations are not created
  until they can be dated.

Wikidata is structurally thin here — membership statements are rare, dates
rarer — and no amount of extra matching rungs fixes data that is absent.
Wikipedia has all of it: these nameplates have one article per generation,
whose infobox carries production years and body styles, and whose text
lists the models the generation covered. We hold proof in raw already: the
E30's article (fetched during the ADR 0018 work) contains
`production = 1982–1994`, the North American model years, and the model
breakdown. But the article fetch is keyed on models, so for line-filed
nameplates these articles are never downloaded. The best-documented cars
in the catalogue are the ones the pipeline is blind to.

We also already scrape, and then ignore, two corroborating signals:
article redirects (the i5's article resolves to "BMW 5 Series (G60)" — a
scraped statement that the i5 belongs to that generation's page), and
EPA's `baseModel` column, which files every 3 Series badge under
"3 Series".

## The decision

1. **Fetch articles for lines, not just models.** A matched line gets its
   nameplate article, that article's per-generation sections, and their
   per-generation articles, through the existing fetch machinery. Wider
   article coverage is also simply more raw data per car.

2. **An LLM turns each nameplate's articles into a proposed skeleton.** A
   skeleton is: the generation list (name, chassis codes, production
   years) plus which models each generation covered. Every field must
   quote the article text it came from. The LLM organizes what the pages
   state; it may not add anything the pages do not state.

3. **A human approves each skeleton; approvals are registry entries; a
   deterministic pass applies them.** Same shape as every recorded
   judgment in this project: the proposal is reviewable text, the approval
   is committed to a registry, and rebuilding from raw replays it
   mechanically. No pass calls an LLM at run time — this is the first
   applied instance of the 2026-08-07 ruling (LLM proposes, human
   confirms, registries record).

4. **Approved generations are created under the company**, dated and coded
   from the articles, with the article records as provenance. No model
   parent is needed (ADR 0016). The articles' model breakdowns fill in
   line membership and generation–model links; EPA `baseModel` and the
   scraped redirects corroborate both.

5. **Wikidata stops gating existence.** Where one of its entries clearly
   corresponds to one created generation, it attaches as that generation's
   external id and its ordering chains corroborate the skeleton. That is
   its whole remaining role here.

## What does not change

Lines are still not models. Links are still evidence, never inference.
The placement pass keeps its rules — it just finally has dated, linked
generations to place cars into. Deterministic passes stay LLM-free.

## Consequences

- The 190 affected nameplates become workable one at a time, in priority
  order — each is one skeleton review, not a queue grind.
- A new step exists between fetch and apply (the proposal). Its failure
  mode, a wrong extraction, is bounded by required quotations and by
  per-nameplate human review.
- The wait pools hid a whole-nameplate hole until a page happened to make
  it visible. The planned reviewer surface should show the wait pools
  beside the flag queues, and every surface should print names, never
  source-internal ids.

---

## Amendment (2026-08-14): configurations get their engines, from the article tables

- Extends: the decision above; ADR 0007 §8/F7 (association facts are
  per-source assertion rows — `configuration_engines` has waited empty for
  this); ADR 0015 (which promised an engines decision to whatever source
  first names engines: this is it); ADR 0017 §1 (identity is inherited,
  never name-matched)
- Considered and rejected on the way here: generation-grain spec facts (a
  `generation_attributes` table was drafted and struck the same day). A
  generation's page exists to sort a model's cars into configurations, not
  to carry specifications. Facts about what a car came with belong on the
  configuration.

### The problem

A configuration should know what engine it came with. Wikidata was the
intended source for that connection and cannot carry it: its statements
are thin here, and checking one means trusting an opaque claim. Wikipedia
states the same connection in a form a person can open and verify — most
model articles carry a table associating each variant with its engine,
years, and displacement. The E46 3 Series page is the famous example, but
the pattern is the ordinary one on model pages:

    Model | Engine code      | Year(s)   | Power | Displacement
    T5    | [[B5254T3]]      | 2005–2007 | ...   | 2521 cc

We hold 434 full model articles in raw and read none of their tables.
Censused 2026-08-14, with a deliberately rough throwaway parser:

- 97 articles carry model+engine tables — 223 tables, roughly 1,700
  engine rows. 95 of the 97 are already attached to our models through
  Wikidata ids, so the anchor is inherited identity, not name matching.
  88 of those models have configurations: about 2,800 configurations
  sit under table-bearing pages.
- Matching rows to configurations on physical keys alone (catalogue years
  overlap, displacement agrees, petrol/diesel not contradicted): **562
  configurations connect to exactly one engine** — 179 match a single
  row, 383 match several rows that all name the same engine (one engine,
  several tunes or years). 293 land on rows naming different engines and
  stay open. 676 configurations carry no displacement on our side and
  cannot be physically matched (mostly EVs and thin filings). The rest
  find no candidate row at all — largely honest misses, since these are
  mostly European-market tables and many US filings' engines simply are
  not in them.
- Trim names are useless as keys and are not used: our filings say "AWD"
  or "Premium" where the table says "T5" or "2.0T". In the whole census,
  trim agreement never resolved an ambiguity.
- The engine cells are wikilinks: 115 distinct targets, of which 44 are
  maker-titled family articles ("Volvo Modular engine", "Mazda F engine",
  "BMW B47") — the rest generic technology or list pages. 210 distinct
  variant codes ride in the link anchors ("Volvo Modular engine#B5254T7").

Terms. A **family article** is Wikipedia's page for one maker's engine or
transmission family; the maker's name leads the title. A **variant code**
is the per-engine designation inside a family ("B5254T7"), usually a
section anchor on the family article. To **mint** is to create an
`engines` or `transmissions` row. To **anchor** is to decide which
configuration a table row is talking about.

### Decision 1 — configuration–engine links from the tables, on physical keys only

A new deterministic pass reads the model+engine tables of attached
articles. A table row is a claim: "the variant with these years, this
displacement, this fuel came with this engine." A configuration takes an
engine link when the physical keys — catalogue-period years overlap,
displacement within tolerance, petrol/diesel not contradicted — leave
exactly one engine identity standing across every candidate row. Several
rows naming one engine are one answer. Beyond that:

- Rows naming different engines: the configuration asserts nothing and
  the pass logs it. This is a real review queue — a person reading the
  page can often settle it — but it is a decision-log queue, not a flag,
  until we see its size in practice. Open cases are not parked: every
  re-run re-attempts them, and a grown registry or a variant code
  settles many mechanically.
- No candidate rows: nothing is asserted and nothing is logged as wrong.
  A US-market filing whose engine is absent from a European table is the
  table being what it is, not an error.
- Trim and badge names are never keys, per the census.

Links land as per-source assertion rows in the existing
`configuration_engines` / `configuration_transmissions`, with the article
record as `raw_record` — every link points at a page and table a reviewer
can open, which is the whole reason Wikipedia beats Wikidata here.
Supersession handles corrections; re-runs converge to exact no-op.

### Decision 2 — engine and transmission entities: a strict minting ladder

Entities are minted only from unambiguous identity signals, on a ladder
checked per link target. The discriminating signal is the target title:
Wikipedia titles family articles with the maker's name first, and the
model is already attached to its company, so the mechanical test is
whether the title begins with that company's name — an exact casefolded
prefix against a row we already hold, no new name-matching surface.

1. **Registry rung, checked first (ships empty).** A committed registry
   of per-title human judgments: mint as engine of company X, mint as
   transmission of company X, or never mint. Cross-maker engines,
   supplier-branded gearboxes ("ZF 8HP transmission", "Lineartronic"),
   and corrections to rung 2 all land here. Recorded judgments, applied
   deterministically, replayed on rebuild; the pass's log of failing
   titles is this registry's review queue. Entries are cheap to produce:
   an LLM classifies the distinct target titles in batch — engine family
   of X, transmission family of X, generic technology, list page — and
   the batch is reviewed and committed like any dry-run (LLM proposes,
   human confirms, registries record: the 2026-08-07 ruling). The LLM
   classifies text that exists; it never generates a fact, and no pass
   calls one at run time.
2. **Prefix rung (mechanical).** A target whose title starts with the
   attached company's name mints, or attaches to, a family entity:
   - Identity is the normalized title, keyed in `external_ids` under
     Wikipedia (English) as `engine-article:<key>` /
     `transmission-article:<key>`. The key is the title casefolded,
     underscores to spaces, with one trailing " engine" /
     " transmission" stripped — "Mercedes-Benz OM642" and "Mercedes-Benz
     OM642 engine" both occur and are one family, one row. Dedup is
     global: every page linking "Volvo Modular engine" resolves to the
     same row.
   - Fields: name (the title as first observed), slug (from the key),
     `manufacturer_company_id` (the attached company). ADR 0015 rejected
     inferring an engine's maker from the car's company when the source
     named none; here the title names the maker, and the rung fires only
     when that name is the attached company's — read off the article,
     not assumed from ownership.
3. **Everything else mints nothing.** Generic technology links
   ("Straight-four engine", "Turbocharger"), list pages, bare text: all
   preserved in raw, waiting.

The minted grain starts at the **family** — that is what a model-page
cell links — and deepens in Decision 3. The generation infoboxes'
engine fields (78 family targets in the earlier census) stay unread:
they assert generation-grain claims this schema deliberately gives no
home. No LLM anywhere on the ladder.

### Decision 3 — dive into the families: fetch their articles, mint the variants

The minted families are themselves a fetch list: each entity's external
id names its article. Those pages carry the deeper facts at exactly the
grain a configuration wants — one section per variant code on most
pages, a spec table on some — so the arc does not stop at the disk:

- Each family article named by a minted or registry-confirmed entity is
  fetched into raw under the Wikipedia source, through the standing
  landing discipline (raw first, passes read raw, refetch supersedes).
- A family pass reads the per-code sections and any spec tables.
  Variants mint as `engines` rows keyed
  `engine-article:<family key>#<code>`, carrying `family_code` always,
  and displacement and cylinders where the section or row states them —
  never decoded out of the code itself. A code anchors to a heading
  after undoing MediaWiki's space-to-underscore encoding; explicit
  `{{anchor}}` ids inside headings count too. A section that states no
  displacement mints its variant lean rather than not at all. The
  family row stays as the article's identity anchor.
- The tables pass re-runs: where a model-page row carried a variant
  code, the configuration's link repoints to that variant, superseding
  the family-grain link. Rows without codes keep the family link —
  still true, just coarser. This is also how a configuration's "deeper
  facts" arrive: through its engine link, never as copied columns.
- Wikidata's role here is identity corroboration only — family articles
  have QIDs via sitelinks, attached for free when useful. It is not
  less processing (the fetch machinery is the same either way), and it
  is thinner exactly where the variant tables are rich; the facts stay
  on pages a reviewer can open.

Power and torque stay unextracted everywhere — model-page tables and
variant tables alike — until the rating-standards decision (the owed
sibling to ADR 0009).

### What does not change

Generations carry no spec facts; their pages sort cars into
configurations, and these links are what make that sorting mean
something. The main decision above (skeletons, placement) proceeds
independently — and when its rollout fetches the per-generation pages of
line-filed nameplates, their tables feed this same pass unchanged.
EPA still mints nothing. Sources beyond Wikipedia — other websites, and
any orchestration over them — wait for their own decision. Deterministic
passes stay LLM-free: classification happens offline into committed
registries, never at run time.

### Consequences

- Zero schema changes: `engines`, `transmissions`,
  `configuration_engines`, `configuration_transmissions`, and both
  external-id arcs already exist. The arc is one pass and one registry.
- The first rows ever in `engines` and `transmissions` — on today's
  pile, up to 44 engine families and a handful of transmission families,
  each arriving with at least one configuration trying to link to it.
  The `/engines/<engine>` route gets its first subjects.
- Around 560 configurations gain engine links from census-grade parsing;
  a production parser (rowspans, more units) and a populated registry
  raise that; market mismatch bounds it.
- The review surfaces are lists the pass already logs: prefix-failing
  titles for the registry (115 engine and ~50 transmission distinct
  titles today — one batch classification and one review, not a grind),
  ambiguous configurations for a human read of the page.
- The family fetch list starts at roughly the minted 44 and grows with
  the registry. A live probe over the pages the engine cells name found
  every title but one real; four in five code anchors land on a
  per-variant section, and three in five of those sections state
  displacement. The 210 variant codes already seen on model pages are
  the first candidates to repoint links onto.

---

## Amendment (2026-08-20): spec defaults, power unparked, the passes land

Three rulings, all applied on the `feat/infobox-widening` branch.

### Spec defaults at model and generation grain

The 2026-08-14 note above rejected generation-grain spec facts, and that
rejection stands for what it rejected: no `generation_attributes` EAV, no
per-generation claims that promote generations into the hierarchy. What it
did not cover is the statistic a source states honestly at a coarser grain
than a configuration — a nameplate page's one wheelbase, an era page's one
curb weight — which until now had nowhere to land and left model pages
empty.

Two 1:1 sibling tables now hold them: `model_specs` and `generation_specs`
(length, width, height, wheelbase, curb weight, doors, seating, power,
torque — the same columns and units configurations carry). They exist
purely as **defaults**: `v_configuration_full` resolves each spec column
configuration-first, then generation, then model, so finer data always
wins and a configuration with no generation inherits straight from its
model. Generations stay outside the four-level hierarchy and carry no new
load. The landing grain is the grain of the page that states the value —
a single-era nameplate article's lead lands at model grain, a
generation-attached article or per-generation section at generation grain;
a multi-era nameplate's lead asserts nothing (its lead shows the current
generation's dims, which must not smear across eras). Only single
unambiguous values land: one `{{convert}}`, a known unit, no lists, no
ranges, no variant labels. Provenance rides the existing model/generation
arcs in `field_provenance`, per field, superseding like everything else.

### Power and torque, unparked

"Power and torque stay unextracted" above is withdrawn (ruled 2026-08-20):
figures land **standardless**, with the per-field provenance keeping the
source's own observed string ("250 PS (184 kW; 247 hp)") so nothing is
lost and the read surface can state the source. The rating-standards
refinement (SAE gross/net, DIN, JIS) stays an open question — it no longer
blocks extraction, and cross-era comparisons mix standards until it is
settled.

### The tables and family passes, landed

Decisions 1–3 run inside the unified Wikipedia pass (ADR 0017 amendment,
same date), not as separate modules. Two mechanics sharpened in
implementation:

- **Claims accumulate before judgment.** A configuration is anchored by
  every article that reaches it — its model's page and its generations'
  pages overlap — so table rows collect per configuration across the whole
  run and one sync judges the union. Per-article judgment would let the
  last article fight the first, and never converge.
- **Variants mint on demand.** A family page's per-code sections mint only
  the codes the model-page tables actually cite (anchored to a heading or
  `{{anchor}}` id, underscores undone, displacement riding along where
  stated). Coded links then repoint from family grain to the variant; the
  full-section sweep can widen later if a consumer appears.

First live run: 10 families + 1 transmission + 91 variants minted, 290
engine and 61 transmission links, 177 configurations with power — the
first power figures in the database. Most table-bearing articles belong to
European models whose configurations do not exist yet; the reviewed
registries fire as those arrive.
