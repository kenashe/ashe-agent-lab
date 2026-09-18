"""Enables ``python -m ashe_lab`` as an equivalent to the ``ashe-lab`` script.

Useful when the package is on ``PYTHONPATH`` but not installed, which is the
situation inside a fresh clone before ``pip install -e .``.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
