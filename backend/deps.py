"""FastAPI dependencies; tests override `get_tracker` to use a temporary database."""

from __future__ import annotations

from job_copilot.db import Tracker


def get_tracker() -> Tracker:
    return Tracker()
