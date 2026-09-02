"""The mapper -> engine contract (ADR 0007 §2).

A mapper answers exactly three questions about one raw record - which
(source, external_id) it is about, what admission signals it carries, and
which field assertions it makes - expressed in these source-agnostic types.
The engine consumes them without ever inspecting a payload.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Assertion:
    """One field assertion from one source's record.

    `observed_value` is the audit-trail text stored in `field_provenance` -
    what the source said, untransformed (for multi-valued claims, the full
    canonical list). `value` is the projection-ready typed value the mapper's
    policy derived from it (e.g. the earliest year of several founding dates),
    used when this assertion wins §6 resolution.
    """

    field_name: str
    observed_value: str
    value: object
    # Set when the fact comes from a record other than the one being applied
    # (a brand member naming its canonical, ADR 0022 §5); provenance points
    # at the record that actually said it.
    raw_record_id: int | None = None


@dataclass(frozen=True)
class FlagRequest:
    """A review flag the mapper wants opened on the entity (ADR 0007 §8)."""

    kind: str
    field_name: str | None = None
    detail: dict | None = None


@dataclass(frozen=True)
class MappedRecord:
    """Everything the engine needs to know about one raw record."""

    external_id: str
    classes: frozenset[str]
    # None when the source has no usable label - such a record cannot mint an
    # entity and quarantines instead (a company row needs a name).
    name: str | None
    assertions: tuple[Assertion, ...] = ()
    flag_requests: tuple[FlagRequest, ...] = ()
