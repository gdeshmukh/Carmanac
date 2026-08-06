"""The reconciler's own record-keeping, shared by every pass.

Two things every pass does identically, regardless of which level it writes:

- **mark** a raw record as processed at the current reconciler version, so
  staleness is queryable after a policy change.
- **log a decision** per attempted record - which rung decided, by what
  method, with what outcome. This is the labeled set the charter gates
  Tier 2/3 sources on.

Both upsert, because a pass re-run over the same record must update its
bookkeeping rather than collide with it.
"""

from __future__ import annotations

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import MatchDecision, RawRecord, ReconciledRecord
from carmanac.reconcile import policy

# Rows per INSERT. Postgres caps a statement at 65535 bind parameters and a
# decision row has ~9 columns, so this sits an order of magnitude below it.
DECISION_CHUNK = 500

_DECISION_UPDATE_COLUMNS = (
    "raw_record_id",
    "rung",
    "method",
    "outcome",
    "detail",
    "reconciler_version",
)


def mark_reconciled(session: Session, record: RawRecord) -> None:
    """Record that this pass has processed `record` at the current version."""
    session.execute(
        pg_insert(ReconciledRecord)
        .values(raw_record_id=record.id, reconciler_version=policy.RECONCILER_VERSION)
        .on_conflict_do_update(
            index_elements=["raw_record_id"],
            set_={
                "reconciled_at": func.now(),
                "reconciler_version": policy.RECONCILER_VERSION,
            },
        )
    )


class DecisionLog:
    """Accumulates `match_decisions` rows for one pass run, then upserts them.

    Buffered rather than written per record because the passes that use this
    process tens of thousands of records; one statement per row would dominate
    their runtime.

    Keyed by external id so a record decided twice in one run keeps only its
    last decision - the sweep pass revisits entities across its phases, and the
    decision that matters is the one it settled on.
    """

    def __init__(self, session: Session, source_id: int, pass_name: str):
        self.session = session
        self.source_id = source_id
        self.pass_name = pass_name
        self._rows: dict[str, dict] = {}

    def record(
        self,
        record: RawRecord,
        outcome: str,
        *,
        rung: str | None = None,
        method: str | None = None,
        detail: dict | None = None,
    ) -> None:
        self.record_key(
            record.external_id,
            outcome,
            raw_record_id=record.id,
            rung=rung,
            method=method,
            detail=detail,
        )

    def record_key(
        self,
        external_id: str,
        outcome: str,
        *,
        raw_record_id: int | None = None,
        rung: str | None = None,
        method: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Keyed variant for passes whose decision subject is one of OUR rows
        rather than a source record (generation placement decides per
        configuration; the deciding raw record is evidence, not the key)."""
        self._rows[external_id] = {
            "source_id": self.source_id,
            "pass_name": self.pass_name,
            "external_id": external_id,
            "raw_record_id": raw_record_id,
            "rung": rung,
            "method": method,
            "outcome": outcome,
            "detail": detail,
            "reconciler_version": policy.RECONCILER_VERSION,
        }

    def __len__(self) -> int:
        return len(self._rows)

    def flush(self) -> None:
        """Upsert every buffered decision, in external-id order.

        Sorted for the engine's determinism rule: two runs over the same data
        must issue the same statements in the same order.
        """
        rows = [self._rows[k] for k in sorted(self._rows, key=_external_id_sort_key)]
        for start in range(0, len(rows), DECISION_CHUNK):
            stmt = pg_insert(MatchDecision).values(rows[start : start + DECISION_CHUNK])
            self.session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_match_decisions_source_id_pass_name_external_id",
                    set_={
                        **{c: getattr(stmt.excluded, c) for c in _DECISION_UPDATE_COLUMNS},
                        "decided_at": func.now(),
                    },
                )
            )
        self._rows.clear()


def _external_id_sort_key(external_id: str) -> tuple[int, int, str]:
    """Sort QIDs and `kind:<n>` ids numerically, everything else lexically.

    Same intent as the engine's record ordering: Q9 before Q10, `vehicle:2`
    before `vehicle:10`.
    """
    prefix, _, rest = external_id.rpartition(":")
    digits = rest[1:] if rest[:1] == "Q" else rest
    if digits.isdigit():
        return (0, int(digits), prefix)
    return (1, 0, external_id)


# Tables carrying a GIN trigram index on `name` (see db/models/hierarchy.py).
_TRIGRAM_TABLES = frozenset({"companies", "models", "generations"})


def trigram_candidates(
    session: Session,
    table: str,
    name: str,
    *,
    limit: int = 5,
    threshold: float = 0.3,
    company_id: int | None = None,
) -> list[dict]:
    """Near-miss names from `table`, for a reviewer to choose between.

    Candidates only - nothing here is ever auto-accepted, which is why the
    threshold can stay loose. `company_id` scopes the search where the table
    has one (models), since a nameplate only competes within its own make.
    """
    if table not in _TRIGRAM_TABLES:
        raise ValueError(f"no trigram index on {table!r}")
    scope = "AND company_id = :company_id" if company_id is not None else ""
    rows = session.execute(
        text(
            # `table` is interpolated, so it is checked against the allow-list
            # above rather than passed through as a bind parameter (identifiers
            # cannot be bound).
            f"""SELECT name, slug, round(similarity(name, :n)::numeric, 2) AS sim
                FROM {table}
                WHERE similarity(name, :n) > :threshold {scope}
                ORDER BY sim DESC, slug LIMIT :limit"""
        ),
        {"n": name, "threshold": threshold, "limit": limit}
        | ({"company_id": company_id} if company_id is not None else {}),
    ).all()
    return [{"name": r.name, "slug": r.slug, "similarity": float(r.sim)} for r in rows]
