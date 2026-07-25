# ADR 0006 — One `companies` table; "make" becomes a role

- Status: Accepted
- Date: 2026-07-24
- Revises: ADR 0005 §2 (which proposed a separate `builders` table)
- Amends: the five-level hierarchy invariant in `CLAUDE.md`

## Context

ADR 0005 defined a make by manufacturer status (own WMI) and put everything else
— restomodders, coachbuilders, tuners — in a separate `builders` table. The
pre-reconciler review found that split does not survive contact with the
requirements.

**A builder page needs everything a make page needs.** Zagato needs a slug, a
name, a country, founded and defunct years, prose, and fuzzy name search, for
exactly the same reasons BMW does. `builders` would be a near-copy of `makes`.

**A builder catalogue needs to parent models.** "Singer DLS" is a catalogue
entry with its own specs, its own page, and its own variants — so a builder must
sit at the top of the hierarchy, which is what `makes` is for.

**Dual-role companies break it outright.** Alpina holds its own WMI *and* builds
on BMW hardware. Under two tables it needs a row in each, so one company has two
pages and the reconciler has two candidate rows to match an incoming Wikidata
record against — a duplicate-identity bug, not a modelling preference.
Pininfarina shows the same thing across time: a contract coachbuilder for
decades, a manufacturer once it sold the Battista. Same company, same page,
changed status.

The common thread is that manufacturer status is a **property a company has**,
not a **kind of thing a company is**. Modelling it as two tables encodes a
temporary state as permanent identity.

## Decision

**One table: `companies`.** It holds every organisation that appears on or
behind a vehicle — BMW, Pontiac, Alpina, Singer, Zagato, Murphy. `makes` is
renamed to it, and every `make_id` foreign key becomes `company_id`.

**"Make" becomes a role, not a table.** A company that holds manufacturer
responsibility for a finished vehicle is a make; the ADR 0005 WMI test is
unchanged, but it now classifies rather than admits. Nothing is excluded from
the database for failing it.

**Roles are a many-to-many, because companies hold several and change over
time.** `company_roles` (lookup) + `company_role_assignments` (fact-bearing,
with provenance — "Zagato is a coachbuilder" is a sourced claim). Initial roles:
`manufacturer`, `coachbuilder`, `restomodder`, `tuner`, `designer`,
`engine_manufacturer`.

Explicit assignments rather than roles derived purely from data, because a
company's page must state what it is before any vehicles are linked to it — and
because a role table extends to kinds of company the schema does not model yet
without another migration.

`manufacturer` remains verifiable: it should agree with the presence of a WMI in
`external_ids`, and disagreement is a reconciliation flag rather than a silent
overwrite.

**Models hang off companies.** `models.company_id` is NOT NULL and points at
`companies` regardless of role, so Singer's product lines get the same catalogue
depth as BMW's with no exclusive arc and no branching in queries.

## Consequences

- **The hierarchy invariant becomes `companies → models → generations →
  model_years → configurations`.** Five levels, unchanged in shape; only the top
  level is renamed and widened. `CLAUDE.md` is updated to match.
- Singer, Zagato and Alpina each get exactly one row and one page. The
  duplicate-identity bug is structurally impossible.
- The reconciler has one match target per company instead of two, which removes
  an entire class of ambiguity before it can appear in data.
- `/makes/<slug>` remains a valid public route — it is a filtered view of
  companies holding the `manufacturer` role. Routing for non-manufacturer
  companies is a frontend decision, not a schema one.
- Roles extend without migration. A future interest in tuning or parts vendors
  is an INSERT into `company_roles`, which is the point of the lookup.
- ADR 0005 stands otherwise: the WMI test, `vehicle_derivations` keyed on the
  base generation with a nullable derived side, and the resolution of the
  coachbuilder gray area are all unchanged. Only its `builders` table is
  withdrawn.
- Done now because the entity tables hold only demo data. The same change after
  real reconciliation would mean rewriting foreign keys across every fact table.
