"""
MCP server exposing code-quality tools to the agent.

Every tool is deliberately narrow:
  * file access is confined to REPO_ROOT and to source/test folders
  * the only command that can run is the Maven test build
  * git can only create ai-fix/* branches, never touch the base branch directly
  * push / PR creation is skipped entirely when DRY_RUN=true

Run standalone for debugging:   python mcp_server/server.py
(The agent normally launches it over stdio via langchain-mcp-adapters.)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from sources import load_findings  # same folder

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "demo-java-app")).resolve()
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
MAVEN_CMD = os.environ.get("MAVEN_CMD", "mvn")
BUILD_TIMEOUT_SEC = int(os.environ.get("BUILD_TIMEOUT_SEC", "900"))

WRITABLE_PREFIXES = ("src/main/java/", "src/test/java/")
BRANCH_PATTERN = re.compile(r"^ai-fix/[A-Za-z0-9._\-/]{1,80}$")
MAX_FILE_BYTES = 200_000

mcp = FastMCP("code-quality-tools")


# ---------------------------------------------------------------- helpers
def _safe_path(rel_path: str) -> Path:
    """Resolve a repo-relative path and refuse anything outside REPO_ROOT."""
    p = (REPO_ROOT / rel_path).resolve()
    if p != REPO_ROOT and REPO_ROOT not in p.parents:
        raise ValueError(f"Path escapes repository: {rel_path}")
    return p


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    # shell=False and a fixed argument list: the model never builds a command string
    return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)


def _git(*args: str) -> str:
    r = _run(["git", *args])
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


# ---------------------------------------------------------------- findings
@mcp.tool()
def list_findings(source: str = "file", max_results: int = 10, min_severity: str = "MAJOR") -> str:
    """List open code-quality / security findings.

    source: 'file' (findings.json in the repo), 'sonar' (SonarQube API) or 'codeql' (GitHub code scanning).
    Returns a JSON array of {id, source, rule, severity, file, line, message, rule_description}.
    """
    findings = load_findings(source, REPO_ROOT, max_results=max_results, min_severity=min_severity)
    return json.dumps(findings)


# ---------------------------------------------------------------- files
@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 file from the repository (path relative to repo root). Returns '' if it does not exist."""
    p = _safe_path(path)
    if not p.exists():
        return ""
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"File too large to read safely: {path}")
    return p.read_text(encoding="utf-8")


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write a Java source or test file. Only src/main/java and src/test/java .java files are allowed."""
    p = _safe_path(path.replace("\\", "/"))
    norm = p.relative_to(REPO_ROOT).as_posix()  # check the RESOLVED path, so '..' tricks cannot bypass it
    if not norm.startswith(WRITABLE_PREFIXES) or not norm.endswith(".java"):
        raise ValueError(f"Writing is only allowed for .java files under {WRITABLE_PREFIXES}: {path}")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Content too large")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {norm} ({len(content)} chars)"


# ---------------------------------------------------------------- build
@mcp.tool()
def run_tests() -> str:
    """Run the Maven build with unit tests. Returns JSON {passed, exit_code, output_tail}."""
    try:
        r = _run([MAVEN_CMD, "-B", "-q", "test"], timeout=BUILD_TIMEOUT_SEC)
        output = (r.stdout + "\n" + r.stderr).strip()
        tail = "\n".join(output.splitlines()[-80:])
        return json.dumps({"passed": r.returncode == 0, "exit_code": r.returncode, "output_tail": tail})
    except subprocess.TimeoutExpired:
        return json.dumps({"passed": False, "exit_code": -1, "output_tail": "Build timed out"})


# ---------------------------------------------------------------- git
@mcp.tool()
def git_start_branch(branch: str, base: str) -> str:
    """Create a fresh ai-fix/* branch from the base branch. Fails if the working tree is dirty."""
    if not BRANCH_PATTERN.match(branch):
        raise ValueError("Branch must match ai-fix/<name>")
    if _git("status", "--porcelain"):
        raise RuntimeError("Working tree is not clean; refusing to start a new fix")
    _git("checkout", base)
    _git("checkout", "-B", branch)
    return f"on branch {branch} (from {base})"


@mcp.tool()
def git_diff() -> str:
    """Show the uncommitted diff, including new files, for human review."""
    _git("add", "-N", ".")  # intent-to-add so new test files appear in the diff
    return _git("diff")


@mcp.tool()
def git_discard(base: str) -> str:
    """Throw away all uncommitted changes and return to the base branch."""
    _git("reset", "--hard")
    _git("clean", "-fd", "--", "src")
    _git("checkout", base)
    return f"discarded changes, back on {base}"


@mcp.tool()
def git_commit_and_push(message: str) -> str:
    """Commit all changes on the current ai-fix/* branch and push it (skipped push in DRY_RUN)."""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not BRANCH_PATTERN.match(branch):
        raise RuntimeError(f"Refusing to commit on non ai-fix branch: {branch}")
    _git("add", "-A", "src")
    _git("commit", "-m", message)
    if DRY_RUN:
        return f"committed on {branch} (DRY_RUN: not pushed)"
    _git("push", "-u", "origin", branch)
    return f"committed and pushed {branch}"


@mcp.tool()
def open_pull_request(title: str, body: str, head: str, base: str) -> str:
    """Open a pull request with the GitHub CLI (gh). Returns the PR URL. Skipped in DRY_RUN."""
    if not BRANCH_PATTERN.match(head):
        raise ValueError("PR head must be an ai-fix/* branch")
    if DRY_RUN:
        return f"DRY_RUN: would open PR '{title}' {head} -> {base}"
    r = _run(["gh", "pr", "create", "--title", title, "--body", body,
              "--head", head, "--base", base, "--label", "ai-generated"])
    if r.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {r.stderr.strip()}")
    return r.stdout.strip()


if __name__ == "__main__":
    mcp.run()  # stdio transport
