"""One-time derived-state refresh for ADR 0013 §5, then the v10 pass.

    .venv/bin/python scripts/decisions/apply_adr_0013_refresh.py

Three steps, all over reconciler-DERIVED state (raw records and the labeled
set untouched; everything deleted is rebuilt from raw by the run at the end):

1. Unwind cross-badge alias-only model attachments (the audit's Trailseeker
   shape): the QID's external id and this pass's assertions go; the v10 run
   re-processes the entity into a `market_name_or_rebadge` flag.
2. Drop all lines, memberships and generations so their names and slugs are
   rebuilt under §1's recorded-prefix stripping ("audi-a3-8v" -> "a3-8v").
   Done now, deliberately before the year pass gives them consumers.
3. Run the v10 pass.

Idempotent: a second invocation finds nothing to unwind and the pass re-runs
as a no-op.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, update

from carmanac.db.models import (
    ExternalId,
    FieldProvenance,
    Generation,
    Model,
    ModelLine,
    ModelLineMember,
    ReconciliationFlag,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile.wikidata_models_pass import (
    _WikidataModelsPass,
    run_wikidata_models_pass,
)

log = logging.getLogger(__name__)


def unwind_cross_badge(session) -> int:
    """Detach models whose Wikidata QID only ever hit via an alias while its
    label wears a different held brand - computed live with the v10 pass's
    own helpers, so this stays in lockstep with the matcher."""
    p = _WikidataModelsPass(session)
    unwound = 0
    for qid, model_id in sorted(p.model_by_qid.items(), key=lambda kv: int(kv[0][1:])):
        subject = p.subjects.get(qid)
        if subject is None:
            continue
        subject.held_companies = sorted(
            {p.company_by_qid[m] for m in subject.entity.makers if m in p.company_by_qid}
        )
        hit = p._name_hits(subject).get(model_id)
        if hit is None or hit[1] == 0:
            continue  # curated/legacy or an honest label hit
        if not p._is_cross_badge(subject.entity, model_id):
            continue
        model = p.models[model_id]
        print(
            f"  unwinding {qid} ({subject.entity.label!r}) from "
            f"{p.companies[model.company_id].slug}/{model.slug}"
        )
        session.execute(
            delete(FieldProvenance).where(
                FieldProvenance.model_id == model_id,
                FieldProvenance.source_id == p.source.id,
            )
        )
        session.execute(update(Model).where(Model.id == model_id).values(summary=None))
        session.execute(
            delete(ExternalId).where(
                ExternalId.source_id == p.source.id,
                ExternalId.external_id == qid,
                ExternalId.model_id == model_id,
            )
        )
        unwound += 1
    return unwound


def drop_lines_and_generations(session) -> dict[str, int]:
    counts = {}
    counts["memberships"] = session.execute(delete(ModelLineMember)).rowcount
    counts["lines"] = session.execute(delete(ModelLine)).rowcount
    counts["generation_flags"] = session.execute(
        delete(ReconciliationFlag).where(ReconciliationFlag.generation_id.isnot(None))
    ).rowcount
    counts["generation_external_ids"] = session.execute(
        delete(ExternalId).where(ExternalId.generation_id.isnot(None))
    ).rowcount
    counts["generation_assertions"] = session.execute(
        delete(FieldProvenance).where(FieldProvenance.generation_id.isnot(None))
    ).rowcount
    counts["generations"] = session.execute(delete(Generation)).rowcount
    return counts


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    with SessionLocal() as session:
        print("unwinding cross-badge alias attachments ...")
        unwound = unwind_cross_badge(session)
        print(f"  {unwound} unwound")
        counts = drop_lines_and_generations(session)
        print("dropped for rebuild:", ", ".join(f"{k}={v}" for k, v in counts.items()))
        session.commit()

    with SessionLocal() as session:
        stats = run_wikidata_models_pass(session)
    print(f"\nv10 pass complete.\n  {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
