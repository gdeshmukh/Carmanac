"""The reconciler: raw records -> entities + assertions + projections (ADR 0007).

One source-agnostic engine (`engine.py`), one thin mapper per source
(`sources/`), and the reviewed policy registries (`policy.py`) that the two
consult. Adding a source is a new mapper plus policy registrations - the
engine does not change.
"""
