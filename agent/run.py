"""
Command-line entry point.

    python -m agent.run                 # interactive: review each fix in the terminal
    python -m agent.run --auto-reject   # dry demo: run fixes + builds, approve nothing

The agent pauses at every human_review interrupt and resumes with your decision.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.types import Command

from .config import settings
from .graph import build_graph

ROOT = Path(__file__).resolve().parent.parent


def mcp_client() -> MultiServerMCPClient:
    env = {**os.environ, "REPO_ROOT": str((ROOT / settings.repo_root).resolve()
                                          if not Path(settings.repo_root).is_absolute()
                                          else settings.repo_root)}
    return MultiServerMCPClient({
        "quality": {
            "command": sys.executable,
            "args": [str(ROOT / "mcp_server" / "server.py")],
            "transport": "stdio",
            "env": env,
        }
    })


def ask_reviewer(payload: dict, auto_reject: bool) -> dict:
    print("\n" + "=" * 78)
    print(f"REVIEW  {payload['finding']}")
    print(f"Risk: {payload['risk']}   Attempts: {payload['attempts']}")
    print(f"\nFix:  {payload['explanation']}\nTest: {payload['test_summary']}\n")
    print(payload["diff"] or "(empty diff)")
    print("=" * 78)
    if auto_reject:
        return {"approved": False, "comment": "auto-reject demo mode"}
    answer = input("Approve and open PR? [y/N] ").strip().lower()
    comment = input("Comment for the PR / rejection reason (optional): ").strip()
    return {"approved": answer == "y", "comment": comment}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-reject", action="store_true", help="never approve (safe demo run)")
    args = parser.parse_args()

    tools = await mcp_client().get_tools()
    graph = build_graph(tools)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 300}

    print(f"Model: {settings.model} | Source: {settings.findings_source} | DRY_RUN: {settings.dry_run}")
    next_input: object = {}
    while True:
        await graph.ainvoke(next_input, config)
        snapshot = await graph.aget_state(config)
        pending = [i for task in snapshot.tasks for i in task.interrupts]
        if not pending:
            break  # graph reached END
        decision = ask_reviewer(pending[0].value, args.auto_reject)
        next_input = Command(resume=decision)


if __name__ == "__main__":
    asyncio.run(main())
