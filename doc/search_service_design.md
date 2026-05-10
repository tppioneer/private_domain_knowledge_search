# Search Service —— 私域知识混合检索微服务详细设计

## 版本历史
| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-05-10 | 初始设计，5个API端点 + 混合检索引擎 + 优雅降级 |
| v1.1 | 2026-05-10 | 工厂模式重构：lite/production 双后端 + pipeline 数据预处理骨架 |

---

## 1. 架构定位

```
开发者A ──IDE + MCP Server(stdio)──┐
开发者B ──IDE + MCP Server(stdio)──┼── HTTP REST ──→ Search Service ──→ [开发] SQLite/FAISS/NetworkX
开发者C ──IDE + MCP Server(stdio)──┘                  (FastAPI, 团队共享)  [生产] ES/Milvus/Neo4j
```

- **MCP Server**：每IDE一个进程，stdio 通信，负责协议适配 + Prompt 组装
- **Search Service**：独立微服务，团队共享，承载混合检索 + 索引管理
- **后端双模式**：`BACKEND_MODE=lightweight`（默认，免外部服务）或 `production`（ES/Milvus/Neo4j）
- MCP Server 通过 `RemoteSearchEngine` + `RemoteKnowledgeBase` 调用 Search Service

---

## 2. 项目结构

```
search_service/
├── main.py              # FastAPI 入口，注册路由和 /health
├── config.py            # 后端模式 + ES/Milvus/Neo4j + lite 路径配置
├── requirements.txt     # fastapi, uvicorn, pydantic, sqlite3, faiss, networkx, ...
├── api/
│   ├── router.py        # 路由注册
│   ├── search.py        # POST /api/v1/search（真实引擎 → mock 降级）
│   ├── entities.py      # GET  /api/v1/entities/{name}
│   ├── specs.py         # GET  /api/v1/specs
│   ├── recommend.py     # POST /api/v1/recommend
│   ├── feedback.py      # POST /api/v1/feedback
│   └── health.py        # 健康检查
├── engine/
│   ├── factory.py            # 工厂：根据 BACKEND_MODE 创建对应实现
│   ├── hybrid_searcher.py    # 融合编排：并行检索 → 合并去重 → 重排序
│   ├── ranker.py             # 重排序模块（启发式加权 + 类型boost）
│   └── backends/
│       ├── protocols.py      # SearcherProtocol 接口定义
│       ├── lite/             # 轻量实现（纯 Python，免外部服务）
│       │   ├── bm25.py       # SQLite FTS5 + BM25 排序
│       │   ├── vector.py     # FAISS IndexFlatIP
│       │   ├── graph.py      # NetworkX DiGraph
│       │   └── embedding.py  # sentence-transformers / 零向量降级
│       └── production/       # 生产实现（需 Docker 部署）
│           ├── bm25.py       # Elasticsearch BM25
│           ├── vector.py     # Milvus 向量检索
│           └── graph.py      # Neo4j 图遍历
├── pipeline/                  # 数据预处理管道
│   ├── loader.py              # 文件加载器（.md / .py / .go）
│   ├── chunker.py             # 文档分块（语义边界 + 重叠窗口）
│   └── orchestrator.py        # 编排：加载 → 分块 → embedding → 索引/入图
└── models/
    └── schemas.py        # Pydantic 数据模型（与 MCP Server 结构一致）
```

---

## 3. API 设计

### 3.1 POST /api/v1/search —— 混合检索

**Request:**
```json
{
  "query": "积分扣减 SDK 幂等处理",
  "context": {
    "file_path": "module/points/points_service.go",
    "language": "go",
    "module": "points",
    "dependencies": [{"name": "points-sdk", "version": "v2.3"}]
  },
  "knowledge_types": ["api", "best_practice"],
  "top_k": 5,
  "min_score": 0.7
}
```

**Response:**
```json
{
  "items": [
    {
      "id": "sdk_doc_101",
      "type": "api",
      "content": "points-sdk v2.3 DeductPoints(bizId, uid, points)...",
      "score": 0.96,
      "meta": {
        "sdk": "points-sdk",
        "version": "v2.3",
        "code_example": "changeId, err := sdk.DeductPoints(bizId, uid, points)",
        "related_entity": "PointsService"
      }
    }
  ],
  "diagnostics": {
    "total_scanned": 3400,
    "time_ms": 72,
    "warnings": []
  }
}
```

