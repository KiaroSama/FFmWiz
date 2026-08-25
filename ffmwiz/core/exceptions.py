"""FFmWiz control-flow and error exceptions.

Leaf module extracted verbatim from FFmWiz.py; depends on nothing.
"""
from __future__ import annotations


class Back(Exception):
    pass


class ExitWizard(Exception):
    pass


class RetryAdditionalFile(Exception):
    pass


class FFprobeError(RuntimeError):
    pass


class ReverseBudgetError(RuntimeError):
    """Raised when a bounded reverse segment cannot be planned inside the peak
    memory cap: geometry/frame rate/pixel format the plan needs is unknown, a
    single decoded frame already exceeds the allowance, or the cap itself is
    smaller than the reserved fixed overhead. The caller decides how to present
    it -- the point is that a warning cannot turn an unbounded allocation into a
    bound (D12)."""
    pass


class ColorRangeUnresolvedError(RuntimeError):
    """Raised when a production builder is asked to resolve an unknown source
    color range without an explicit workflow decision and without an opt-in
    compatibility fallback."""
    pass



__all__ = [
    "Back",
    "ExitWizard",
    "RetryAdditionalFile",
    "FFprobeError",
    "ReverseBudgetError",
    "ColorRangeUnresolvedError",
]
