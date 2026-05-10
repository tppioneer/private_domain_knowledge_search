from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class KnowledgeType(str, Enum):
    TERM = "term"
    API = "api"
    DOCUMENT = "document"
    BEST_PRACTICE = "best_practice"
    DEFECT_HISTORY = "defect_history"
    SECURITY_RULE = "security_rule"
    TEST_TEMPLATE = "test_template"
    SPEC = "spec"


class FeedbackAction(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
    IGNORED = "ignored"


class EntityType(str, Enum):
    TERM = "term"
    API = "api"


# ── context 对象 ──

class Dependency(BaseModel):
    name: str
    version: str


class Context(BaseModel):
    file_path: Optional[str] = None
    language: Optional[str] = None
    framework: Optional[str] = None
    dependencies: list[Dependency] = Field(default_factory=list)
    current_code_snippet: Optional[str] = None
    cursor_line: Optional[int] = None
    adjacent_comments: list[str] = Field(default_factory=list)
    module: Optional[str] = None


# ── search_private_knowledge 返回 ──

class KnowledgeMeta(BaseModel):
    sdk_class: Optional[str] = None
    method: Optional[str] = None
    since_version: Optional[str] = None
    deprecated_in: Optional[str] = None
    required_config: list[str] = Field(default_factory=list)
    code_example: Optional[str] = None
    related_entity: Optional[str] = None
    security_rule: Optional[str] = None
    sdk: Optional[str] = None
    version: Optional[str] = None
    config_required: Optional[str] = None
    source: Optional[str] = None
    applicable_version: Optional[str] = None
    related_ticket: Optional[str] = None


class KnowledgeItem(BaseModel):
    id: str
    type: KnowledgeType
    content: str
    score: float
    meta: KnowledgeMeta = Field(default_factory=KnowledgeMeta)


class Diagnostics(BaseModel):
    total_scanned: int = 0
    time_ms: int = 0
    warnings: list[str] = Field(default_factory=list)


class SearchKnowledgeResponse(BaseModel):
    items: list[KnowledgeItem] = Field(default_factory=list)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
    assembled_prompt: AssemblePromptOutput | None = None


# ── get_entity_detail 返回 ──

class VersionChange(BaseModel):
    version: str
    change: str


class EntityDefinition(BaseModel):
    signature: Optional[str] = None
    parameters: list[dict] = Field(default_factory=list)
    return_type: Optional[str] = None
    exceptions: list[str] = Field(default_factory=list)
    since_version: Optional[str] = None
    deprecated_in: Optional[str] = None
    code_example: Optional[str] = None
    config_requirements: Optional[str] = None
    definition_text: Optional[str] = None
    context_: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    related_terms: list[str] = Field(default_factory=list)


class EntityDetailResponse(BaseModel):
    entity_name: str
    entity_type: EntityType
    found: bool = True
    message: Optional[str] = None
    definition: EntityDefinition = Field(default_factory=EntityDefinition)
    related_specs: list[str] = Field(default_factory=list)
    version_changelog: list[VersionChange] = Field(default_factory=list)


# ── get_applicable_spec 返回 ──

class SpecItem(BaseModel):
    id: str
    rule: str
    category: str
    effective_condition: Optional[str] = None
    positive_example: Optional[str] = None
    negative_example: Optional[str] = None
    conflicts: list[str] = Field(default_factory=list)


class ConflictWarning(BaseModel):
    message: str


class SpecResponse(BaseModel):
    specs: list[SpecItem] = Field(default_factory=list)
    conflict_warnings: list[str] = Field(default_factory=list)


# ── recommend_context 参数 ──

class ProjectMeta(BaseModel):
    project_id: str = "unknown"
    team: str = "unknown"
    tech_stack: list[str] = Field(default_factory=list)
    core_dependencies: list[Dependency] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)


class CommonApi(BaseModel):
    name: str
    snippet: str


class RecentSpecUpdate(BaseModel):
    rule: str
    updated_at: str


class PinnedKnowledge(BaseModel):
    architecture_overview: Optional[str] = None
    common_apis: list[CommonApi] = Field(default_factory=list)
    recent_spec_updates: list[RecentSpecUpdate] = Field(default_factory=list)
    security_whitelist_patterns: list[str] = Field(default_factory=list)


class RecommendContextResponse(BaseModel):
    pinned_knowledge: PinnedKnowledge = Field(default_factory=PinnedKnowledge)


# ── report_feedback ──

class ModificationDetail(BaseModel):
    original_generated_code: Optional[str] = None
    final_accepted_code: Optional[str] = None
    rejection_reason: Optional[str] = None


class ReportFeedbackResponse(BaseModel):
    status: str = "recorded"
    feedback_id: str


# ── 审计日志 ──

class AuditRecord(BaseModel):
    tool_name: str
    caller_team: Optional[str] = None
    caller_project: Optional[str] = None
    query_params: dict = Field(default_factory=dict)
    result_ids: list[str] = Field(default_factory=list)
    time_ms: int = 0


# ── assemble_prompt ──

class AutoAssembleConfig(BaseModel):
    enabled: bool = False
    include_specs: bool = True
    max_tokens: int = 4096
    role_hint: str = ""


class AssemblePromptInput(BaseModel):
    user_query: str
    context: dict | None = None
    search_items: list[dict] = Field(default_factory=list)
    specs: list[dict] = Field(default_factory=list)
    pinned_knowledge: dict | None = None
    max_tokens: int = 4096
    role_hint: str = ""


class PromptSections(BaseModel):
    system: str = ""
    background: str = ""
    user_request: str = ""
    constraints: str = ""


class SanitizationEntry(BaseModel):
    item_id: str
    original_hash: str
    action: str  # "cleaned" | "removed"


class AssembleStats(BaseModel):
    input_items: int = 0
    after_dedup: int = 0
    after_truncation: int = 0
    estimated_tokens: int = 0
    budget_remaining: int = 0


class AssemblePromptOutput(BaseModel):
    assembled_prompt: str = ""
    sections: PromptSections = Field(default_factory=PromptSections)
    stats: AssembleStats = Field(default_factory=AssembleStats)
    sanitization_log: list[SanitizationEntry] = Field(default_factory=list)
