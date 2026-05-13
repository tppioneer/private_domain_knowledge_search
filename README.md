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
| **inner_sdk_search_mcp/** | MCP Server，stdio 协议适配 + Prompt 组装 | 6 个工具已完成，Mock 开箱即用 |
| **search_service/** | 混合检索微服务，HTTP REST API | 5 个 API + 工厂模式双后端（lite/production）+ 数据管道 |
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
│   ├── search_service_design.md   # Search Service 微服务详细设计
│   ├── pipeline_design.md          # 数据预处理管道详细设计
│   └── deployment_guide.md         # 部署与接入指南（OpenCode 等 IDE）
├── inner_sdk_search_mcp/          # MCP Server（每 IDE 一个进程）
│   ├── server.py                  # 主入口，注册 6 个 MCP 工具
│   ├── config.py                  # 配置（搜索超时、Prompt 参数、Search Service URL）
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
    ├── config.py                  # 后端模式 + ES/Milvus/Neo4j + lite 路径配置
    ├── requirements.txt           # fastapi, uvicorn, pydantic, sqlite3, faiss, networkx...
    ├── api/                       # REST API 端点（同上）
    ├── engine/
    │   ├── factory.py             # 工厂：根据 BACKEND_MODE 创建对应实现
    │   ├── hybrid_searcher.py     # 三路并行 → 融合去重 → 重排序
    │   ├── ranker.py              # 重排序（类型boost + 关键词加成）
    │   └── backends/
    │       ├── protocols.py       # SearcherProtocol 接口定义
    │       ├── lite/              # SQLite FTS5 / FAISS / NetworkX
    │       └── production/        # Elasticsearch / Milvus / Neo4j
    ├── pipeline/                  # 数据预处理管道
    │   ├── loader.py              # 文件加载器
    │   ├── chunker.py             # 文档分块（语义边界 + 重叠窗口）
    │   └── orchestrator.py        # 编排：加载 → 分块 → embedding → 索引
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

# 默认 lightweight 模式（SQLite + FAISS + NetworkX，免外部服务）
uvicorn search_service.main:app --host 0.0.0.0 --port 8080

# 生产模式（需先部署 ES/Milvus/Neo4j）
BACKEND_MODE=production uvicorn search_service.main:app --host 0.0.0.0 --port 8080

# 导入试点数据（将 doc/*.md 设计文档索引到轻量后端）
python -m search_service.pipeline.orchestrator doc/
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
pip install -r pyproject.toml

# 方式 A：使用 Mock 实现（无需 Search Service）
python -m inner_sdk_search_mcp.server

# 方式 B：对接 Search Service
# Linux/Mac
export SEARCH_SERVICE_URL=http://localhost:8080
# Windows PowerShell
$env:SEARCH_SERVICE_URL = "http://localhost:8080"

python -m inner_sdk_search_mcp.server
```

MCP Server 通过 stdio 与 IDE 通信。不设置 `SEARCH_SERVICE_URL` 时自动使用 Mock 实现，开箱即用。

### 3. 配置 IDE

在 IDE 的 MCP 配置中添加：

```json
{
  "mcpServers": {
    "private-knowledge": {
      "command": "python",
      "args": ["-m", "inner_sdk_search_mcp.server"],
      "cwd": "/path/to/private_domain_knowledge_search"
    }
  }
}
```

---

## MCP 工具清单

| 工具 | 功能 | 触发场景 |
|------|------|----------|
| `search_private_knowledge` | 通用混合检索（支持 `auto_assemble`） | 用户提到内部SDK、工具类名、封装/对接/接入等 |
| `get_entity_detail` | 精确查询术语/API 的完整定义与示例 | 用户明确询问某个实体名称时 |
| `get_applicable_spec` | 获取模块适用的规范契约 | 生成代码前确认规范，或代码审查时 |
| `recommend_context` | 基于项目元信息推荐高频知识骨架 | IDE 首次连接时自动调用 |
| `report_feedback` | 上报知识采纳/拒绝反馈 | 用户采纳或修改 MCP 提供的知识后 |
| `assemble_prompt` | 清洗→去重→组装结构化 Prompt | search 返回结果后组装上下文 |

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
| `BACKEND_MODE` | `lightweight` | 后端模式：`lightweight` 或 `production` |
| `SQLITE_DB_PATH` | `./data/knowledge.db` | 轻量模式 SQLite 数据库路径 |
| `FAISS_INDEX_DIR` | `./data/faiss` | 轻量模式 FAISS 索引目录 |
| `GRAPH_STORAGE_PATH` | `./data/graph.json` | 轻量模式 NetworkX 图存储路径 |
| `SEARCH_HOST` | `0.0.0.0` | 监听地址 |
| `SEARCH_PORT` | `8080` | 监听端口 |
| `ES_HOSTS` | `http://localhost:9200` | ES 地址（production 模式） |
| `MILVUS_HOST` | `localhost` | Milvus 地址（production 模式） |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 地址（production 模式） |
| `CANDIDATE_MULTIPLIER` | `3` | 候选倍数 |
| `SEARCH_TIMEOUT_MS` | `300` | 单路检索超时（毫秒） |

---

## E2E 验证结果（2026-05-10）

| 测试项 | 结果 |
|--------|------|
| Pipeline 摄入 doc/*.md（7 个文档） | 9 chunks，BM25 9 / FAISS 9 / Graph 9 nodes |
| Search Service `/api/v1/search?query=MCP` | 3 个真实文档分块，scanned=27（三路并行），scores 0.92 |
| Search Service `/api/v1/search?query=Neo4j` | 3 个真实文档分块 |
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
