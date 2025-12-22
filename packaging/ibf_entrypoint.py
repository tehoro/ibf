from __future__ import annotations

import sys

if getattr(sys, "frozen", False):
    print("Loading ibf... please wait.", flush=True)

from ibf.cli import main


if __name__ == "__main__":
    main()
