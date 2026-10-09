"""Start the API: `python -m backend.run` (from the repo root).

Not `uvicorn --reload`: on Windows Playwright needs the Proactor event loop to spawn browsers,
and the reloader's child process may pick the selector loop instead.
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, loop="asyncio")


if __name__ == "__main__":
    main()
