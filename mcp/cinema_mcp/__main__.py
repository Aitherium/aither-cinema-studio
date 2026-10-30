"""``python -m cinema_mcp`` runs the same server as the ``cinema-mcp`` script."""

from __future__ import annotations

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
