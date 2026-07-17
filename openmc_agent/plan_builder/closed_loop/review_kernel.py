"""Gate-neutral structured review kernel public surface.

Facts and Placement adapters own evidence normalization; this module exposes
the common provider I/O contract without coupling either gate to the other.
"""

from .review_io import (
    StructuredReviewAttempt,
    StructuredReviewCallSpec,
    StructuredReviewResult,
    run_structured_review_call,
)

__all__ = ["StructuredReviewAttempt", "StructuredReviewCallSpec", "StructuredReviewResult", "run_structured_review_call"]
