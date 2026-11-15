"""Run all synthetic/offline tests and fail if any suite was silently skipped.

Usage (from any directory): <project>/.venv/Scripts/python.exe <project>/tools/run_checks.py
Requires Microsoft Edge, Python 3.12, and requirements-dev.txt. The browser tests
launch their own headless Edge against synthetic HTML, not a signed-in CDP session.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    # Avoid modifying tracked bytecode or starting visible Qt windows during checks.
    sys.dont_write_bytecode = True
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(ROOT / "tests"), top_level_dir=str(ROOT)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.testsRun == 0:
        print("Check failed: no tests were discovered.", file=sys.stderr)
        return 1
    if result.skipped:
        print(
            "Check failed: skipped tests are not a complete verification. "
            "Install Microsoft Edge and requirements-dev.txt, then rerun.",
            file=sys.stderr,
        )
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
