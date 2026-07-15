# models/findings.py

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class FindingCategory(str, Enum):
    SECURITY = "SECURITY"
    ARCHITECTURE = "ARCHITECTURE"
    TEST_COVERAGE = "TEST_COVERAGE"
    DOCUMENTATION = "DOCUMENTATION"


class CodeLocation(BaseModel):
    file: str
    line_start: int
    line_end: int
    snippet: str = Field(default="", description="The relevant code snippet, for display in dashboard")


class Finding(BaseModel):
    category: FindingCategory
    severity: Severity
    title: str
    description: str
    location: CodeLocation
    suggestion: Optional[str] = Field(default=None, description="Suggested fix, if any")
    references: list[str] = Field(default_factory=list, description="e.g. OWASP links, docs")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # Used later for dedup (gap #8) — function name + category + normalized
    # snippet, so a finding survives line-number churn across commits.
    def dedup_key(self) -> str:
        import hashlib
        normalized = "".join(self.location.snippet.split())  # strip whitespace
        snippet_hash = hashlib.sha256(normalized.encode()).hexdigest()[:12]
        return f"{self.location.file}:{self.category}:{snippet_hash}"


class AgentReport(BaseModel):
    agent_name: str
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    runtime_ms: int = 0
    error: Optional[str] = Field(default=None, description="Populated if agent failed/rate-limited")


class ReviewReport(BaseModel):
    pr_number: int
    repo: str
    commit_sha: str
    agent_reports: list[AgentReport] = Field(default_factory=list)
    all_findings: list[Finding] = Field(default_factory=list)  # merged, deduplicated, ranked
    critical_count: int = 0
    high_count: int = 0
    hitl_required: bool = False
    synthesized_review: str = ""

    # Status field for gap #1 — durable job tracking.
    # "pending" | "running" | "complete" | "failed"
    status: str = "pending"

    def recompute_counts(self) -> None:
        self.critical_count = sum(1 for f in self.all_findings if f.severity == Severity.CRITICAL)
        self.high_count = sum(1 for f in self.all_findings if f.severity == Severity.HIGH)
        self.hitl_required = self.critical_count > 0