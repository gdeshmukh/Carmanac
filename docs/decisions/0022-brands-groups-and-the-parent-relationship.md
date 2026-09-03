# ADR 0022 — Brands, groups, and the parent relationship

- Status: Accepted
- Date: 2026-09-02
- Depends on: ADR 0006 (one `companies` table, make is a role), ADR 0007 §5
  (curated identity merges) and §8 (per-source assertion stores), ADR 0011 §2
  (where a line files), ADR 0019 (addresses are projections)
- Resolves: the "defunct/acquired makes" open question (corporate parents)

## Terms

- A **brand** is a company whose name is on cars. It holds models. Every
  catalogue page at the top of the hierarchy is a brand.
- A **group** is a company that owns brands and sells nothing under its own
  name. It holds no models. A company can be both: BMW sells BMWs and owns
  Mini; Ford Motor Company sells Fords and owns Lincoln.
- A **parent era** is one span of time during which one company was the
  parent of another. Volvo Cars has two: Ford, then Geely.
- A **brand artifact** is a Wikidata entity classed only "car brand" or
  "trademark", minted beside the long-standing company entity for the same
  marque. One editing wave produced most of them.

## Context

The database holds 7,201 companies and knows nothing about how they relate.
The companies fetch captures label, description, classes, dates, country and
website; no corporate structure. The gap has two costs.

**The model sweep cannot find its brands.** Wikidata names the corporate parent
as a car's maker — General Motors for the Corvette, Volkswagen Group for the
Golf — while our models are filed under the badge the car wears, because that
is how vPIC and the EPA file them. 1,286 of 2,330 models carry no Wikidata id.
Most of them are this shape: the maker we resolve holds zero models, so the
name rungs look in an empty index. Without an id no article is fetched and no
generation is minted. The vote that recovers them (ADR 0011 §2's line rule,
extended one rung up) needs the company graph as recorded evidence.

**The company pages have nothing to say.** "Part of Stellantis since 2021,
General Motors before that" is what a reader expects on the Opel page, and
what a reviewer needs on the back end to tell a rebadge from a namesake.

A live probe over every company id we hold (2026-09-02):

| Wikidata edge | companies carrying it | of the 137 brands |
|---|---|---|
| parent organization (stated on the child) | 374 | 48 |
| subsidiary (stated on the parent; the inverse) | adds 152 child→parent edges | 56 |
| owned by | 441 | 107 |

51 parent claims carry start and end dates and 48 companies list more than one
parent. The chains are real and they are the interesting part: Opel (General
Motors 1931–2017, Groupe PSA 2017–2021, Stellantis 2021–), Land Rover (British
Leyland, Rover Group, BMW, Ford, Tata), Aston Martin's Ford years, Maserati,
Mini. Some eras are missing — Wikidata has no Ford era for Volvo Cars at all —
so whatever holds this data must be able to say "no source has said" rather
than fill the gap.

"Owned by" is a different kind of statement. It names shareholders (BMW is
owned by Klatten, Quandt and BlackRock; Toyota by Nippon Life; Tesla by Musk
and Fidelity) and states, and a company is "owned by" three of them at once.
But it is also the only edge Wikidata offers between a brand artifact and its
company (the Hyundai brand is "owned by" Hyundai Motor Company) and between a
division and its group (GMC is "owned by" General Motors).

**The brand artifacts hold the catalogue.** 27 model-holding rows are brand
artifacts; the substantive company stands beside each one as a separate,
model-less row. Ford (143 models) is the artifact and Ford Motor Company holds
nothing. Two different things hide in that set. Ford and Ford Motor Company are
one enterprise recorded twice — the shape ADR 0007 §5 already merges for Audi,
BMW and Alfa Romeo. Plymouth and Stellantis North America are two companies,
one owning the other. The merge registry's precedent is right for the first and
wrong for the second.

**Names follow the artifact question.** The merges so far made the substantive
entity canonical, so the page says "Audi AG" where the car says Audi. Merging
Ford into Ford Motor Company would do the same to Ford. A catalogue company is
named by its badge.

## Decision

### 1. Parent eras are a dated fact table

`company_relationships` records one source's assertion that one company was
the parent of another for a span of years:

    company_id, parent_company_id, kind, start_year, end_year,
    source_id, raw_record_id, scraped_at, confidence_score,
    superseded_by, created_at

`kind` is `parent_organization` and nothing else yet; the column exists so a
second relationship kind (a joint venture, a licensee) is a value, not a
migration. Start and end years are nullable: an open-ended row is a current
parent, a row with neither is an undated claim.

It is a per-source assertion store in the `company_role_assignments` shape. A
second source agreeing is a second live row; a source changing its claim
supersedes its own row; nothing is deleted. Uniqueness is per (child, parent,
kind, start, end, source) among live rows.

