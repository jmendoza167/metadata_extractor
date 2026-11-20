"""Allow running metaex with ``python -m metaex``."""

import sys

from metaex.cli import main

if __name__ == "__main__":
    sys.exit(main())