**试点期降级策略：** 检索引擎不可用或无结果时，自动返回预设 mock 数据（设计文档附录中的积分服务场景示例），确保 MCP Server 端到端可用。

### 3.2 GET /api/v1/entities/{name}

精确查询实体（术语或API）的完整结构化定义。

**Params:**
- `entity_type` (可选) — `term` | `api`
- `version_requirement` (可选) — 如 `>=2.1.0`

**Response:** 同 MCP 设计文档 `EntityDetailResponse` 结构。

### 3.3 GET /api/v1/specs

根据模块、文件路径获取适用的规范契约。

**Params:**
- `module` (可选) — 如 `points`
- `file_path` (可选)
- `dependency_constraints` (可选) — JSON string

**Response:** 同 `SpecResponse` 结构。

### 3.4 POST /api/v1/recommend

根据项目元信息推荐高频知识素材。

**Request:** `ProjectMeta` 结构
**Response:** `PinnedKnowledge` 结构

### 3.5 POST /api/v1/feedback

上报知识采纳/拒绝反馈，记录 feedback_id。权重调整逻辑后续实现。

### 3.6 GET /health

```json
{
  "status": "ok",
  "es": true,
  "milvus": true,
  "neo4j": true
}
```
- `status`: `ok` | `degraded`（部分后端可用） | `down`（全部不可用）

---

## 4. 混合检索引擎（核心）

### 4.1 工厂模式

`engine/factory.py` 根据 `BACKEND_MODE` 环境变量创建对应后端实现，与 MCP Server 的 Mock/Remote 切换模式一致：

```
BACKEND_MODE=lightweight → LiteBM25Searcher / LiteVectorSearcher / LiteGraphSearcher
BACKEND_MODE=production  → BM25Searcher / VectorSearcher / GraphSearcher
```

```python
from .factory import create_bm25_searcher, create_vector_searcher, create_graph_searcher

class HybridSearcher:
    def __init__(self):
        self.bm25 = create_bm25_searcher()
        self.vector = create_vector_searcher()
        self.graph = create_graph_searcher()
```

所有后端实现均满足 `backends/protocols.py` 中定义的 `SearcherProtocol` 接口（`available` 属性 + `async search()` 方法）。

### 4.2 混合检索流水线

```
  query + context
       │
       ├─→ BM25 (SQLite/ES)       ─→ top_k*3 候选
       ├─→ 向量 (FAISS/Milvus)    ─→ top_k*3 候选
       └─→ 图遍历 (NetworkX/Neo4j)─→ 关联实体文档
              │
              ↓ 三路并行检索（asyncio.gather，各路由 _safe_search 保护）
         合并 + 去重（按 ID，保留最高分）
              │
              ↓
         重排序（类型boost + 关键词命中加成）
              │
              ↓
         Top-K 裁剪（min_score 过滤 + top_k 截断）
              │
              ↓
         {items, diagnostics}
```

### 4.3 轻量后端（`backends/lite/`）

| 能力 | 组件 | 关键实现 |
|------|------|----------|
| 全文检索 | SQLite FTS5 | `bm25()` 排序函数，FTS5 MATCH 查询，Sigmoid 分数归一化 |
| 向量检索 | FAISS | `IndexFlatIP` 内积相似度，文件持久化（vectors.index + id_map.json） |
| 图遍历 | NetworkX | 内存有向图，节点内容子串匹配，JSON 文件持久化 |
| Embedding | sentence-transformers / 零向量 | 优先加载 `bge-small-zh`，不可用时返回 `[0.0]*768` 占位 |

**轻量后端的优雅降级**：所有本地组件必定可达（`available = True`），无数据时返回空结果。异常通过 `logger.exception` 记录。

### 4.4 生产后端（`backends/production/`）

BM25 → Elasticsearch / 向量 → Milvus / 图遍历 → Neo4j。模块由原 `engine/` 平铺位置移入，内容不变，仅 `import` 路径更新为 `....models`。

### 4.5 重排序器 (`engine/ranker.py`)

启发式加权：

| 类型 | boost |
|------|-------|
| api | 1.00 |
| best_practice / spec | 0.95 |
| document / defect_history | 0.90 |
| security_rule | 0.85 |
| term | 0.80 |
| test_template | 0.75 |

