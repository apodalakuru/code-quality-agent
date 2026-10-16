"""
Finding sources: a local JSON file (demo), SonarQube, or GitHub CodeQL code scanning.
All are normalised to the same shape so the agent does not care where a finding came from.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from base64 import b64encode
from pathlib import Path

SEVERITY_ORDER = ["INFO", "MINOR", "MAJOR", "CRITICAL", "BLOCKER"]
CODEQL_TO_SONAR = {"note": "MINOR", "warning": "MAJOR", "error": "CRITICAL",
                   "low": "MINOR", "medium": "MAJOR", "high": "CRITICAL", "critical": "BLOCKER"}


def _rank(sev: str) -> int:
    sev = (sev or "INFO").upper()
    return SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else 0


def _get_json(url: str, headers: dict) -> dict | list:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _from_file(repo_root: Path) -> list[dict]:
    return json.loads((repo_root / "findings.json").read_text(encoding="utf-8"))


def _from_sonar() -> list[dict]:
    base = os.environ["SONAR_URL"].rstrip("/")
    key = os.environ["SONAR_PROJECT_KEY"]
    token = os.environ["SONAR_TOKEN"]
    headers = {"Authorization": "Basic " + b64encode(f"{token}:".encode()).decode()}
    q = urllib.parse.urlencode({"componentKeys": key, "resolved": "false", "ps": 100,
                                "types": "BUG,VULNERABILITY,CODE_SMELL"})
    data = _get_json(f"{base}/api/issues/search?{q}", headers)
    rule_cache: dict[str, str] = {}
    out = []
    for i in data.get("issues", []):
        rule = i["rule"]
        if rule not in rule_cache:
            try:
                r = _get_json(f"{base}/api/rules/show?key={urllib.parse.quote(rule)}", headers)
                rule_cache[rule] = r.get("rule", {}).get("name", "")
            except Exception:
                rule_cache[rule] = ""
        out.append({
            "id": i["key"], "source": "sonar", "rule": rule, "severity": i.get("severity", "MAJOR"),
            "file": i["component"].split(":", 1)[-1], "line": i.get("line"),
            "message": i.get("message", ""), "rule_description": rule_cache[rule],
        })
    return out


def _from_codeql() -> list[dict]:
    repo = os.environ["GITHUB_REPO"]  # owner/name
    headers = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
               "Accept": "application/vnd.github+json"}
    alerts = _get_json(
        f"https://api.github.com/repos/{repo}/code-scanning/alerts?state=open&tool_name=CodeQL&per_page=100",
        headers)
    out = []
    for a in alerts:
        rule = a.get("rule", {})
        loc = a.get("most_recent_instance", {}).get("location", {})
        sev = rule.get("security_severity_level") or rule.get("severity") or "warning"
        out.append({
            "id": str(a["number"]), "source": "codeql", "rule": rule.get("id", ""),
            "severity": CODEQL_TO_SONAR.get(sev, "MAJOR"), "file": loc.get("path", ""),
            "line": loc.get("start_line"),
            "message": a.get("most_recent_instance", {}).get("message", {}).get("text", ""),
            "rule_description": rule.get("description", ""),
        })
    return out


def load_findings(source: str, repo_root: Path, max_results: int = 10, min_severity: str = "MAJOR") -> list[dict]:
    loaders = {"file": lambda: _from_file(repo_root), "sonar": _from_sonar, "codeql": _from_codeql}
    if source not in loaders:
        raise ValueError(f"Unknown findings source: {source}")
    findings = [f for f in loaders[source]()
                if f.get("file", "").endswith(".java") and _rank(f["severity"]) >= _rank(min_severity)]
    findings.sort(key=lambda f: _rank(f["severity"]), reverse=True)  # most severe first
    return findings[:max_results]
