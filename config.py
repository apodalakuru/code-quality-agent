import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    repo_root: str = field(default_factory=lambda: os.environ.get("REPO_ROOT", "demo-java-app"))
    base_branch: str = field(default_factory=lambda: os.environ.get("BASE_BRANCH", "main"))
    findings_source: str = field(default_factory=lambda: os.environ.get("FINDINGS_SOURCE", "file"))
    min_severity: str = field(default_factory=lambda: os.environ.get("MIN_SEVERITY", "MAJOR"))
    max_findings: int = field(default_factory=lambda: int(os.environ.get("MAX_FINDINGS", "5")))
    max_attempts: int = field(default_factory=lambda: int(os.environ.get("MAX_ATTEMPTS", "3")))
    model: str = field(default_factory=lambda: os.environ.get("MODEL", "claude-sonnet-5-5"))
    dry_run: bool = field(default_factory=lambda: os.environ.get("DRY_RUN", "true").lower() == "true")


settings = Settings()
