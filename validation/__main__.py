"""CLI entry point for `python -m validation --cik ...`."""

import sys

from .accounting_equation import main

if __name__ == "__main__":
    sys.exit(main())

