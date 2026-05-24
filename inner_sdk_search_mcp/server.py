"""私域知识自动检索 MCP Server。

基于 Model Context Protocol，将私域知识检索能力封装为 6 个标准化工具：

- search_private_knowledge  : 通用混合检索（支持 auto_assemble）
- get_entity_detail         : 精确实体查询
- get_applicable_spec       : 获取适用规范契约
- recommend_context         : 项目上下文预加载
- report_feedback           : 知识反馈上报
- assemble_prompt           : Prompt 上下文组装（清洗→去重→组装）
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import server_config
from .metrics import MetricsContext
from .models.schemas import (
    AssemblePromptInput,
    AutoAssembleConfig,
    Context,
    EntityType,
    FeedbackAction,
    KnowledgeType,
    ProjectMeta,
)
from .services import (
    MockKnowledgeBase,
    MockSearchEngine,
    RemoteKnowledgeBase,
    RemoteSearchEngine,
)
from .services.knowledge_base import KnowledgeBase
from .services.search_engine import SearchEngine
from .tools import (
    assemble_prompt_tool,
    get_applicable_spec,
    get_entity_detail,
    recommend_context,
    report_feedback,
    search_private_knowledge,
)


class PrivateKnowledgeMCPServer:
    """私域知识 MCP Server 封装。"""

    def __init__(self, search_engine: SearchEngine, knowledge_base: KnowledgeBase):
        self.search_engine = search_engine
        self.knowledge_base = knowledge_base
        self.server = FastMCP(server_config.name)

        self._register_tools()

    def _register_tools(self) -> None:
        s = self.server
        engine = self.search_engine
        kb = self.knowledge_base

        @s.tool(
            name="search_private_knowledge",
            description="""搜索企业私域知识库，查找内部SDK的API签名、使用示例和最佳实践。

【何时必须调用此工具】
- 用户提到内部SDK、私有SDK、公司自研组件、内部工具类时
- 用户提到具体的工具类名（如 FileManager、PointsFactory、GamClient 等）
- 用户需要"对接"、"接入"、"封装"、"调用"某个内部SDK
- 用户问到某个内部API的方法签名、参数、返回值
- 用户需要了解企业内部编码规范或历史缺陷记录

【触发关键词】内部SDK、私有SDK、自研、工具类、封装、对接、接入、怎么用、方法签名、com.、Factory、Manager、Helper""",
        )
        async def search_private_knowledge_tool(
            query: str,
            context: dict | None = None,
            knowledge_types: list[str] | None = None,
            top_k: int = 5,
            min_score: float = 0.7,
            auto_assemble: dict | None = None,
        ) -> dict:
            ctx = Context(**context) if context else None
            types = [KnowledgeType(t) for t in knowledge_types] if knowledge_types else None
            aa_config = AutoAssembleConfig(**auto_assemble) if auto_assemble else None
            session_id = (context or {}).get("session_id", "")
            with MetricsContext(
                "search_private_knowledge", session_id=session_id,
                log_dir=server_config.metrics_log_dir,
                enabled=server_config.metrics_enabled,
            ) as mc:
                mc.set_input(
                    query_len=len(query), top_k=top_k, min_score=min_score,
                    knowledge_types=knowledge_types, auto_assemble=bool(auto_assemble),
                )
                with mc.span("remote_search") as span:
                    result = await search_private_knowledge(
                        engine=engine, query=query, context=ctx,
                        knowledge_types=types, top_k=top_k, min_score=min_score,
                        auto_assemble=aa_config, kb=kb,
                    )
                    span.meta["item_count"] = len(result.items)
                    span.meta["backend_ms"] = result.diagnostics.backend_ms
                mc.set_output(
                    result_count=len(result.items),
                    has_assembled=result.assembled_prompt is not None,
                )
                if result.assembled_prompt:
                    mc.set_output(assembled_tokens=result.assembled_prompt.estimated_tokens)
            return result.model_dump()

        @s.tool(
            name="get_entity_detail",
            description="""精确查询某个SDK类、API方法或企业术语的完整定义、参数说明和代码示例。
使用时机：用户明确提到某个实体名称时（如"FileManager是什么"、"DeductPoints参数有哪些"）。""",
        )
        async def get_entity_detail_tool(
            entity_name: str,
            entity_type: str | None = None,
            version_requirement: str | None = None,
        ) -> dict:
            etype = EntityType(entity_type) if entity_type else None
            with MetricsContext(
                "get_entity_detail", session_id="",
                log_dir=server_config.metrics_log_dir,
                enabled=server_config.metrics_enabled,
            ) as mc:
                mc.set_input(entity_name=entity_name, entity_type=entity_type)
                with mc.span("get_entity") as span:
                    result = await get_entity_detail(
                        kb=kb, entity_name=entity_name, entity_type=etype,
                        version_requirement=version_requirement,
                    )
                    span.meta["found"] = result.entity_name != "" if result.entity_name else False
                mc.set_output(found=bool(result.entity_name))
            return result.model_dump()

        @s.tool(
            name="get_applicable_spec",
            description="""获取当前项目模块必须遵守的编码规范和数据契约。使用时机：用户生成代码前需要确认规范约束，或代码审查时检查合规性。""",
        )
        async def get_applicable_spec_tool(
            module: str | None = None,
            file_path: str | None = None,
            dependency_constraints: dict | None = None,
        ) -> dict:
            with MetricsContext(
                "get_applicable_spec", session_id="",
                log_dir=server_config.metrics_log_dir,
                enabled=server_config.metrics_enabled,
            ) as mc:
                mc.set_input(module=module, file_path=file_path)
                with mc.span("get_specs") as span:
                    result = await get_applicable_spec(
                        kb=kb, module=module, file_path=file_path,
                        dependency_constraints=dependency_constraints,
                    )
                    span.meta["spec_count"] = len(result.specs)
                mc.set_output(spec_count=len(result.specs))
            return result.model_dump()

        @s.tool(
            name="recommend_context",
            description="""根据项目元信息预加载高频知识骨架（架构概览、常用API列表、安全白名单）。
