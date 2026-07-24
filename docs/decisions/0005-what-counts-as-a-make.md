# ADR 0005 — What counts as a make, and how derived vehicles relate to their base

- Status: Proposed
- Date: 2026-07-24
- Resolves: the "coachbuilders" open question in `PROGRESS.md`; informs the
  "defunct/acquired makes" question

## Context

The Wikidata ingest landed 7,222 candidate entities. They are not 7,222 makes.
The set mixes real marques with assembly plants, holding companies, and
mobility-services subsidiaries — "KINTO Europe", a Toyota car-sharing operation,
sits beside BMW. The reconciler cannot promote rows into `makes` until "make" is
defined, so this blocks the next piece of work.

Two families of edge case force the definition to be precise.

**Manufacturers of modified vehicles.** Alpina builds on BMW hardware but is a
registered manufacturer whose cars are titled as Alpina. Ruf is the same shape
against Porsche. Singer rebuilds customer-owned 911s that keep their original
Porsche VIN — legally a modified used car, commercially a distinct product
people search for by name.

**Coachbuilders.** Zagato, Pininfarina, and Bertone bodied vehicles for many
different manufacturers — Aston Martin, Ferrari, Lancia, Alfa Romeo. A
"DB4 GT Zagato" is titled an Aston Martin; Zagato is the coachbuilder, not the
make. But Pininfarina now sells the Battista under its own name as Automobili
Pininfarina, where it *is* the make.

This rules out the obvious shortcut. **Derivation cannot be a relationship
between makes**: "Zagato derives from Aston Martin" is false, because Zagato
also worked with Ferrari and Lancia. The relationship is between *vehicles*, and
one coachbuilder must be attachable to every company it built for.

## Decision

### 1. A make is the company under whose name the finished vehicle is sold

The operational test: **does the entity take manufacturer responsibility for the
finished vehicle** — its own VIN/World Manufacturer Identifier and type
approval, rather than modifying a car that remains another maker's?

This test is chosen because it is *checkable against data already planned for
ingestion*: NHTSA vPIC (Tier 1) publishes WMI-to-manufacturer mappings, so make
status becomes verifiable rather than a per-marque judgment call.

Applying it: Alpina and Ruf pass — own WMI, titled under their own name.
Assembly plants, holding companies, and service subsidiaries like KINTO fail —
no finished vehicle is sold under that name.

**Singer is admitted as a deliberate exception.** It fails the legal test (the
cars keep Porsche VINs) but passes the commercial one: a complete catalogued
product sold and searched for under its own name. The rule is therefore *legal
manufacturer status **or** a distinct catalogued product line sold under the
entity's own name*, and where those disagree the derivation link (below) records
the truth the legal test would have carried. Marking the exception is better
than a rule that quietly excludes a marque users expect.

### 2. Coachbuilders and modifiers are `makes` rows

Not a parallel entity type. They are companies, and several of them sell under
their own name either now or historically (Automobili Pininfarina, Zagato
Mostro). A separate `coachbuilders` table would need duplicating the moment one
became a manufacturer. What varies is not the entity — it is the *relationship*.

### 3. Derivation is a fact table between generations

    vehicle_derivations
      derived_generation_id   -> generations   the Alpina B3 / Singer 911 / DB4 GT Zagato
      base_generation_id      -> generations   the BMW 3 Series G20 / Porsche 964 / DB4 GT
      derivation_type_id      -> derivation_types
      coachbuilder_make_id    -> makes         nullable; who did the work
      + source_id, scraped_at, confidence, raw_record_id   (it is a claim)

**Generation, not make** — because Zagato proves make-level wrong.

**Generation, not configuration** — generations already hold chassis codes,
which *are* platform identity, and the base is reliably known at that level. For
coachbuilt one-offs the exact donor trim frequently is not.

`derivation_types` is a lookup, consistent with the other dimension tables, so a
new kind of relationship is an INSERT rather than a migration. Initial values:

| type | example |
| --- | --- |
| `coachbuilt` | Aston Martin DB4 GT Zagato ← DB4 GT |
| `restomod` | Singer 911 ← Porsche 911 (964) |
| `tuned` | Alpina B3 (G20) ← BMW 3 Series (G20) |
| `rebadged` | Toyota GR86 ← Subaru BRZ |
| `platform_shared` | shared architecture, neither derived from the other |

`rebadged` is not an afterthought — badge engineering is a large, real category
(GR86/BRZ, Chevrolet Prizm/Toyota Corolla, the entire Stellantis back catalogue)
that otherwise had no home in the schema.

`coachbuilder_make_id` is what satisfies the requirement directly: every company
Zagato built for is one query.

```sql
SELECT DISTINCT base_make.name
FROM vehicle_derivations d
JOIN generations g   ON g.id = d.base_generation_id
JOIN models m        ON m.id = g.model_id
JOIN makes base_make ON base_make.id = m.make_id
WHERE d.coachbuilder_make_id = :zagato;
```

The table is fact-bearing, so it carries the full provenance quartet per
`CLAUDE.md` — "Zagato bodied this car" is a sourced claim like any other.

## Consequences

- The reconciler gains a defined admission rule for `makes`, unblocking
  promotion of the 7,222 landed entities. Expect the surviving count to be far
  lower; the residue is not discarded, it simply is not promoted.
- **Coachbuilder attribution and derived-make lineage share one mechanism.**
  Zagato attaches to every marque it built for via `coachbuilder_make_id`, while
  Singer/Alpina lineage uses the same row without one.
- The frontend gets what it needs both directions: a Singer page can show its
  Porsche base, a Zagato page can list every marque it bodied, and a Porsche 964
  page can surface what was built on it.
- Two new tables (`vehicle_derivations`, `derivation_types`) and a migration.
  Not yet implemented — this ADR precedes it.
- **Deferred:** configuration-level derivation for one-offs where a distinct
  generation does not exist, and whether `platform_shared` (a symmetric
  relation) is well modelled by a directional table. Both are additive later.
- The Singer exception means make status is not purely mechanical. Cases that
  fail the legal test but pass the commercial one are flagged for review rather
  than auto-promoted.
