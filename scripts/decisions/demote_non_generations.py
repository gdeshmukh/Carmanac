"""Retire the links of generation rows ruled wrong-grain (ADR 0018 §2).

Wikidata's P179 minted trim/body lineages as generations; the ruled entries
live in `policy.NOT_A_GENERATION` (the registry is what keeps this from
resurrecting - the wd-models pass re-asserts links from P179 every run).
This script is the applied half of the decision:

- The dry run (default) prints, per registry entry AND per unruled member
  of the censused species (a proposal with the same evidence, no verdict):
  the generation row, its live links, any placements citing it, and open
  flags on the row. Reviewed before every --execute - the standing gate.
- --execute applies registry entries only: live `generation_model_links`
  retire by self-supersession (the role-retraction pattern) and open flags
  on those generation rows resolve with the verdict recorded.

The rows, their external ids, and their facts all STAY - they are real
entities of the trim-line/derivation kind not yet modeled. Overlap flags
are untouched: with the links retired, the placement pass's
`candidates_no_longer_overlap` path dismisses them on its next run.
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    Configuration,
    ExternalId,
    Generation,
    GenerationModelLink,
    Model,
    ReconciliationFlag,
    Source,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy

# The rest of the censused species (2026-08-07), NOT ruled: presented by the
# dry run as proposals with evidence. An entry moves to the registry only by
# ruling. The 5 Turbo is a genuine homologation gray case - the evidence is
# presented without a lean either way. The Civic Type R FL5 left this list
# by the opposite ruling (2026-08-07): it IS a generation - FL5 is its code,
# under the type-r filing - so it belongs in neither list.
PROPOSED_SPECIES: dict[str, str] = {
    "Q1299603": "5 Turbo (homologation gray case)",
}


def census(session: Session) -> list[dict]:
    """Evidence per species QID, registry entries first."""
    wikidata_id = session.scalar(select(Source.id).where(Source.name == "Wikidata"))
    entries = []
    for qid in [*policy.NOT_A_GENERATION, *PROPOSED_SPECIES]:
        generation = session.scalars(
            select(Generation)
            .join(ExternalId, ExternalId.generation_id == Generation.id)
            .where(ExternalId.source_id == wikidata_id, ExternalId.external_id == qid)
        ).one_or_none()
        if generation is None:
            entries.append({"qid": qid, "generation": None})
            continue
        links = session.execute(
            select(GenerationModelLink.id, Source.name, Company.slug, Model.slug)
            .join(Source, Source.id == GenerationModelLink.source_id)
            .join(Model, Model.id == GenerationModelLink.model_id)
            .join(Company, Company.id == Model.company_id)
            .where(
                GenerationModelLink.generation_id == generation.id,
                GenerationModelLink.superseded_by.is_(None),
            )
        ).all()
        placements = session.scalars(
            select(Configuration.slug).where(Configuration.generation_id == generation.id)
        ).all()
        flags = session.execute(
            select(
                ReconciliationFlag.id, ReconciliationFlag.kind, ReconciliationFlag.field_name
            ).where(
                ReconciliationFlag.generation_id == generation.id,
                ReconciliationFlag.status == "open",
            )
        ).all()
        entries.append(
            {
                "qid": qid,
                "generation": generation,
                "company": session.get(Company, generation.company_id).slug,
                "verdict": policy.NOT_A_GENERATION.get(qid),
                "note": PROPOSED_SPECIES.get(qid),
                "links": links,
                "placements": placements,
                "flags": flags,
            }
        )
    return entries


def apply(session: Session, entries: list[dict]) -> tuple[int, int]:
    """Retire registry entries' live links; resolve open flags on the rows."""
    retired = resolved = 0
    for entry in entries:
        verdict = entry.get("verdict")
        if verdict is None or entry["generation"] is None:
            continue
        for link_id, *_ in entry["links"]:
            link = session.get(GenerationModelLink, link_id)
            link.superseded_by = link.id
            retired += 1
        for flag_id, *_ in entry["flags"]:
            flag = session.get(ReconciliationFlag, flag_id)
            flag.status = "resolved"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": f"not_a_generation:{verdict}"}
            resolved += 1
    session.commit()
    return retired, resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="retire registry entries' links (default: dry run)"
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        entries = census(session)
        for entry in entries:
            generation = entry["generation"]
            if generation is None:
                print(f"{entry['qid']}: no generation row attached - nothing to show")
                continue
            head = (
                f"RULED not_a_generation:{entry['verdict']}"
                if entry["verdict"]
                else f"PROPOSAL (not ruled) - {entry['note']}"
            )
            span = f"{generation.start_year or '?'}–{generation.end_year or 'present/?'}"
            print(f"{entry['qid']}  {entry['company']}/{generation.slug}  [{head}]")
            print(f"  name={generation.name!r} span={span} codes={generation.chassis_codes}")
            print(f"  live links ({len(entry['links'])}):")
            for _link_id, source_name, company_slug, model_slug in entry["links"]:
                print(f"    {company_slug}/{model_slug}  asserted by {source_name}")
            print(f"  placements citing it: {len(entry['placements'])}")
            for slug in entry["placements"][:5]:
                print(f"    {slug}")
            print(f"  open flags on the row: {len(entry['flags'])}")
            for _flag_id, kind, field_name in entry["flags"]:
                print(f"    {kind} ({field_name})")
            if entry["verdict"] and entry["placements"]:
                # The placement pass withdraws these mechanically once the
                # links are gone (sole-placer supersession), but an execute
                # that changes placements deserves eyes first.
                print("  WARNING: live placements cite this row; they will be withdrawn")

        if not args.execute:
            print("dry run - pass --execute to retire the registry entries' links")
            return 0

        retired, resolved = apply(session, entries)
        print(
            f"retired {retired} link(s), resolved {resolved} flag(s); "
            "re-run the wd-models, sections and placement passes to converge"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
