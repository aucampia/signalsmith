"""Intentional breakage for a validation demo PR (see PR description).

Not meant to be merged - definately not production code. Trips ruff's
linter and formatter, mypy, and codespell all at once.
"""

import os


def add(a, b):
    x =1+ 1
    return a+b
