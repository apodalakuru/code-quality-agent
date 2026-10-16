from __future__ import annotations

from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class Finding(TypedDict):
    id: str
    source: str
    rule: str
    severity: str
    file: str
    line: Optional[int]
    message: str
    rule_description: str


class FileChange(BaseModel):
    path: str = Field(description="Repo-relative path, e.g. src/main/java/com/acme/Foo.java")
    content: str = Field(description="The COMPLETE new content of the file, not a diff")


class FixProposal(BaseModel):
    """Structured output the LLM must return for each finding."""
    explanation: str = Field(description="2-4 sentences: what was wrong and how the change fixes it")
    changes: list[FileChange] = Field(description="Source and test files to write (full contents)")
    test_summary: str = Field(description="Which JUnit test proves the fix and what it asserts")
    risk: Literal["low", "medium", "high"] = Field(description="Behavioural risk of the change")


class FixResult(TypedDict):
    finding_id: str
    rule: str
    file: str
    status: Literal["pr_opened", "rejected", "failed"]
    detail: str


class AgentState(TypedDict, total=False):
    findings: list[Finding]
    index: int                      # position of the current finding in `findings`
    current: Optional[Finding]
    branch: str
    context: dict                   # {source_file, source_code, test_file, test_code}
    proposal: Optional[dict]        # FixProposal.model_dump()
    attempts: int
    write_errors: list[str]         # file writes refused by the MCP server's guardrails
    build_output: str               # last failing build output, fed back to the model
    build_passed: bool
    review: Optional[dict]          # {"approved": bool, "comment": str}
    results: list[FixResult]
