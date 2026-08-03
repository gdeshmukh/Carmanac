# Reconciler incidents

Live failures that each put a guard in the code. Kept here rather than inline
so the source can say *what* the guard does in a line, and this can say *what
happened* at length.

Each entry: what broke, what it cost, and what now prevents it.

---

## Admission admitted 2,175 non-car companies

**v1 of the admission rule** treated a class set of pure corporate boilerplate
("business", "enterprise", "company") as admissible — the reasoning being that
the fetch axes had already filtered for automotive relevance.

The first live pass produced 2,175 roleless companies: seatbelt suppliers,
parts makers, dealerships, glass-repair chains. Exactly the "supporting cast at
the expense of the cars" the charter forbids.

**Guard:** admission now requires *affirmative* car evidence — a target class,
a builder class, or a hand-vetted pin. Boilerplate-only sets quarantine. The
polarity is deliberate: under-admission is cheap (edit a list, re-run, the
entity appears), over-admission means unwinding entity rows other data may
already reference. `policy.classify`.

## Mercedes-AMG founded 1812

A single vandalized P571 claim gave AMG a founding year 74 years before the
automobile. Nothing caught it: `multi_value` flags only fire when sources
*disagree*, and a lone wrong claim has no disagreement.

**Guard:** bounds and cross-field plausibility checks at projection time, which
open `implausible_value` flags. The value still projects — pages show data
tentatively rather than hiding it — but the suspicion is recorded.
`policy.plausibility_issues`.

## "Ranger" read out of "Range Rover"

The cross-badge guard asks which held brand a label wears as a prefix. Because
`normalize_name` strips spacing, a raw `startswith` matched the held brand
**Ranger** inside "Range Rover (1st generation)" — a brand that label does not
wear. The false alarm then survived its own merge by hopping brands.

**Guard:** brand prefixes must end on a *word boundary* of the label.
`_WikidataModelsPass._label_brand`.

The same word-boundary lesson applies in the EPA model ladder, where the
longest as-filed name prefixing a model string must also break on a token
boundary — otherwise "Ranger" claims "Range Rover" rows.

## A stale flag kept answering the old question

Open flags were refreshed only when their *reason* string changed. When a
cross-badge verdict flipped True → False with the reason unchanged, the flag
kept its stale detail and told a reviewer the wrong thing.

**Guard:** an open flag is the *current* question, so its whole detail
refreshes whenever the current computation differs. Only closes are immutable
history. `_WikidataModelsPass._flag`.

## Model records read as makes

The vPIC match pass originally identified make records by payload shape. But a
model payload carries `make_id` and `make_name` too — deliberately, since that
link is how a nameplate finds its make — so the shape test read all 2,018
landed model records as makes and attached `model:<id>` external ids to
*companies*.

Caught by ADR 0010's tests before the next match run, so no live rows were
affected.

**Guard:** record kind is identified by the external id's kind prefix
(`make:` / `model:`), which is a durable marker. Payload shape is not, because
record kinds legitimately share fields. `_MatchPass._is_make_record`.

## Nameplate labels claimed by four entities at once

Rung 3 assumed a unique exact-name hit was a match. Live data had 51 models
claimed by several entities simultaneously — generation entities carrying the
bare nameplate label, so four "BMW X5"s and four "Honda Accord"s each claimed
the same model.

**Guard:** a unique hit is only a *claim*. Correspondence is decided per model
in `_resolve_claims`, after every claim is known, with label evidence
outranking alias evidence. `_WikidataModelsPass`.

## Aliases carried rebadges

Wikidata files market names and rebadges as aliases: the Raize entity carries
"Daihatsu Rocky", "Perodua Ativa" and "Subaru Rex". Treating aliases as equal
to labels put a Subaru Trailseeker on `toyota/bz-woodland`.

A blanket alias ban was the obvious fix and was wrong — the pre-implementation
audit of 387 attached matches found 37 "alias-only" attachments were 17
stripping artifacts, ~15 *correct* US-market-name matches (Echo, LeCar,
Sentra — where the alias IS the as-filed name), and exactly one real
miscategorization. Banning aliases would have traded ~15 true matches for 1
true negative.

**Guard:** name forms rank (ADR 0013). A label says what an entity *is*;
aliases say what it is *also called*. Uncontested same-brand alias claims
attach; contested or cross-badge ones flag as `market_name_or_rebadge`.

## The supersession order

The naive sequence — insert the new assertion, then point the old one at it —
is impossible. `uq_field_provenance_live` rejects the second live row before
the old one can be repointed, and the old one cannot reference an id that does
not exist yet.

**Guard:** the three-step dance in `engine._supersede`. See
[schema-traps.md](schema-traps.md) for the full explanation.
