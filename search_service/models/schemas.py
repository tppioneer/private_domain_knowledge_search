"""Search Service 数据模型 —— 与 MCP Server 的 mcp/models/schemas.py 保持一致。

抽离自 MCP models，添加健康检查等 Search Service 专用模型。
"""

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


# ── context ──

class Dependency(BaseModel):
    name: str
    version: str


class SearchContext(BaseModel):
    file_path: Optional[str] = None
    language: Optional[str] = None
    framework: Optional[str] = None
    dependencies: list[Dependency] = Field(default_factory=list)
    current_code_snippet: Optional[str] = None
    cursor_line: Optional[int] = None
    adjacent_comments: list[str] = Field(default_factory=list)
    module: Optional[str] = None


# ── search ──

class SuggestedAlternative(BaseModel):
    """高层替代 API 的推荐信息。"""
    api: str = ""                          # 替代方法签名，如 "FileQueryManager.getShardFileService(tenantId)"
    entry_method: str = ""                 # 入口方法，如 "FileQueryManager.getShardFileService(tenantId)"
    full_call_chain: str = ""              # 完整调用链，如 "FileQueryManager.getShardFileService(tenantId).getFileSummary(fileId)"
    description: str = ""                  # 说明，如 "通过 FileQueryManager 获取实例，自动注入租户上下文"


class LayeredRecommendation(BaseModel):
    """分层推荐项 —— 用于顶层 layered_recommendations 列表。"""
    layer: str = "high"                    # high | mid | low
    score: float = 0.0
    content: str = ""                      # 推荐描述文本
    description: str = ""
    full_call_chain: str = ""              # 完整调用路径
    entry_method: str = ""                 # 入口方法


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
    knowledge_source: Optional[str] = None
    role: Optional[str] = None
    # ── 层级标注（方案一） ──
    layer: Optional[str] = None            # high | mid | low
    requires_context_building: bool = False
    context_dependencies: list[str] = Field(default_factory=list)
    standard_context_provider: Optional[str] = None  # 如 "ContextManager.getCurrentTenantId()"
    suggested_alternative: Optional[SuggestedAlternative] = None


class KnowledgeItem(BaseModel):
    id: str
    type: KnowledgeType
    content: str
    score: float
    meta: KnowledgeMeta = Field(default_factory=KnowledgeMeta)


class Diagnostics(BaseModel):
    total_scanned: int = 0
    time_ms: int = 0
    backend_ms: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str
    context: Optional[SearchContext] = None
    knowledge_types: Optional[list[KnowledgeType]] = None
    top_k: int = Field(default=5, ge=1, le=10)
    min_score: float = Field(default=0.7, ge=0.0, le=1.0)


class SearchResponse(BaseModel):
    items: list[KnowledgeItem] = Field(default_factory=list)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)
    layered_recommendations: list[LayeredRecommendation] = Field(default_factory=list)


# ── entity ──

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
    context_: Optional[str] = Field(default=None, alias="context")
    synonyms: list[str] = Field(default_factory=list)
    related_terms: list[str] = Field(default_factory=list)


class EntityDetailResponse(BaseModel):
    entity_name: str
    entity_type: EntityType
    definition: EntityDefinition = Field(default_factory=EntityDefinition)
    related_specs: list[str] = Field(default_factory=list)
    version_changelog: list[VersionChange] = Field(default_factory=list)


# ── specs ──

class SpecItem(BaseModel):
    id: str
    rule: str
    category: str
    effective_condition: Optional[str] = None
    positive_example: Optional[str] = None
    negative_example: Optional[str] = None
    conflicts: list[str] = Field(default_factory=list)


class SpecResponse(BaseModel):
    specs: list[SpecItem] = Field(default_factory=list)
    conflict_warnings: list[str] = Field(default_factory=list)


# ── recommend ──

class ProjectMeta(BaseModel):
    project_id: str
    team: str
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


class RecommendResponse(BaseModel):
    pinned_knowledge: PinnedKnowledge = Field(default_factory=PinnedKnowledge)


# ── feedback ──

class ModificationDetail(BaseModel):
    original_generated_code: Optional[str] = None
    final_accepted_code: Optional[str] = None
    rejection_reason: Optional[str] = None


class FeedbackRequest(BaseModel):
    session_id: str
    consumed_knowledge_ids: list[str]
    action: FeedbackAction
    modification_detail: Optional[ModificationDetail] = None


class FeedbackResponse(BaseModel):
    status: str = "recorded"
    feedback_id: str


# ── health ──

class HealthStatus(BaseModel):
    status: str  # "ok" | "degraded" | "down"
    es: bool = False
    milvus: bool = False
    neo4j: bool = False
