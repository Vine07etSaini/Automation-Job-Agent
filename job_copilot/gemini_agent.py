"""Gemini agent that uses the Job Copilot MCP server as its toolbox.

Gemini receives the MCP ClientSession as a tool; the google-genai SDK lists the server's
tools, sends them to Gemini and runs the function calls it asks for automatically.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.markdown import Markdown

from job_copilot.config import IST, ROOT
from job_copilot.llm.gemini import make_client, model_name

SYSTEM = """You are the user's AI Job Copilot. You help them find jobs, review matches and apply.
Rules:
- Use the tools to answer; never make up jobs, scores or profile facts.
- Never invent or exaggerate skills or experience. Experience and education are read-only.
- Only approve, reject or apply when the user clearly asks you to. Confirm what you did.
- Keep answers short; use tables or bullet lists for job lists. Include job ids so the user can refer to them.
Today is {today} (IST)."""


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "job_copilot.mcp_server"],
        env=dict(os.environ),
        cwd=str(ROOT),
    )


def _config(session: ClientSession) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM.format(today=datetime.now(IST).strftime("%A, %d %b %Y")),
        temperature=0.3,
        tools=[session],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=25),
    )


async def ask(question: str) -> str:
    """One-shot question to the agent."""
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            client = make_client()
            resp = await client.aio.models.generate_content(model=model_name(), contents=question,
                                                            config=_config(session))
            return resp.text or ""


async def chat() -> None:
    """Interactive multi-turn chat in the terminal."""
    console = Console()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            client = make_client()
            conversation = client.aio.chats.create(model=model_name(), config=_config(session))
            console.print(f"[bold]Job Copilot[/] - Gemini [cyan]{model_name()}[/] with {len(tools)} MCP tools. "
                          "Type 'exit' to quit.\n")
            while True:
                try:
                    msg = (await asyncio.to_thread(console.input, "[bold green]you>[/] ")).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if msg.lower() in {"exit", "quit", "q"}:
                    break
                if not msg:
                    continue
                with console.status("thinking..."):
                    resp = await conversation.send_message(msg)
                console.print(Markdown(resp.text or "(no response)"))
                console.print()
