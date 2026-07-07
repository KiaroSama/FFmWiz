"""Pure helper functions extracted from FFmWiz.py, organized by dependency
level (LNN) and, for large levels, by concern. No test-monkeypatched or
mutable-global dependencies; downward-closed over ffmwiz.core.*. A module at
level N imports only ffmwiz.core.* and modules at levels < N (acyclic).
FFmWiz.py re-exports every name so the public API is unchanged.
"""
