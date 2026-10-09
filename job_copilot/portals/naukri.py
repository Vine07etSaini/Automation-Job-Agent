"""Naukri.com via Playwright with a persistent, logged-in browser profile.

Run `python main.py login naukri` once to sign in manually; the session is reused afterwards.
Naukri changes its markup often, so selectors are kept in one place with fallbacks.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlencode

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import TimeoutError as PWTimeout

from job_copilot.agents.freshness import parse_posted
from job_copilot.config import BROWSER_DIR
from job_copilot.models import JobPosting, JobStatus
from job_copilot.portals.base import Portal, PortalBlocked

log = logging.getLogger(__name__)

BASE = "https://www.naukri.com"
LOGIN_URL = f"{BASE}/nlogin/login"

CARDS_JS = """
() => Array.from(document.querySelectorAll('.srp-jobtuple-wrapper, article.jobTuple, .cust-job-tuple'))
  .map(el => {
    const txt = s => (el.querySelector(s)?.innerText || '').trim();
    const a = el.querySelector('a.title');
    return {
      id: el.getAttribute('data-job-id') || '',
      title: a ? a.innerText.trim() : '',
      url: a ? a.href : '',
      company: txt('a.comp-name, .comp-name'),
      exp: txt('.expwdth, .exp-wrap, .exp'),
      salary: txt('.sal-wrap, .sal'),
      location: txt('.locWdth, .loc-wrap, .loc'),
      posted: txt('.job-post-day, [class*="post-day"], .postedDate'),
      tags: Array.from(el.querySelectorAll('ul.tags-gt li, .tags li')).map(li => li.innerText.trim()).filter(Boolean),
    };
  })
"""

DETAILS_JS = """
() => {
  const pick = sels => {
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el && el.innerText.trim().length > 50) return el.innerText.trim();
    }
    return '';
  };
  const stats = Array.from(document.querySelectorAll('[class*="jhc__stat"], [class*="stat"]'))
    .map(e => e.innerText.trim());
  const posted = (stats.find(t => /^posted/i.test(t)) || '').replace(/^posted\\s*:?\\s*/i, '');
  const skills = Array.from(document.querySelectorAll('[class*="key-skill"] a, [class*="key-skill"] span'))
    .map(e => e.innerText.trim()).filter(Boolean);
  return {
    description: pick(['[class*="dang-inner-html"]', '[class*="job-desc"]', 'section.job-desc', '[class*="JDC"]']),
    posted, skills,
  };
}
"""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def search_url(role: str, location: str, page: int, max_age_days: int, experience_years: float) -> str:
    suffix = f"-{page}" if page > 1 else ""
    params = {"jobAge": max_age_days, "experience": int(experience_years)}
    if not location or location.lower() == "remote":
        if location:
            params["wfhType"] = 2
        path = f"{_slug(role)}-jobs{suffix}"
    else:
        path = f"{_slug(role)}-jobs-in-{_slug(location)}{suffix}"
    return f"{BASE}/{path}?{urlencode(params)}"


class NaukriPortal(Portal):
    name = "naukri"

    async def open(self) -> None:
        BROWSER_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self.ctx: BrowserContext = await self._pw.chromium.launch_persistent_context(
            str(BROWSER_DIR / "naukri"),
            headless=self.settings.headless,
            viewport={"width": 1366, "height": 860},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        self.page: Page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()

    async def close(self) -> None:
        if getattr(self, "ctx", None):
            await self.ctx.close()
        if getattr(self, "_pw", None):
            await self._pw.stop()

    async def _goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        title = (await self.page.title() or "").lower()
        if "access denied" in title or "captcha" in title:
            raise PortalBlocked(f"Naukri blocked the request ({title}). Try headless: false and lower volume.")

    # ------------------------------------------------------------ search

    async def search(self, role: str, location: str, max_age_days: int, experience_years: float) -> list[JobPosting]:
        results: list[JobPosting] = []
        for page_no in range(1, self.settings.pages_per_search + 1):
            url = search_url(role, location, page_no, max_age_days, experience_years)
            log.info("Naukri search: %s", url)
            await self._goto(url)
            try:
                await self.page.wait_for_selector(".srp-jobtuple-wrapper, article.jobTuple, .cust-job-tuple", timeout=15000)
            except PWTimeout:
                log.info("No job cards on %s", url)
                break
            cards = await self.page.evaluate(CARDS_JS)
            for c in cards:
                if not c["url"] or not c["title"]:
                    continue
                ext_id = c["id"] or (m.group(1) if (m := re.search(r"-(\d{6,})(?:[?#]|$)", c["url"])) else c["url"])
                results.append(JobPosting(
                    portal=self.name, external_id=ext_id, title=c["title"], company=c["company"] or "Unknown",
                    url=c["url"].split("?")[0], location=c["location"], experience_text=c["exp"],
                    salary_text=c["salary"], posted_text=c["posted"], posted_at=parse_posted(c["posted"]),
                    tags=c["tags"],
                ))
            if len(cards) < 15:
                break
            await self.pause()
        return results

    async def fetch_details(self, posting: JobPosting) -> JobPosting:
        await self._goto(posting.url)
        try:
            await self.page.wait_for_selector('[class*="dang-inner-html"], [class*="job-desc"]', timeout=15000)
        except PWTimeout:
            log.warning("Description not found for %s", posting.url)
        d = await self.page.evaluate(DETAILS_JS)
        updated = posting.model_copy()
        updated.description = d["description"] or posting.description
        if d["posted"]:
            # second freshness check: the job page's own date wins over the search card
            updated.posted_text = d["posted"]
            updated.posted_at = parse_posted(d["posted"]) or posting.posted_at
        if d["skills"]:
            updated.tags = list(dict.fromkeys([*posting.tags, *d["skills"]]))
        await self.pause()
        return updated

    # ------------------------------------------------------------ apply

    async def _visible(self, selector: str) -> bool:
        try:
            return await self.page.locator(selector).first.is_visible(timeout=1500)
        except PWTimeout:
            return False

    async def apply(self, url: str) -> tuple[JobStatus, str]:
        await self._goto(url)
        await self.page.wait_for_timeout(2500)
        if await self._visible("text=/already applied/i"):
            return JobStatus.APPLIED, "already applied"
        if await self._visible("#company-site-button, button:has-text('Apply on company site')"):
            return JobStatus.NEEDS_MANUAL, "applies on company site"
        if await self._visible("#login-apply-button, button:has-text('Login to apply')"):
            return JobStatus.NEEDS_MANUAL, "not logged in - run: python main.py login naukri"
        button = self.page.locator("#apply-button, button.apply-button, button:has-text('Apply')").first
        try:
            await button.click(timeout=5000)
        except PWTimeout:
            return JobStatus.NEEDS_MANUAL, "apply button not found"
        await self.page.wait_for_timeout(4000)
        if await self._visible("[class*='chatbot'], [class*='Chatbot']"):
            return JobStatus.NEEDS_MANUAL, "screening questions - finish in the browser"
        if await self._visible("text=/successfully applied|applied to|you have applied/i"):
            await self.pause()
            return JobStatus.APPLIED, "applied on Naukri"
        return JobStatus.NEEDS_MANUAL, "could not confirm the application - please check"


async def interactive_login() -> None:
    """Open a visible browser so the user can sign in; the session is saved in storage/browser."""
    from job_copilot.models import PortalSettings

    async with NaukriPortal(PortalSettings(headless=False)) as portal:
        await portal.page.goto(LOGIN_URL)
        await asyncio.to_thread(input, "Log in to Naukri in the opened browser window, then press Enter here... ")
