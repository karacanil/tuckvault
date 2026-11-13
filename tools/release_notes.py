"""Prints the CHANGELOG.md section of one version, for the GitHub release text.

    python tools\\release_notes.py 1.1.0
"""

import re
import sys
from pathlib import Path


def notes(version: str) -> str:
    text = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not match:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return match.group(1).strip() + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1]))
