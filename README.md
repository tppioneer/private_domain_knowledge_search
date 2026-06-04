# 私域知识赋能 AI Coding 工具

基于 **Spec + RAG 双引擎** 的技术体系，将企业私域知识（SDK API、编码规范、历史缺陷）注入 AI 编程工具，使 AI 从"通用码农"升级为"业务专家型 Copilot"。

---

## 架构总览

```
开发者A ──IDE + MCP Server(stdio)──┐
开发者B ──IDE + MCP Server(stdio)──┼── HTTP REST ──→ Search Service ──→ SQLite/FAISS/NetworkX
开发者C ──IDE + MCP Server(stdio)──┘                  (团队共享微服务)    (or ES/Milvus/Neo4j)
```

| 组件 | 定位 |
|------|------|
| **inner_sdk_search_mcp/** | MCP Server，6 个工具 + Prompt 组装 |
| **search_service/** | 混合检索微服务，BM25 + FAISS + Graph 三路并行 |
| **pipeline/** | 数据预处理管道，Java/Python/TypeScript 源码解析 + 索引入库 |

---

## 项目结构

```
├── README.md
├── CLAUDE.md
├── pyproject.toml                     # 依赖分组：search / pipeline / lite
├── doc/
│   ├── 1.requirement.md               # 总体技术方案
│   ├── 2.search_service_design.md     # Search Service 详细设计
│   ├── 3.mcp_design.md                # MCP Server 工具接口设计
│   ├── 4.pipeline_design.md           # 数据预处理管道设计
│   ├── 5.deployment_guide.md          # 部署与接入指南
│   └── 6.human_mark_design.md         # 人工标注辅助系统设计
├── inner_sdk_search_mcp/              # MCP Server
│   ├── server.py
│   ├── config.py
│   ├── models/schemas.py
│   ├── services/                      # 检索后端抽象 + Remote/Mock 实现
│   ├── tools/                         # 6 个 MCP 工具
│   ├── prompt/                        # Prompt 组装（清洗→去重→分组→裁剪）
│   └── metrics.py                     # 工具调用埋点
├── search_service/                    # 混合检索微服务
│   ├── main.py
│   ├── config.py
│   ├── logging_config.py              # 全局日志配置
│   ├── api/                           # REST API
│   ├── engine/
│   │   ├── factory.py
│   │   ├── hybrid_searcher.py         # 三路并行 + 融合 + 日志
│   │   ├── ranker.py                  # role/layer/construction 五级加权
│   │   └── backends/lite/             # SQLite FTS5 / FAISS / NetworkX / Entity
│   └── models/schemas.py
├── pipeline/                          # 数据预处理（独立模块）
│   ├── models.py                      # PipelineChunk / ChunkMeta
│   ├── config/                        # 全局默认规则 + 标注
│   │   ├── default_rules.json
│   │   └── default_annotations.json
│   ├── adapters/                      # 多语言 tree-sitter adapter
│   │   ├── base.py                    # 共享节点类型
│   │   ├── java.py / python.py / typescript.py
│   │   └── __init__.py
│   ├── code_parser.py                 # 多语言源码解析 + role/layer 推断
│   ├── loader.py / chunker.py / classifier.py
│   └── orchestrator.py               # 编排入口
└── optimizer/                         # 检索调优工具
    ├── diff_analyzer.py
    ├── replay_validator.py
    └── param_tuner.py
```

---

## 快速开始

### 1. 安装

```bash
# 全量安装
pip install ".[lite]"

# 或按需
pip install ".[search]"     # 仅 Search Service
pip install ".[pipeline]"   # 仅 Pipeline
```

### 2. 导入知识库数据

```bash
# pipeline 是独立模块，直接 sqlite3/faiss/networkx 写入
python -m pipeline.orchestrator test_data/huawei-sdk

# 输出示例:
#   detected Maven project: 1 modules
#   bm25 indexed: 7126
#   vector indexed: 7126
#   graph nodes: 6625
```

### 3. 启动 Search Service

```bash
uvicorn search_service.main:app --host 0.0.0.0 --port 8080
```

### 4. 启动 MCP Server

```bash
export SEARCH_SERVICE_URL=http://localhost:8080
python -m inner_sdk_search_mcp.server
```

---

## MCP 工具清单

| 工具 | 功能 |
|------|------|
| `search_private_knowledge` | 混合检索 + auto_assemble + construction_guide |
| `get_entity_detail` | 类级/方法级精确查询（类名查询返回所有方法签名） |
| `get_applicable_spec` | 获取模块适用的规范契约 |
| `recommend_context` | 项目上下文预加载 |
| `report_feedback` | 知识反馈上报 |
| `assemble_prompt` | 清洗→去重→分组→4 段式 Prompt 组装 |

---

## 检索排序策略

ranker 五级加权（均无条件生效）：

| 级别 | 权重示例 | 说明 |
|------|----------|------|
| Type boost | api(1.0) > spec(0.95) > term(0.80) | 知识类型 |
| Role boost | entry_point(1.0) > public_api(0.80) > internal(0.55) | SDK 角色 |
| Layer boost | high(1.50) > mid(1.0) > low(0.55) > suppressed(0.10) | 方法层级 |
| Gateway boost | entry_point + return_type → ×1.15 | 入口网关 |
| Construction boost | builder/static_factory → ×1.25 | 构造方法排最前 |

公式：`final = score × type × role × layer × (gateway) × (construction) × keyword`

---

## 配置项

### Search Service

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKEND_MODE` | `lightweight` | `lightweight` / `production` |
| `SQLITE_DB_PATH` | `./data/knowledge.db` | |
| `FAISS_INDEX_DIR` | `./data/faiss` | |
| `GRAPH_STORAGE_PATH` | `./data/graph.json` | |
| `SEARCH_TIMEOUT_MS` | `1000` | 单路检索超时 |
| `LOG_LEVEL` | `INFO` | 全局日志级别 |
| `EMBEDDING_MODEL_PATH` | `./models` | 本地 embedding 模型路径 |

### MCP Server

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SEARCH_SERVICE_URL` | (空) | 未设置使用 Mock |
| `PROMPT_DEFAULT_MAX_TOKENS` | `4096` | |
| `METRICS_ENABLED` | `true` | 工具调用埋点 |
| `METRICS_LOG_DIR` | `./logs/metrics` | |

---

## Pipeline 配置体系

### 规则（merge 策略）

`pipeline/config/default_rules.json` ← `.sdk-rules.json` (项目级) ← `.sdk-rules.json` (模块级)

可配置：子包分类、入口类后缀、层级关键词、复杂参数类型、文件过滤。

### 标注（override 策略）

`pipeline/config/default_annotations.json` ← `.sdk-annotations.json` (项目级) ← `.sdk-annotations.json` (模块级)

可配置：`entry_points`、`suppressed.packages`、`suppressed.classes`。

---

## 语言支持

| 语言 | adapter | 文件扩展名 | role/layer |
|------|---------|-----------|------------|
| Java | `adapters/java.py` | `.java` | ✅ 完整 |
| Python | `adapters/python.py` | `.py` | `public_api`/`mid` |
| TypeScript | `adapters/typescript.py` | `.ts` | `public_api`/`mid` |

---

## 日志

- `logs/search_service.log` — Search Service 全部模块
- `logs/access.log` — HTTP 请求日志
- `logs/metrics/` — MCP 工具调用 JSONL 埋点
