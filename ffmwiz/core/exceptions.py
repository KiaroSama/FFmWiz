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
    "ColorRangeUnresolvedError",
]
