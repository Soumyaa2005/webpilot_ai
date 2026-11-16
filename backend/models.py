"""Pydantic models: intent, plan, extracted-data schema, API request/response."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PAGES_LIMIT = 3  # MVP hard cap

TaskType = Literal["website_crawl", "extract_info", "headings_links",
                   "structured_json", "contact_info", "compare_pages"]
FieldName = Literal["title", "description", "headings", "paragraphs", "links", "contacts"]
StepName = Literal["validate_url", "discover_pages", "extract_content",
                   "process_data", "validate_json", "generate_summary"]
STEP_ORDER: list[str] = ["validate_url", "discover_pages", "extract_content",
                         "process_data", "validate_json", "generate_summary"]
STEP_LABELS = {
    "validate_url": "URL validation",
    "discover_pages": "Website discovery",
    "extract_content": "Content extraction",
    "process_data": "Data processing",
    "validate_json": "JSON validation",
    "generate_summary": "Response generation",
}


class Intent(BaseModel):
    task_type: TaskType = "website_crawl"
    url: str
    fields: list[FieldName] = Field(default_factory=lambda: ["title", "description", "headings", "paragraphs", "links"])
    summary_required: bool = True
    json_required: bool = True
    max_pages: int = MAX_PAGES_LIMIT
    expected_output: str = ""
    source: Literal["groq", "rules"] = "rules"
    notes: list[str] = Field(default_factory=list)

    @field_validator("max_pages", mode="before")
    @classmethod
    def clamp_pages(cls, v: Any) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return MAX_PAGES_LIMIT
        return max(1, min(v, MAX_PAGES_LIMIT))


class AIIntentOutput(BaseModel):
    """Schema the LLM output must satisfy.

    Unknown keys are ignored (never read, never executed). Unknown field names are
    dropped. Unknown task types, bad step names, wrong types or a missing URL are
    rejected and the deterministic rules are used instead.
    """
    model_config = ConfigDict(extra="ignore")
    task_type: TaskType
    url: str
    fields: list[FieldName] = Field(default_factory=list)
    summary_required: bool = True
    json_required: bool = True
    max_pages: int = MAX_PAGES_LIMIT
    expected_output: str = ""
    steps: Optional[list[StepName]] = None

    @field_validator("fields", mode="before")
    @classmethod
    def keep_known_fields(cls, v):
        allowed = {"title", "description", "headings", "paragraphs", "links", "contacts"}
        if not isinstance(v, list):
            return []
        return [f for f in v if isinstance(f, str) and f in allowed]

    @field_validator("steps")
    @classmethod
    def steps_in_order(cls, v):
        if v is None:
            return v
        idx = [STEP_ORDER.index(s) for s in v]
        if idx != sorted(idx) or len(set(idx)) != len(idx):
            raise ValueError("steps must be unique and in canonical order")
        return v


class TaskPlan(BaseModel):
    task_type: TaskType
    url: str
    steps: list[StepName]
    output_format: list[Literal["summary", "json"]]
    max_pages: int = Field(ge=1, le=MAX_PAGES_LIMIT)
    fields: list[FieldName] = Field(default_factory=list)


# ---- extracted data schema (used by data_processor output + json_validator) ----
class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")


class Heading(_Strict):
    level: int = Field(ge=1, le=6)
    text: str = Field(min_length=1)
    source: str


class Paragraph(_Strict):
    text: str = Field(min_length=1)
    source: str


class Link(_Strict):
    text: str
    url: str
    source: str


class ExtractedData(_Strict):
    url: str
    title: str
    meta_description: str
    headings: list[Heading]
    paragraphs: list[Paragraph]
    internal_links: list[Link]
    pages_crawled: int = Field(ge=0)
    sources: list[str]
    summary: Optional[str] = None


# ---- API models ----
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    wait: bool = True  # False -> return immediately and poll /api/tasks/{id}


class ToolRun(BaseModel):
    tool: str
    step: str
    attempt: int = 1
    status: Literal["running", "completed", "failed", "timeout"]
    exit_code: Optional[int] = None
    duration_ms: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    command: str = ""
    started_at: str = ""


class StepState(BaseModel):
    id: str
    label: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    duration_ms: Optional[int] = None
    detail: str = ""


class ChatResponse(BaseModel):
    task_id: str
    status: Literal["pending", "running", "completed", "failed", "partial_success"]
    message: str = ""
    intent: dict = Field(default_factory=dict)
    execution_plan: list[str] = Field(default_factory=list)
    execution_steps: list[StepState] = Field(default_factory=list)
    cli_results: list[ToolRun] = Field(default_factory=list)
    summary: str = ""
    extracted_data: dict = Field(default_factory=dict)
    json_valid: bool = False
    ai_interpretation: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    duration_ms: Optional[int] = None
    created_at: str = ""