使用时机：IDE首次连接时自动调用，或项目依赖变更时手动刷新。""",
        )
        async def recommend_context_tool(
            project_meta: dict,
        ) -> dict:
            meta = ProjectMeta(**project_meta)
            with MetricsContext(
                "recommend_context", session_id="",
                log_dir=server_config.metrics_log_dir,
                enabled=server_config.metrics_enabled,
            ) as mc:
                mc.set_input(project_id=meta.project_id, team=meta.team)
                with mc.span("get_recommend") as span:
                    result = await recommend_context(kb=kb, project_meta=meta)
                    pinned = result.pinned_knowledge
                    span.meta["has_architecture"] = bool(pinned.architecture_overview)
                    span.meta["api_count"] = len(pinned.common_apis)
                mc.set_output(
                    has_architecture=bool(pinned.architecture_overview),
                    api_count=len(pinned.common_apis),
                )
            return result.model_dump()

        @s.tool(
            name="report_feedback",
            description="""上报用户对注入知识的采纳/拒绝反馈，用于优化检索排序。使用时机：用户采纳或修改了MCP提供的知识后。""",
        )
        async def report_feedback_tool(
            session_id: str,
            consumed_knowledge_ids: list[str],
            action: str,
            modification_detail: dict | None = None,
        ) -> dict:
            with MetricsContext(
                "report_feedback", session_id=session_id,
                log_dir=server_config.metrics_log_dir,
                enabled=server_config.metrics_enabled,
            ) as mc:
                mc.set_input(action=action, consumed_count=len(consumed_knowledge_ids))
                with mc.span("submit_feedback") as span:
                    result = await report_feedback(
                        kb=kb, session_id=session_id,
                        consumed_knowledge_ids=consumed_knowledge_ids,
                        action=FeedbackAction(action),
                    )
                    span.meta["feedback_id"] = result.feedback_id
                mc.set_output(feedback_id=result.feedback_id, status=result.status)
            return result.model_dump()

        @s.tool(
            name="assemble_prompt",
            description="""将多来源检索结果清洗、去重、组装为结构化的Prompt。使用时机：search_private_knowledge 返回结果后，可以用此工具组装完整的上下文Prompt；或设置 search_private_knowledge 的 auto_assemble=true 自动组装。""",
        )
        async def assemble_prompt_tool_handler(
            user_query: str,
            context: dict | None = None,
            search_items: list[dict] | None = None,
            specs: list[dict] | None = None,
            pinned_knowledge: dict | None = None,
            max_tokens: int = 4096,
            role_hint: str = "",
        ) -> dict:
            input_ = AssemblePromptInput(
                user_query=user_query,
                context=context,
                search_items=search_items or [],
                specs=specs or [],
                pinned_knowledge=pinned_knowledge,
                max_tokens=max_tokens,
                role_hint=role_hint,
            )
            session_id = (context or {}).get("session_id", "")
            with MetricsContext(
                "assemble_prompt", session_id=session_id,
                log_dir=server_config.metrics_log_dir,
                enabled=server_config.metrics_enabled,
            ) as mc:
                mc.set_input(
                    query_len=len(user_query), max_tokens=max_tokens,
                    item_count=len(search_items or []), spec_count=len(specs or []),
                )
                with mc.span("assemble") as span:
                    result = await assemble_prompt_tool(input_)
                    span.meta["estimated_tokens"] = result.estimated_tokens
                    span.meta["truncated"] = result.truncated_items > 0
                mc.set_output(
                    estimated_tokens=result.estimated_tokens,
                    truncated=result.truncated_items > 0,
                )
            return result.model_dump()

    async def run(self):
        await self.server.run_stdio_async()


def create_server(
    search_engine: SearchEngine | None = None,
    knowledge_base: KnowledgeBase | None = None,
) -> PrivateKnowledgeMCPServer:
    """创建 MCP Server 实例。

    优先级: 显式传入 > SEARCH_SERVICE_URL 环境变量 > Mock 实现
    """
    if search_engine is None:
        if server_config.search_service_url:
            search_engine = RemoteSearchEngine()
        else:
            search_engine = MockSearchEngine()

    if knowledge_base is None:
        if server_config.search_service_url:
            knowledge_base = RemoteKnowledgeBase()
        else:
            knowledge_base = MockKnowledgeBase()

    return PrivateKnowledgeMCPServer(
        search_engine=search_engine,
        knowledge_base=knowledge_base,
    )


async def main():
    server = create_server()
    await server.run()


def run():
    """同步入口点，供 setuptools console_scripts 调用。"""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()

