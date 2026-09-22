"""Enables `python -m nproj <command>` — see nproj/cli.py for the commands."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
