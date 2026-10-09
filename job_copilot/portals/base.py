"""Common portal interface."""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod

from job_copilot.models import JobPosting, JobStatus, PortalSettings


class PortalBlocked(RuntimeError):
    """The portal served a captcha / access-denied page."""


class Portal(ABC):
    name: str

    def __init__(self, settings: PortalSettings | None = None):
        self.settings = settings or PortalSettings()

    async def __aenter__(self) -> "Portal":
        await self.open()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def open(self) -> None:  # noqa: B027 - optional hook
        pass

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

    async def pause(self) -> None:
        """Human-like delay between page loads."""
        await asyncio.sleep(random.uniform(self.settings.min_delay_seconds, self.settings.max_delay_seconds))

    @abstractmethod
    async def search(self, role: str, location: str, max_age_days: int, experience_years: float) -> list[JobPosting]:
        """Return job cards (no full description yet)."""

    @abstractmethod
    async def fetch_details(self, posting: JobPosting) -> JobPosting:
        """Open the job page and fill in description / posted date / recruiter contact."""

    @abstractmethod
    async def apply(self, url: str) -> tuple[JobStatus, str]:
        """Try to apply. Returns (APPLIED | NEEDS_MANUAL | FAILED, reason)."""


def get_portal(name: str, settings: PortalSettings | None = None) -> Portal:
    if name == "naukri":
        from job_copilot.portals.naukri import NaukriPortal
        return NaukriPortal(settings)
    if name == "sample":
        from job_copilot.portals.sample import SamplePortal
        return SamplePortal(settings)
    raise ValueError(f"Unknown portal '{name}'. Available: naukri, sample")
