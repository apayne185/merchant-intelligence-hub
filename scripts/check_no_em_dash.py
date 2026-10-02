"""
House style: no em dashes (U+2014) anywhere in the repository.

Scans every git-tracked text file and exits non-zero with file:line for each
hit. Stdlib only, so it runs in pre-commit and CI without the project venv.

    python3 scripts/check_no_em_dash.py
"""
from __future__ import annotations

import subprocess  # nosec B404 - runs a fixed git command, no user input
import sys
from pathlib import Path

EM_DASH = "\u2014"


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)  # nosec B603 B607 - fixed git command
    return [Path(p) for p in out.stdout.decode().split("\0") if p]


def main() -> int:
    hits = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if EM_DASH in line:
                hits.append(f"{path}:{n}: {line.strip()[:100]}")
    for h in hits:
        print(h)
    if hits:
        print(f"\n{len(hits)} em dash(es) found. Use a colon, comma, parentheses or a hyphen instead.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