**"Current parent" is a projection**, not a column: the live rows with no end
year. Nothing joins on it and no registry keys on it.

Rejected: a `parent_company_id` column on `companies`, the shape the open
question leaned toward. One column holds one answer. It cannot record Volvo's
Ford years beside Geely, or Opel's three owners, and for every defunct marque
it would state a current parent that is not current. The eras are the content;
a table that keeps them is the only shape that can also stay silent where a
source is silent.

### 2. Structural edges assert; shareholding is context

"Parent organization" and "subsidiary" assert parent eras, with their start
and end qualifiers as the era's years. They are the same relationship stated
from either end, so a subsidiary claim on the parent lands as a row on the
child exactly as a parent claim would.

"Owned by" is landed in the same raw record and asserts nothing. It is evidence
a human may read when ruling on a proposal — the brand/company list in §4 was
built from it — and never structure. Should a shareholding edge ever matter as
data, that is a new kind under §1 with its own decision.

### 3. Both ends are companies we hold

A relationship resolves each end through `external_ids` and the identity
merge registry, as every source does. A parent we do not hold is a claim that
waits in raw with its decision recorded; a relationship never mints a company.
An edge whose two ends resolve to the same row — the Ford brand's "owned by"
Ford Motor Company after the two merge — is a self-edge and is skipped.

### 4. A brand artifact and the company it names are one company

The test is mechanical and runs on names, whitespace-bound: the artifact's
name leads the owner's name as whole leading tokens. Ford leads Ford Motor
Company; Hyundai leads Hyundai Motor Company; Aston Martin leads Aston Martin
Lagonda. Such a pair is one enterprise recorded twice and merges through the
identity registry, canonical on the substantive company entity, which holds
the facts. The proposer generates the list from this test and a human rules
each row; the registry records the ruling, never the list.

Where the artifact's name does not lead the owner's name — Plymouth and
Stellantis North America, Mini and BMW, Geo and General Motors — they are two
companies and the owner is a parent under §1. No merge.

A pair may fall on the merge side with the catalogue on the artifact: the
artifact row holds the models, the make match and the slug, while the company
row is model-less. The merge script keeps the artifact row as the pair's
company (its identity, slug and models are untouched), attaches the company's
id to it, and lets the company record assert the facts on the next pass. This
is the Tesla path of ADR 0007 §5 with a materialized canonical.

Lines and generations filed under the legal entity are the enterprise's and
move with its identity — Ford Motor Company's five line rows are Ford's. A
name or slug already taken on the surviving side is a collision the script
refuses on rather than resolving, because two rows at one natural key is the
duplicate identity the merge exists to remove.

### 5. A brand is named by its badge

Where a company has a brand-classed merge member, its `name` fact is taken
from that member's label — Audi, not Audi AG; Ford, not Ford Motor Company —
with provenance pointing at the member's own record. Every other fact stays
the canonical's. A company without a brand member keeps its label. The
residue (legal tails with no artifact beside them: Genesis Motor, Ram Trucks)
is a per-entity name ruling and is not opened here.

### 6. Brands are the catalogue; groups are behind it

A catalogue company is a brand, and its cars are the cars sold under its badge
— the filing decides, so the Corvette is Chevrolet's however prominently the
model badge is worn. A group holds no cars and has no catalogue page of its
own; it appears on its brands' pages through §1. Volkswagen keeps its cars and
its name; Volkswagen Group holds none. Nothing marks a group: being one is
having parent rows and no models, and a company can be both.

### 7. The model sweep reads the graph as evidence, never as a gate

When a model-shaped entity's maker holds no models, the entity files by the
same vote a line does (ADR 0011 §2): under the unique model-holding company
whose name leads the entity's own name, and then matches by name there. Whether
§1 records that company as a child of the stated maker is written into the
decision as corroboration. It never blocks: the maker of the Chrysler
Crossfire is Karmann, the contract builder, no parent edge exists, and the
Crossfire is a Chrysler. The mechanics are ADR 0011 §2's amendment.

## Consequences

- The companies fetch gains a second sweep keyed by the company ids we hold,
  landed under its own marker, so the makes sweep's content hashes are
  untouched. A small pass projects it into §1's table.
- The merge script gains §4's third path. The brand/company proposer is a
  decisions script with a dry run; its rulings land in the identity registry.
- The front door already lists companies that hold cars; §6 changes nothing
  there. Company pages gain "part of" when the read surface is next touched.
- Owed elsewhere: the residue name rulings (§5); whether a group gets a thin
  page; Volvo's Ford era and the other missing eras, which need a second
  source and land as ordinary rows when one does.
