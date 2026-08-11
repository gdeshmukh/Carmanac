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

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import Company, MatchDecision, Model, RawRecord, ReconciledRecord, SlugAlias
from carmanac.db.models.reconciliation import ALIAS_ARC_BY_KIND
from carmanac.reconcile import policy

# One lock for every address writer (ADR 0019): the mint-capable passes and
# the rename/merge scripts. Arbitrary constant; what matters is that all of
# them use the same one.
_ADDRESS_LOCK_KEY = 0x0019_ADD2

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


def hold_address_lock(session: Session) -> None:
    """Serialize address writers (ADR 0019).

    Session-level advisory lock, not transaction-level, because the big
    passes commit in chunks and must stay covered across their commits.
    `unlock_all` first: pooled connections inherit lock counts from earlier
    holders on the same connection, and without the reset a stale stack
    would make a fresh writer on another connection fail forever. Fail-fast
    rather than block - the operator re-runs when the other writer is done.
    Released with the connection.
    """
    session.execute(text("SELECT pg_advisory_unlock_all()"))
    got = session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADDRESS_LOCK_KEY})
    if not got.scalar():
        raise RuntimeError(
            "another address writer (a pass run or a rename/merge script) holds "
            "the advisory lock; retry when it finishes"
        )


def validate_registry_pairs(session: Session) -> None:
    """ADR 0019 §3: every slug-pair registry entry must resolve against LIVE
    pairs, or the consuming pass refuses to run.

    Renames migrate their registry keys atomically (the rename scripts refuse
    to execute ahead of the rewrite); this is the enforcement that it
    happened. Deliberately not alias-aware: a key resolving only through
    history would behave differently in a fresh environment, and a merge
    could silently carry a negative judgment onto a row its author never
    judged. A stale negative is the worst case - it re-arms the exact match
    a human dismissed - so the whole run aborts, which is loud and cheap to
    recover from. An empty models table is a fresh clone or test DB: nothing
    to resolve against, nothing to write wrongly, skip.
    """
    live = {
        f"{company_slug}/{model_slug}"
        for company_slug, model_slug in session.execute(
            select(Company.slug, Model.slug).join(Model, Model.company_id == Company.id)
        )
    }
    if not live:
        return
    stale = sorted(
        {
            f"WIKIDATA_MODEL_MATCHES[{qid!r}] -> {pair!r}"
            for qid, pair in policy.WIKIDATA_MODEL_MATCHES.items()
            if pair not in live
        }
        | {
            f"WIKIDATA_MODEL_NEGATIVES ({qid!r}, {pair!r})"
            for qid, pair in policy.WIKIDATA_MODEL_NEGATIVES
            if pair not in live
        }
        | {
            f"SECTION_ARTICLE_MODELS[{qid!r}] -> {pair!r}"
            for qid, pair in policy.SECTION_ARTICLE_MODELS.items()
            if pair not in live
        }
    )
    if stale:
        raise RuntimeError(
            "stale slug-pair registry keys - a rename outran its policy.py "
            "rewrite, and running on would disarm recorded judgments "
            "(ADR 0019 §3). Fix the entries and re-run:\n  " + "\n  ".join(stale)
        )


def alias_addresses(session: Session, kind: str) -> dict[tuple[int | None, str], int]:
    """One kind's retired addresses: (scope_company_id, slug) -> current row.

    Every mint site unions these into its occupancy state so a freed address
    is never re-minted (the INSERT trigger is the mechanical backstop; this
    keeps the polite path a flag instead of an aborted run), and the line
    lookup resolves through them so a renamed line matches its own row
    instead of duplicate-minting.
    """
    arc = getattr(SlugAlias, ALIAS_ARC_BY_KIND[kind])
    return {
        (scope, slug): target
        for scope, slug, target in session.execute(
            select(SlugAlias.scope_company_id, SlugAlias.slug, arc).where(
                SlugAlias.entity_kind == kind
            )
        )
    }


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
