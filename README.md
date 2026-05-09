# 私域知识赋能 AI Coding 工具

基于 **Spec + RAG 双引擎** 的技术体系，将企业私域知识（术语、SDK文档、编码规范、历史缺陷）注入 AI 编程工具，使 AI 从"通用码农"升级为"业务专家型 Copilot"。

当前阶段：**试点期**，完成 MCP Server 框架 + Prompt 组装 + Search Service 混合检索微服务骨架。

---

## 架构总览

```
开发者A ──IDE + MCP Server(stdio)──┐
开发者B ──IDE + MCP Server(stdio)──┼── HTTP REST ──→ Search Service ──→ ES / Milvus / Neo4j
开发者C ──IDE + MCP Server(stdio)──┘                  (团队共享微服务)
```

| 组件 | 定位 | 当前状态 |
|------|------|----------|
| **mcp/** | MCP Server，stdio 协议适配 + Prompt 组装 | 6 个工具已完成，Mock 开箱即用 |
| **search_service/** | 混合检索微服务，HTTP REST API | 5 个 API + 混合检索引擎骨架，Mock 降级 |
| **doc/** | 设计文档（需求方案、MCP 设计、Search Service 设计） | — |

---

## 项目结构

```
private_domain_knowledge_search/
├── README.md
├── CLAUDE.md
├── doc/
│   ├── requirement.md             # 私域知识赋能 AI Coding 总体技术方案
│   ├── mcp_design.md              # MCP Server 6 个工具接口详细设计
│   └── search_service_design.md   # Search Service 微服务详细设计
├── mcp/                           # MCP Server（每 IDE 一个进程）
│   ├── server.py                  # 主入口，注册 6 个 MCP 工具
│   ├── config.py                  # 配置（搜索超时、Prompt 参数、Search Service URL）
│   ├── requirements.txt           # mcp, pydantic, httpx
│   ├── models/schemas.py          # 全部请求/响应 Pydantic 模型
│   ├── services/                  # 检索后端抽象 + 实现
│   │   ├── search_engine.py       # SearchEngine 抽象接口
│   │   ├── knowledge_base.py      # KnowledgeBase 抽象接口
│   │   ├── remote_search_engine.py   # HTTP 调用 Search Service
│   │   └── remote_knowledge_base.py  # HTTP 调用 Search Service
│   ├── tools/                     # 6 个 MCP 工具处理函数
│   │   ├── search_knowledge.py    # search_private_knowledge（含 auto_assemble）
│   │   ├── get_entity.py          # get_entity_detail
│   │   ├── get_spec.py            # get_applicable_spec
│   │   ├── recommend_context.py   # recommend_context
│   │   ├── report_feedback.py     # report_feedback
│   │   └── assemble_prompt.py     # assemble_prompt
│   └── prompt/                    # Prompt 组装流水线
│       ├── sanitizer.py           # 安全清洗（零宽字符、Unicode规范化、越狱检测）
│       ├── deduplicator.py        # 去重（ID精确 + 内容相似度）
│       ├── assembler.py           # 分组排序 + Token预算裁剪 + 4段式组装
│       └── assemble_prompt.py     # 流水线入口
└── search_service/                # 混合检索微服务（团队共享）
    ├── main.py                    # FastAPI 入口
    ├── config.py                  # ES/Milvus/Neo4j 连接配置
    ├── requirements.txt           # fastapi, uvicorn, pydantic, httpx, elasticsearch...
    ├── api/                       # REST API 端点
    │   ├── search.py              # POST /api/v1/search
    │   ├── entities.py            # GET  /api/v1/entities/{name}
    │   ├── specs.py               # GET  /api/v1/specs
    │   ├── recommend.py           # POST /api/v1/recommend
    │   ├── feedback.py            # POST /api/v1/feedback
    │   └── health.py              # GET  /health
    ├── engine/                    # 混合检索引擎
    │   ├── hybrid_searcher.py     # 三路并行 → 融合去重 → 重排序
    │   ├── bm25.py                # Elasticsearch BM25 检索
    │   ├── vector.py              # Milvus 向量语义检索
    │   ├── graph.py               # Neo4j 图遍历检索
    │   └── ranker.py              # 重排序模块
    └── models/schemas.py          # 与 mcp 端一致的 Pydantic 模型
```

---

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 1. 启动 Search Service（混合检索微服务）

```bash
# 安装依赖
pip install -r search_service/requirements.txt

# 启动服务（试点期默认使用 Mock 数据，无需 ES/Milvus/Neo4j）
uvicorn search_service.main:app --host 0.0.0.0 --port 8080

# 或直接运行
python -m search_service.main
```

验证：
```bash
# 混合检索
curl -X POST http://localhost:8080/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"积分扣减","top_k":3}'

# 实体查询
curl http://localhost:8080/api/v1/entities/GamClient.callback?entity_type=api

# 规范查询
curl "http://localhost:8080/api/v1/specs?module=points"

# 上下文推荐
curl -X POST http://localhost:8080/api/v1/recommend \
  -H "Content-Type: application/json" \
  -d '{"project_id":"p1","team":"order-team","tech_stack":["python"],"core_dependencies":[{"name":"sdk","version":"1.0"}],"modules":["points"]}'

# 反馈上报
curl -X POST http://localhost:8080/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","consumed_knowledge_ids":["kb_1"],"action":"accepted"}'

# 健康检查
curl http://localhost:8080/health
```

### 2. 启动 MCP Server（IDE 协议适配层）

```bash
# 安装依赖
pip install -r mcp/requirements.txt

# 方式 A：使用 Mock 实现（无需 Search Service）
python -m mcp.server

# 方式 B：对接 Search Service
# Linux/Mac
export SEARCH_SERVICE_URL=http://localhost:8080
# Windows PowerShell
$env:SEARCH_SERVICE_URL = "http://localhost:8080"

python -m mcp.server
```

MCP Server 通过 stdio 与 IDE 通信。不设置 `SEARCH_SERVICE_URL` 时自动使用 Mock 实现，开箱即用。

### 3. 配置 IDE

在 IDE 的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "private-knowledge": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "cwd": "/path/to/private_domain_knowledge_search"
    }
  }
}
```

---

## MCP 工具清单

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `search_private_knowledge` | 通用混合检索（支持 `auto_assemble` 自动组装 Prompt） | query, context, knowledge_types, top_k, auto_assemble |
| `get_entity_detail` | 精确查询术语/API 的完整定义与示例 | entity_name, entity_type, version_requirement |
| `get_applicable_spec` | 获取模块适用的规范契约 | module, file_path, dependency_constraints |
| `recommend_context` | 基于项目元信息推荐高频知识骨架 | project_meta |
| `report_feedback` | 上报知识采纳/拒绝反馈，驱动闭环优化 | session_id, consumed_knowledge_ids, action |
| `assemble_prompt` | 清洗→去重→组装 4 段式结构化 Prompt | user_query, search_items, specs, max_tokens, role_hint |

---

## 配置项

### MCP Server（环境变量）

| 变量 | 默认值 | 描述 |
|------|--------|------|
| `SEARCH_SERVICE_URL` | （空） | Search Service 地址，未设置使用 Mock |
| `SEARCH_TIMEOUT_MS` | `300` | 搜索超时（毫秒） |
| `PROMPT_DEFAULT_MAX_TOKENS` | `4096` | Prompt 最大 token 数 |
| `PROMPT_DEDUP_THRESHOLD` | `0.92` | 去重相似度阈值 |
| `PROMPT_DEFAULT_ROLE_HINT` | `资深软件工程师` | 默认角色提示 |

### Search Service（环境变量）

| 变量 | 默认值 | 描述 |
|------|--------|------|
| `SEARCH_HOST` | `0.0.0.0` | 监听地址 |
| `SEARCH_PORT` | `8080` | 监听端口 |
| `ES_HOSTS` | `http://localhost:9200` | ES 地址 |
| `MILVUS_HOST` | `localhost` | Milvus 地址 |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 地址 |
| `CANDIDATE_MULTIPLIER` | `3` | 候选倍数 |
| `SEARCH_TIMEOUT_MS` | `300` | 单路检索超时（毫秒） |

---

## E2E 验证结果（2026-05-10）

| 测试项 | 结果 |
|--------|------|
| Search Service `/api/v1/search` 返回 mock 数据 | 3 items（api/best_practice/defect_history），scores 0.96/0.93/0.89 |
| Search Service `/api/v1/entities/{name}` 实体查询 | GamClient.callback 签名完整返回 |
| Search Service `/api/v1/specs` 规范查询 | 1 spec + 1 conflict warning |
| Search Service `/api/v1/recommend` 上下文推荐 | 2 common APIs + 架构概览 + 安全白名单 |
| Search Service `/api/v1/feedback` 反馈上报 | recorded + feedback_id |
| MCP Server → RemoteClient → Search Service | 5 个工具全部成功调用 |
| auto_assemble 自动组装 | 569 chars Prompt，273 tokens，4 段结构完整 |

---

## 后续计划

1. **数据预处理**：文档分块 + 向量化 + 知识图谱构建，接入试点业务域真实数据
2. **检索引擎对接**：ES / Milvus / Neo4j 替换 Mock 为真实后端
3. **运营闭环**：反馈权重调整 + 知识质量评估 + 增量更新管道
4. **推广扩展**：多业务域覆盖 + 代码检视场景 + 权限隔离

详见 `doc/requirement.md`。
