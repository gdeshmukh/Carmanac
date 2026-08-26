"""Relocate stranded model-line rows to the carmaker their name states.

Wikidata's maker property points at holding companies (Mercedes-Benz Group,
Ford Motor Company), so the line phase filed real lines under companies that
hold zero models while vPIC filed their models under the carmakers. The pass
now derives a stranded line's company with `line_destination` and files new
entities there directly; rows created under the old derivation have to be
moved once, and any later vote change (`line_awaits_relocation` flags, an
`awaits-review` hold lifting) is applied here rather than by the pass, which
never abandons an existing row.

The census drives off the pass's own loaded state and votes on entity
LABELS exactly as `_line_phase` does - a row moves only when the pass's
current derivation puts it elsewhere, so a pass run after `--execute`
re-derives every row exactly where it sits. Rows no current sweep record
derives are never touched. Held rows and open vote questions (ambiguous or
model-less brands) are listed and left in place; a destination whose natural
key or slug is already taken is a merge question: listed, never applied.

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
    line_brand_wearers,
    line_destination,
)


def main() -> None:
    execute = "--execute" in sys.argv
    with SessionLocal() as session:
        pass_ = _WikidataModelsPass(session)
        model_holding = set(pass_.models_by_name)
        lines_by_id = {line_id: key for key, line_id in pass_.line_by_key.items()}
        taken_slugs = {
            (company_id, slug)
            for company_id, slug in session.execute(
                text("SELECT company_id, slug FROM model_lines")
            )
        }
        member_counts = dict(
            session.execute(
                text(
                    "SELECT model_line_id, count(*) FROM model_line_members"
                    " WHERE superseded_by IS NULL GROUP BY model_line_id"
                )
            ).all()
        )

        moves: dict[int, tuple[int, int, str, str]] = {}  # line id -> (dest, name, slug)
        held, flagged, blocked, moved_keys = [], [], [], set()
        for qid in sorted(pass_.p179_referenced, key=lambda q: int(q[1:])):
            subject = pass_.subjects.get(qid)
            if subject is None or qid in pass_.model_by_qid or qid in pass_.generation_by_qid:
                continue
            entity = subject.entity
            if entity.label is None:
                continue
            held_companies = sorted(
                {pass_.company_by_qid[m] for m in entity.makers if m in pass_.company_by_qid}
            )
            if len(held_companies) != 1:
                continue
            maker_id = held_companies[0]
            maker = pass_.companies[maker_id]
            old_name = pass_._strip(entity.label, maker_id)
            old_key = (maker_id, normalize_name(old_name))
            line_id = pass_.line_by_key.get(old_key)
            hold = pass_.line_holds.get((maker.slug, normalize_name(old_name)))
            if hold is not None:
                if line_id is not None:
                    held.append((maker, old_name, hold))
                continue
            wearers = line_brand_wearers(entity.label, pass_.companies_by_norm)
            destination, flag_reason = line_destination(maker_id, wearers, model_holding)
            if flag_reason is not None:
                if line_id is not None:
                    candidates = ", ".join(
                        f"{pass_.companies[cid].name} ({pass_.companies[cid].slug or 'no slug'})"
                        for cid in wearers
                    )
                    flagged.append((maker, old_name, flag_reason, candidates))
                continue
            if destination == maker_id or line_id is None:
                continue  # in place already, or no row: the pass files fresh entities itself
            dest = pass_.companies[destination]
            new_name = pass_._strip(entity.label, destination)
            new_slug = slugify(new_name)
            new_key = (destination, normalize_name(new_name))
            if not new_slug or new_slug in policy.RESERVED_ROUTE_SEGMENTS:
                blocked.append((maker, old_name, dest, new_name, "unslugable"))
                continue
            prior = moves.get(line_id)
            if prior is not None:
                if prior[:3] != (destination, new_name, new_slug):
                    blocked.append((maker, old_name, dest, new_name, "two derivations disagree"))
                continue
            if (
                new_key in pass_.line_by_key
                or new_key in moved_keys
                or (destination, new_slug) in taken_slugs
            ):
                blocked.append((maker, old_name, dest, new_name, "collision - merge question"))
                continue
            moved_keys.add(new_key)
            taken_slugs.add((destination, new_slug))
            moves[line_id] = (destination, new_name, new_slug, old_name)

        for line_id, (destination, new_name, new_slug, old_name) in sorted(
            moves.items(), key=lambda kv: kv[1]
        ):
            old_company = pass_.companies[lines_by_id[line_id][0]]
            dest = pass_.companies[destination]
            members = member_counts.get(line_id, 0)
            carried = f"  [{members} member(s) carried]" if members else ""
            print(
                f"MOVE  {old_company.slug}: {old_name!r}"
                f"  ->  {dest.slug}/{new_slug}  {new_name!r}{carried}"
            )
        for maker, name, why in sorted(set(held), key=lambda h: (h[0].slug or "", h[1])):
            print(f"HELD  {maker.slug}: {name!r}  ({why})")
        for maker, name, reason, candidates in sorted(
            set(flagged), key=lambda f: (f[0].slug or "", f[1])
        ):
            print(f"FLAG  {maker.slug}: {name!r}  {reason}: {candidates}")
        for maker, old_name, dest, new_name, why in blocked:
            print(f"BLOCKED  {maker.slug}: {old_name!r}  ->  {dest.slug}: {new_name!r}  ({why})")

        print(
            f"\n{len(moves)} move(s), {len(set(held))} held, {len(set(flagged))} flagged,"
            f" {len(blocked)} blocked"
        )
        if not execute:
            print("dry run: pass --execute to apply the moves")
            return
        for line_id, (destination, new_name, new_slug, _old) in moves.items():
            session.execute(
                text(
                    "UPDATE model_lines SET company_id = :cid, name = :name, slug = :slug"
                    " WHERE id = :id"
                ),
                {"cid": destination, "name": new_name, "slug": new_slug, "id": line_id},
            )
        session.commit()
        print(f"applied {len(moves)} move(s)")


if __name__ == "__main__":
    main()
