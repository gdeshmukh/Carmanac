# ADR 0020 — Generation skeletons read from Wikipedia articles

- Status: Proposed
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
