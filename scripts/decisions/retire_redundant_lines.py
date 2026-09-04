"""Retire line rows whose subject entity is now a matched model (ADR 0022 §7).

Wikidata files a nameplate as a series whenever anything carries P179 to it,
so the line phase minted a "Corvette" row beside the as-filed Corvette model.
Once the badge vote attaches that entity to the model, the pass stops
deriving the row - the match rungs settle the entity before the line rung
sees it - but the rows already created stay until something deletes them.
That is this script's one act.

The test is derived, never listed: a line whose company and normalized name
match an entity this source now maps to a model, at either historical key
(the maker side, or the destination the vote picked). A row still holding
live members is reported and left alone - its members are the evidence a
human should see before the row goes.

Dry-run by default; `--execute` applies.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from carmanac.db.session import SessionLocal
from carmanac.reconcile.matching import normalize_name
from carmanac.reconcile.wikidata_models_pass import _WikidataModelsPass


def main() -> None:
    execute = "--execute" in sys.argv
    with SessionLocal() as session:
        pass_ = _WikidataModelsPass(session)
        # ALL member rows, tombstones included: any row blocks the FK, and a
        # blocked line beats an aborted transaction.
        member_counts = dict(
            session.execute(
                text(
                    "SELECT model_line_id, count(*) FROM model_line_members GROUP BY model_line_id"
                )
            ).all()
        )

        retire: dict[int, tuple[str, str, str]] = {}
        blocked: list[tuple[str, str, int]] = []
        for qid, subject in sorted(pass_.subjects.items(), key=lambda kv: int(kv[0][1:])):
            model_id = pass_.model_by_qid.get(qid)
            label = subject.entity.label
            if model_id is None or not label:
                continue
            # Either key the row could sit at: the model's own company (where
            # the vote sends it) or a maker that resolved to a held company.
            companies = {pass_.models[model_id].company_id}
            companies.update(
                pass_.company_by_qid[m] for m in subject.entity.makers if m in pass_.company_by_qid
            )
            for company_id in companies:
                key = (company_id, normalize_name(pass_._strip(label, company_id)))
                line_id = pass_.line_by_key.get(key)
                if line_id is None or line_id in retire:
                    continue
                where = f"{pass_.companies[company_id].slug}/{key[1]}"
                if member_counts.get(line_id):
                    blocked.append((where, label, member_counts[line_id]))
                    continue
                retire[line_id] = (where, label, pass_._slug_pair(model_id))

        for line_id, (where, label, model) in sorted(retire.items(), key=lambda kv: kv[1][0]):
            print(f"retire line {line_id:5d}  {where:34s}  {label!r} is now {model}")
        for where, label, count in sorted(blocked):
            print(f"BLOCKED     {where:34s}  {label!r} still holds {count} member row(s)")
        print(f"retire={len(retire)} blocked={len(blocked)}")

        if not execute:
            print("dry run - pass --execute to apply")
            return
        session.execute(
            text("DELETE FROM model_lines WHERE id = ANY(:ids)"), {"ids": sorted(retire)}
        )
        session.commit()
        print("done; re-run the wikidata models pass to confirm it re-derives nothing")


if __name__ == "__main__":
    main()
