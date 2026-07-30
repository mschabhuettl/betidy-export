#!/usr/bin/env python3
"""
One-command BeTidy -> Donetick migration.

Chains the two steps into a single command:

  1. betidy_extract.py  — log in to BeTidy with your credentials (Cognito) and
                          fetch your data into betidy_export.json.
  2. donetick_import.py — create the chores in Donetick via its API.

Any extra command-line flags are passed straight through to donetick_import.py
(e.g. --labels-map, --dry-run, --limit, --skip-existing, --include-inactive).

Your BeTidy credentials are used only for the local Cognito login in step 1 and
are never sent to Donetick.

Usage:
    export BETIDY_EMAIL="you@example.com"
    export BETIDY_PASSWORD="your-betidy-password"
    export DONETICK_URL="https://donetick.example.com"
    export DONETICK_TOKEN="your-donetick-access-token"

    python betidy_to_donetick.py                       # extract + import
    python betidy_to_donetick.py --labels-map labels.json
    python betidy_to_donetick.py --dry-run             # extract, then preview only
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def require(*keys):
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        sys.exit("ERROR: missing environment variable(s): " + ", ".join(missing))


def run(script, args=()):
    cmd = [sys.executable, os.path.join(HERE, script), *args]
    print(f"\n▶ {script} {' '.join(args)}".rstrip(), flush=True)
    result = subprocess.run(cmd, env=os.environ.copy())
    if result.returncode != 0:
        sys.exit(f"{script} failed (exit {result.returncode})")


def main():
    passthrough = sys.argv[1:]  # forwarded to donetick_import.py
    require("BETIDY_EMAIL", "BETIDY_PASSWORD", "DONETICK_URL")
    if "--dry-run" not in passthrough:
        require("DONETICK_TOKEN")

    run("betidy_extract.py")  # -> betidy_export.json
    run("donetick_import.py", passthrough)  # -> Donetick
    print("\n✅ Done.", flush=True)


if __name__ == "__main__":
    main()
