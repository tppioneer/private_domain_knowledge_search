# Search Service —— 私域知识混合检索微服务详细设计

## 版本历史
| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-05-10 | 初始设计，5个API端点 + 混合检索引擎 + 优雅降级 |

---

## 1. 架构定位

```
开发者A ──IDE + MCP Server(stdio)──┐
开发者B ──IDE + MCP Server(stdio)──┼── HTTP REST ──→ Search Service ──→ ES / Milvus / Neo4j
开发者C ──IDE + MCP Server(stdio)──┘                  (FastAPI, 团队共享)
```

- **MCP Server**：每IDE一个进程，stdio 通信，负责协议适配 + Prompt 组装
- **Search Service**：独立微服务，团队共享，承载混合检索 + 索引管理
- MCP Server 通过 `RemoteSearchEngine` + `RemoteKnowledgeBase` 调用 Search Service

---

## 2. 项目结构

```
search_service/
├── main.py              # FastAPI 入口，注册路由和 /health
├── config.py            # ES / Milvus / Neo4j 连接配置（环境变量）
├── requirements.txt     # fastapi, uvicorn, pydantic, httpx, elasticsearch, pymilvus, neo4j
├── api/
│   ├── router.py        # 路由注册
│   ├── search.py        # POST /api/v1/search
│   ├── entities.py      # GET  /api/v1/entities/{name}
│   ├── specs.py         # GET  /api/v1/specs
│   ├── recommend.py     # POST /api/v1/recommend
│   ├── feedback.py      # POST /api/v1/feedback
│   └── health.py        # 健康检查
├── engine/
│   ├── hybrid_searcher.py  # 融合编排：并行检索 → 合并去重 → 重排序
│   ├── bm25.py             # Elasticsearch BM25 全文检索
│   ├── vector.py           # Milvus 稠密向量语义检索
│   ├── graph.py            # Neo4j 代码知识图谱遍历
│   └── ranker.py           # 重排序模块（试点期启发式，推广期 Cross-BERT）
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

### 4.1 流水线

```
  query + context
       │
       ├─→ BM25 (ES)     ─→ top_k*3 候选
       ├─→ 向量 (Milvus) ─→ top_k*3 候选
       └─→ 图遍历 (Neo4j)─→ 关联实体文档
              │
              ↓ 三路并行检索（asyncio.gather）
         合并 + 去重（按 ID，保留最高分）
              │
              ↓
         重排序（类型boost + 关键词命中加成 → Cross-BERT）
              │
              ↓
         Top-K 裁剪（min_score 过滤 + top_k 截断）
              │
              ↓
         {items, diagnostics}
```

### 4.2 核心代码：HybridSearcher

`engine/hybrid_searcher.py` 中的 `HybridSearcher.search()` 是编排入口：

```python
async def search(self, query, context, knowledge_types, top_k, min_score):
    candidate_k = top_k * 3  # 每路多取3倍候选

    # 三路并行检索（各自独立超时/错误处理）
    bm25_task = _safe_search(self.bm25, "bm25", query, ...)
    vector_task = _safe_search(self.vector, "vector", query, ...)
    graph_task = _safe_search(self.graph, "graph", query, ...)

    bm25_results, vector_results, graph_results = await asyncio.gather(
        bm25_task, vector_task, graph_task
    )

    # 融合去重
    merged = {}
    for item in bm25_results + vector_results + graph_results:
        if item.id not in merged or item.score > merged[item.id].score:
            merged[item.id] = item

    # 重排序 + 裁剪
    ranked = rerank(list(merged.values()), query)
    return [item for item in ranked if item.score >= min_score][:top_k]
```

### 4.3 BM25 检索器 (`engine/bm25.py`)

基于 Elasticsearch 的 BM25 全文检索：
- 支持 `knowledge_types` 和 `module` 过滤
- 试点期 ES 不可用时 `available` 属性返回 `False`，search 返回空列表

### 4.4 向量检索器 (`engine/vector.py`)

基于 Milvus 的稠密向量语义检索：
- 试点期 embedding 使用占位（零向量），接入真实数据时替换为 `bge-large-zh` 调用
- Milvus 不可用时优雅降级

### 4.5 图遍历检索器 (`engine/graph.py`)

基于 Neo4j 的代码知识图谱遍历：
- 通过 Cypher 查询匹配实体及其关联节点
- 关联实体路径注入结果 `meta.related_entity`

### 4.6 重排序器 (`engine/ranker.py`)

试点期使用启发式加权：
- 知识类型 boost（API 1.0 > best_practice 0.95 > defect_history 0.90 > security_rule 0.85 > term 0.80）
- 关键词命中加成（每个命中词 +0.02）
- 推广期替换为 Cross-BERT 模型

### 4.7 优雅降级

每个检索器在以下情况返回空列表而非抛出异常：
- 后端不可达（`available` 属性检查）
- 查询超时（`asyncio.wait_for`）
- 查询执行异常

`diagnostics.warnings` 记录每条路径的状态，方便运维排查。

---

## 5. 配置说明

所有配置通过环境变量注入，`config.py` 中定义默认值：

| 环境变量 | 默认值 | 描述 |
|----------|--------|------|
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

## 6. 启动方式

```bash
# 安装依赖
pip install -r search_service/requirements.txt

# 启动服务
uvicorn search_service.main:app --host 0.0.0.0 --port 8080

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

## 7. 数据模型

`search_service/models/schemas.py` 与 `mcp/models/schemas.py` 结构完全一致，包含：

- `SearchRequest` / `SearchResponse` — 混合检索
- `EntityDetailResponse` — 实体查询
- `SpecResponse` — 规范契约
- `PinnedKnowledge` — 上下文推荐
- `FeedbackRequest` / `FeedbackResponse` — 反馈上报
- `HealthStatus` — 健康检查（Search Service 特有）

两边的模型可考虑后续抽取为共享 package（pip install 或 git submodule）。
