"""
LangGraph workflow for the code-quality remediation agent.

  fetch_findings -> select_next -> start_branch -> read_context -> propose_fix -> apply_fix -> run_build
                         ^                                             ^                          |
                         |                                             +---- build failed, retry -+
                         |                                                                        |
                         |                                   build passed -> human_review (interrupt)
                         |                                                        |            |
                         +------------- finish <---- commit_and_pr <-- approved   |            |
                         |                 ^                                      |            |
                         |                 +---------- discard <------- rejected -+ / retries exhausted
                         +--> summarize -> END   (when no findings are left)

The LLM only proposes changes. Every side effect goes through a narrow MCP tool,
and nothing is committed or turned into a PR without explicit human approval.
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .config import settings
from .prompts import FIX_PROMPT, RETRY_BLOCK, SYSTEM_PROMPT
from .state import AgentState, FixProposal, FixResult


def _normalise(result: Any) -> Any:
    """MCP tool results may arrive as str, JSON str, or a list of content blocks."""
    if isinstance(result, list):
        result = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    return result


def _guess_test_path(src_path: str) -> str:
    return src_path.replace("src/main/java/", "src/test/java/").replace(".java", "Test.java")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:40]


def build_graph(tools: list, checkpointer=None):
    by_name = {t.name: t for t in tools}

    async def call(name: str, **kwargs) -> Any:
        return _normalise(await by_name[name].ainvoke(kwargs))

    llm = ChatAnthropic(model=settings.model, temperature=0, max_tokens=8000)
    fixer = llm.with_structured_output(FixProposal)

    def record(state: AgentState, status: str, detail: str) -> list[FixResult]:
        f = state["current"]
        return state.get("results", []) + [
            {"finding_id": f["id"], "rule": f["rule"], "file": f["file"], "status": status, "detail": detail}]

    # ------------------------------------------------------------- nodes
    async def fetch_findings(state: AgentState) -> dict:
        findings = await call("list_findings", source=settings.findings_source,
                              max_results=settings.max_findings, min_severity=settings.min_severity)
        print(f"Found {len(findings)} finding(s) to work on.")
        return {"findings": findings, "index": 0, "results": []}

    async def select_next(state: AgentState) -> dict:
        i, findings = state["index"], state["findings"]
        if i >= len(findings):
            return {"current": None}
        f = findings[i]
        print(f"\n[{i + 1}/{len(findings)}] {f['severity']} {f['rule']} in {f['file']}:{f['line']}")
        return {"current": f, "attempts": 0, "proposal": None, "build_output": "", "write_errors": [],
                "build_passed": False, "review": None}

    async def start_branch(state: AgentState) -> dict:
        f = state["current"]
        branch = f"ai-fix/{_slug(f['rule'])}-{_slug(f['id'])}"
        await call("git_start_branch", branch=branch, base=settings.base_branch)
        return {"branch": branch}

    async def read_context(state: AgentState) -> dict:
        src = state["current"]["file"]
        test = _guess_test_path(src)
        return {"context": {
            "source_file": src, "source_code": await call("read_file", path=src),
            "test_file": test, "test_code": await call("read_file", path=test) or "(no existing test file)"}}

    async def propose_fix(state: AgentState) -> dict:
        f, ctx, attempt = state["current"], state["context"], state.get("attempts", 0)
        retry = ""
        if attempt > 0 and state.get("proposal"):
            retry = RETRY_BLOCK.format(attempt=attempt, prev_explanation=state["proposal"]["explanation"],
                                       build_output=state.get("build_output", "")[-6000:])
        prompt = FIX_PROMPT.format(
            source=f["source"], rule=f["rule"], severity=f["severity"], file=f["file"], line=f["line"],
            message=f["message"], rule_description=f.get("rule_description", ""),
            source_code=ctx["source_code"], test_file=ctx["test_file"], test_code=ctx["test_code"],
            retry_block=retry)
        proposal: FixProposal = await fixer.ainvoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(prompt)])
        print(f"  attempt {attempt + 1}: {proposal.explanation[:120]}...")
        return {"proposal": proposal.model_dump(), "attempts": attempt + 1}

    async def apply_fix(state: AgentState) -> dict:
        # On a retry, reset to a clean branch so files from the failed attempt do not linger
        if state["attempts"] > 1:
            await call("git_discard", base=settings.base_branch)
            await call("git_start_branch", branch=state["branch"], base=settings.base_branch)
        errors = []
        for change in state["proposal"]["changes"]:
            try:
                await call("write_file", path=change["path"], content=change["content"])
            except Exception as e:  # e.g. the model tried to write outside the allowed folders
                errors.append(f"Write refused for {change['path']}: {e}")
        return {"write_errors": errors}

    async def run_build(state: AgentState) -> dict:
        if state.get("write_errors"):  # a write was refused: skip the build, feed the reason back
            return {"build_passed": False, "build_output": "\n".join(state["write_errors"])}
        result = await call("run_tests")
        print(f"  build {'PASSED' if result['passed'] else 'FAILED'}")
        return {"build_passed": result["passed"], "build_output": result["output_tail"]}

    async def human_review(state: AgentState) -> dict:
        diff = await call("git_diff")
        p, f = state["proposal"], state["current"]
        decision = interrupt({          # pauses the graph until a human resumes it
            "finding": f"{f['severity']} {f['rule']} in {f['file']}:{f['line']} - {f['message']}",
            "explanation": p["explanation"], "test_summary": p["test_summary"], "risk": p["risk"],
            "attempts": state["attempts"], "diff": diff})
        return {"review": decision}

    async def commit_and_pr(state: AgentState) -> dict:
        f, p = state["current"], state["proposal"]
        title = f"fix({f['source']}): {f['rule']} in {f['file'].rsplit('/', 1)[-1]}"
        body = (f"**Finding:** {f['severity']} `{f['rule']}` at `{f['file']}:{f['line']}`\n\n{f['message']}\n\n"
                f"**Fix:** {p['explanation']}\n\n**Test:** {p['test_summary']}\n\n**Risk:** {p['risk']}\n\n"
                f"**Reviewer note:** {state['review'].get('comment') or '-'}\n\n"
                "_Generated by the code-quality agent; approved by a human before this PR was opened._")
        await call("git_commit_and_push", message=title)
        url = await call("open_pull_request", title=title, body=body,
                         head=state["branch"], base=settings.base_branch)
        print(f"  {url}")
        return {"results": record(state, "pr_opened", str(url))}

    async def discard(state: AgentState) -> dict:
        await call("git_discard", base=settings.base_branch)
        if state.get("build_passed"):
            status, detail = "rejected", (state.get("review") or {}).get("comment", "rejected by reviewer")
        else:
            status, detail = "failed", f"build still failing after {state['attempts']} attempt(s)"
        print(f"  {status}: {detail}")
        return {"results": record(state, status, detail)}

    async def finish(state: AgentState) -> dict:
        return {"index": state["index"] + 1}

    async def summarize(state: AgentState) -> dict:
        print("\n===== Summary =====")
        for r in state.get("results", []):
            print(f"{r['status']:>10}  {r['rule']:<28} {r['file']}  {r['detail']}")
        return {}

    # ------------------------------------------------------------- routing
    def after_select(state: AgentState) -> str:
        return "start_branch" if state.get("current") else "summarize"

    def after_build(state: AgentState) -> str:
        if state["build_passed"]:
            return "human_review"
        return "propose_fix" if state["attempts"] < settings.max_attempts else "discard"

    def after_review(state: AgentState) -> str:
        return "commit_and_pr" if (state.get("review") or {}).get("approved") else "discard"

    # ------------------------------------------------------------- wiring
    g = StateGraph(AgentState)
    for name, fn in [("fetch_findings", fetch_findings), ("select_next", select_next),
                     ("start_branch", start_branch), ("read_context", read_context),
                     ("propose_fix", propose_fix), ("apply_fix", apply_fix), ("run_build", run_build),
                     ("human_review", human_review), ("commit_and_pr", commit_and_pr),
                     ("discard", discard), ("finish", finish), ("summarize", summarize)]:
        g.add_node(name, fn)

    g.add_edge(START, "fetch_findings")
    g.add_edge("fetch_findings", "select_next")
    g.add_conditional_edges("select_next", after_select, ["start_branch", "summarize"])
    g.add_edge("start_branch", "read_context")
    g.add_edge("read_context", "propose_fix")
    g.add_edge("propose_fix", "apply_fix")
    g.add_edge("apply_fix", "run_build")
    g.add_conditional_edges("run_build", after_build, ["human_review", "propose_fix", "discard"])
    g.add_conditional_edges("human_review", after_review, ["commit_and_pr", "discard"])
    g.add_edge("commit_and_pr", "finish")
    g.add_edge("discard", "finish")
    g.add_edge("finish", "select_next")
    g.add_edge("summarize", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())  # checkpointer is required for interrupt()
