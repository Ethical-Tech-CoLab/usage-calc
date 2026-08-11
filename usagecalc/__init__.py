"""usage-calc - what a Copilot CLI project actually cost, measured not guessed.

The package is deliberately layered so a consumer can take as much or as
little as it needs:

    intervals   pure interval maths, no I/O, no store knowledge
    store       reading the SQLite session store, with its reconciliation guard
    metrics     days, sittings, the person/model split, groupings
    contrib     merging usage exported from other machines
    energy      a published bracket, never collapsed to one number
    project     the only module that knows a repository name or an output path
    build       assemble the payload, render or inject the dashboard

See the README for what each number means and, more usefully, for the list of
things it does not mean.
"""

__version__ = "0.1.0"

from .build import build, inject, render, template_path  # noqa: F401
from .store import StoreError  # noqa: F401
