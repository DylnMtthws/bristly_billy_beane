"""Pure functions from card text to mechanic facts.

This package is the substrate the Research Assistant reasons over, and its one
rule is absolute: **stdlib and ``re`` only.** No configuration, no database, no
filesystem, no other ``sabermetrics`` module, no vendor SDK. A function here is
a total function of its arguments, so it is trivially testable, trivially
cacheable, and identical in a test, a batch job and a request.

The rule is enforced by ``tests/test_package_boundaries.py``, not by
convention, because the first config read added "just for a default" is what
turns a pure library into one that behaves differently depending on where it
runs.

**No scoring, no weights, no judgement.** A mechanic tag says what a card *does*
as an auditable predicate over its text. What that is worth is a different
question, answered elsewhere against a stated objective. Modules that carry
graded weights stay in ``analytics/`` — see ``analytics/keyword_scoring.py``,
which was split out of ``oracle_keywords`` for exactly this reason.
"""
