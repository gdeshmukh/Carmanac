"""Retire the line rows ruled generation-grain (WIKIDATA_GENERATION_GRAIN).

Wikidata files a per-generation entity as a series whenever anything carries
P179 to it, so the line phase minted rows like "Corvette C7" and "Passat B6"
- generations sitting in the lines table. Ruled 2026-08-25: each resolves in
the generations table instead, adopting a generation that already stands
under the brand or minting one from the entity's own record; the line row
goes.

The pass owns the generations side: with the registry in place its structure
phase adopts or mints and attaches the external id, and its line track never
sees these entities again. This script owns the one act the pass never
performs - deleting the rows the old derivation created. Each row prints
with the verdict the next pass run derives for its entity, from the pass's
own loaded state. A row still holding live members is never deleted.

Dry-run by default; `--execute` applies.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.addressing import slugify
from carmanac.reconcile.matching import normalize_name
from carmanac.reconcile.wikidata_models_pass import (
    _WikidataModelsPass,
    brand_destination,
    line_brand_wearers,
)


def main() -> None:
    execute = "--execute" in sys.argv
    with SessionLocal() as session:
        pass_ = _WikidataModelsPass(session)
        # ALL member rows, tombstones included: any row blocks the FK, and
        # a blocked line beats an aborted transaction.
        member_counts = dict(
            session.execute(
                text(
                    "SELECT model_line_id, count(*) FROM model_line_members GROUP BY model_line_id"
                )
            ).all()
        )
        model_holding = set(pass_.models_by_name)

        def slug_pair(model_id: int) -> str:
            model = pass_.models[model_id]
            return f"{pass_.companies[model.company_id].slug}/{model.slug}"

        retire: list[tuple[int, str, str, str, str]] = []
        blocked: list[tuple[str, str]] = []
        for qid in sorted(policy.WIKIDATA_GENERATION_GRAIN, key=lambda q: int(q[1:])):
            subject = pass_.subjects.get(qid)
            if subject is None or subject.entity.label is None:
                blocked.append((qid, "no sweep record with a label"))
                continue
            entity = subject.entity
            makers = sorted(
                {pass_.company_by_qid[m] for m in entity.makers if m in pass_.company_by_qid}
            )
            if len(makers) != 1:
                blocked.append((entity.label, "maker unresolved - old key unknown"))
                continue
            # The row may sit at either historical key: the maker side (held
            # rows filed there) or the destination side (a clean vote filed
            # or relocated it under the brand before the grain ruling).
            keys = {(makers[0], normalize_name(pass_._strip(entity.label, makers[0])))}
            wearers = line_brand_wearers(entity.label, pass_.companies_by_norm)
            destination, _reason = brand_destination(makers[0], wearers, model_holding)
            if destination is not None and destination != makers[0]:
                keys.add((destination, normalize_name(pass_._strip(entity.label, destination))))
            found = {pass_.line_by_key[k]: k for k in keys if k in pass_.line_by_key}
            if not found:
                continue  # already retired
            if len(found) > 1:
                blocked.append((entity.label, "derives two rows - review"))
                continue
            line_id, (row_company_id, _norm) = next(iter(found.items()))
            maker = pass_.companies[row_company_id]
            old_name = pass_._strip(entity.label, row_company_id)
            if member_counts.get(line_id, 0):
                blocked.append((entity.label, "row holds member rows"))
                continue
            resolved = pass_.generation_grain.get(qid)
            if resolved is None:
                blocked.append((entity.label, "registry anchor unresolved"))
                continue
            company_id, model_id, display = resolved
            address = f"{pass_.companies[company_id].slug}/{slugify(display)}"
            occupant = pass_.generation_by_company_slug.get((company_id, slugify(display)))
            verdict = (
                f"adopts {address} {display!r}"
                if occupant is not None
                else f"mints {address} {display!r}"
            )
            if model_id is not None:
                verdict += f"  linked to {slug_pair(model_id)}"
            parents = ", ".join(
                f"{pass_.subjects[t].entity.label!r}"
                + (
                    f" (matched {slug_pair(pass_.model_by_qid[t])})"
                    if t in pass_.model_by_qid
                    else " (unmatched)"
                )
                for t in entity.series_of
                if t in pass_.subjects
            )
            members = ", ".join(
                f"{s.entity.label!r}" for s in pass_.subjects.values() if qid in s.entity.series_of
            )
            evidence = f"parent: {parents or 'none'}   members: {members or 'none'}"
            retire.append((line_id, maker.slug or "?", old_name, verdict, evidence))

        for _line_id, maker_slug, old_name, verdict, evidence in retire:
            print(f"RETIRE  {maker_slug}: {old_name!r}  ->  {verdict}")
            print(f"        {evidence}")
        for label, why in blocked:
            print(f"BLOCKED  {label!r}  ({why})")

        print(f"\n{len(retire)} row(s) to retire, {len(blocked)} blocked")
        if not execute:
            print("dry run: pass --execute to delete the rows")
            return
        for line_id, *_rest in retire:
            session.execute(text("DELETE FROM model_lines WHERE id = :id"), {"id": line_id})
        session.commit()
        print(f"deleted {len(retire)} row(s)")


if __name__ == "__main__":
    main()