每个命中关键词 +0.02。推广期替换为 Cross-BERT 模型。

---

## 5. 配置说明

所有配置通过环境变量注入，`config.py` 中定义默认值：

| 环境变量 | 默认值 | 描述 |
|----------|--------|------|
| `BACKEND_MODE` | `lightweight` | 后端模式：`lightweight` 或 `production` |
| `SQLITE_DB_PATH` | `./data/knowledge.db` | 轻量模式 SQLite 数据库路径 |
| `FAISS_INDEX_DIR` | `./data/faiss` | 轻量模式 FAISS 索引目录 |
| `GRAPH_STORAGE_PATH` | `./data/graph.json` | 轻量模式 NetworkX 图存储路径 |
| `SEARCH_HOST` | `0.0.0.0` | 服务监听地址 |
| `SEARCH_PORT` | `8080` | 服务端口 |
| `ES_HOSTS` | `http://localhost:9200` | ES 地址（逗号分隔） |
| `ES_INDEX` | `private_knowledge` | ES 索引名 |
| `MILVUS_HOST` | `localhost` | Milvus 地址 |
| `MILVUS_PORT` | `19530` | Milvus 端口 |
| `MILVUS_COLLECTION` | `knowledge_vectors` | Milvus 集合名 |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 连接 |
| `NEO4J_USER` | `neo4j` | Neo4j 用户 |
| `NEO4J_PASSWORD` | `` | Neo4j 密码 |
| `CANDIDATE_MULTIPLIER` | `3` | 候选倍数（每路取 top_k*N） |
| `SEARCH_TIMEOUT_MS` | `300` | 单路检索超时 |

---

## 6. 数据预处理管道

`pipeline/` 目录提供数据预处理骨架，企业数据到位后调用 `orchestrator.run(data_dir)` 即可灌入后端：

```
loader.load("docs/*.md")           # 加载文档
  → chunker.chunk_text(text)       # 分块（3K-5K tokens，重叠窗口）
    → embedding.embed(chunks)      # 向量化（bge-small-zh / 零向量降级）
      → bm25.index_documents()     # 写入 SQLite FTS5（或 ES）
      → vector.index_vectors()     # 写入 FAISS（或 Milvus）
      → graph.add_entity/relation()# 写入 NetworkX（或 Neo4j）
```

**分块策略**：按空行+标题边界切段落，合并至 3K-10K 字符（~1500-5000 tokens），块间重叠 ~300 tokens。

**运行方式**：
```bash
# 摄入指定目录下的 Markdown / Python / Go 文件
python -m search_service.pipeline.orchestrator doc/

# 或从代码调用
from search_service.pipeline.orchestrator import run
stats = run("doc/", patterns=["**/*.md"])
# → {"files": 7, "chunks": 9, "indexed_bm25": 9, "indexed_vector": 9, "graph_nodes": 9}
```

---

## 7. 启动方式

```bash
# 安装依赖
pip install -r search_service/requirements.txt

# 默认 lightweight 模式（SQLite + FAISS + NetworkX，免外部服务）
uvicorn search_service.main:app --host 0.0.0.0 --port 8080

# 生产模式（需先 docker compose up 启动 ES/Milvus/Neo4j）
BACKEND_MODE=production uvicorn search_service.main:app --host 0.0.0.0 --port 8080

# 或直接运行
python -m search_service.main
```

MCP Server 侧通过 `SEARCH_SERVICE_URL` 环境变量指向该服务：
```bash
export SEARCH_SERVICE_URL=http://localhost:8080
python -m mcp.server
# 未设置时自动使用 Mock 实现
```

---

## 8. 数据模型

`search_service/models/schemas.py` 与 `mcp/models/schemas.py` 结构完全一致，包含：

- `SearchRequest` / `SearchResponse` — 混合检索
- `EntityDetailResponse` — 实体查询
- `SpecResponse` — 规范契约
- `PinnedKnowledge` — 上下文推荐
- `FeedbackRequest` / `FeedbackResponse` — 反馈上报
- `HealthStatus` — 健康检查（Search Service 特有）

两边的模型可考虑后续抽取为共享 package（pip install 或 git submodule）。
