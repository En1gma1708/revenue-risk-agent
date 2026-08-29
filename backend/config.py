"""
Shared constants used across generate_cases.py, run_batch.py, and anything that needs to reason
about "today" relative to the fixed synthetic demo timeline. Split out from generate_cases.py so
other modules don't need to import a module whose top-level code is meant to be run as a script.
"""

from datetime import datetime

SEED = 42

# "Today" for the simulated demo, fixed so PTP dates can be pre-dated relative to it without
# needing a real wait or a simulated-clock UI control (see PRD.md SS11).
DEMO_TODAY = datetime(2026, 8, 24, 12, 0)
