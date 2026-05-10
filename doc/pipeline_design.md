# 数据预处理管道 —— 详细设计文档

## 版本历史
| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-05-10 | 初始设计：文档加载分块 + Java AST 解析 + 轻量索引 |
| v1.1 | 2026-05-10 | chunk 新增 knowledge_source 字段（doc/sdk_code），支持检索结果按来源分组 |

---

## 1. 模块定位

数据预处理管道负责将企业私域数据（设计文档、SDK 源码）转化为可检索的索引，供 Search Service 的混合检索引擎消费。

```
企业数据
  ├── Markdown 文档 (.md)      ──→  loader → chunker ─┐
  └── Java SDK 源码            ──→  code_parser ───────┤
                                                        ↓
                                                  orchestrator
                                                        ↓
                                               BM25 + FAISS + Graph
```

---

## 2. 项目结构

```
search_service/pipeline/
├── __init__.py
├── loader.py          # 文件加载器（glob 模式匹配）
├── chunker.py         # 文档分块器（语义边界 + 重叠窗口）
├── code_parser.py     # Java SDK AST 解析器（pom.xml + javalang）
└── orchestrator.py    # 编排入口：加载 → 分块 → 解析 → 索引
```

---

## 3. 核心组件

### 3.1 loader.py —— 文件加载器

**职责**：按 glob 模式从目录加载文件内容。

**函数**：`load_files(root_dir, patterns)` → `[{path, filename, content}]`

**默认模式**：`["**/*.md", "**/*.py", "**/*.go"]`。支持扩展名推断知识类型。

**实现要点**：
- 使用 `pathlib.Path.glob()` 递归匹配
- UTF-8 编码读取，失败则跳过
- 返回相对路径和原始文本内容，不做任何分块

### 3.2 chunker.py —— 文档分块器

**职责**：将长文档按语义边界切分为目标大小 (3K-5K tokens) 的片段。

**函数**：`chunk_text(text, source_path, title, chunk_type, module)` → `[{id, type, content, ...}]`

**分块策略**：
1. 按空行切分为段落（段落是语义基本单元）
2. 段落合并至 3000-10000 字符范围（约 1500-5000 tokens）
3. 块间保留约 300 token 的重叠窗口（避免边界切断上下文）
4. 输出统一格式的 chunk 字典，可直接喂给索引器

**Token 估算**：中文按 1.5 char/token，英文按 2.5 char/token。

**chunk 结构**：
```python
{
    "id": "0932a6a4c60f",          # MD5(source_path:index)
    "type": "document",             # knowledge type
    "content": "# 私域知识...",     # 分块文本
    "title": "requirement",         # 源文件名（无扩展名）
    "module": "doc",                # 从路径推断的模块名
    "source_path": "doc/requirement.md",
    "tokens": 1800,                 # 估算 token 数
    "meta_json": '{"knowledge_source": "doc", "source": "doc/requirement.md", ...}'
}
```

**`knowledge_source` 字段**（v1.1）：标记数据来源，用于检索结果的分组展示。

| 值 | 含义 | 写入方 |
|----|------|--------|
| `"doc"` | 文档（Markdown / 设计文档 / API 文档） | chunker.py |
| `"sdk_code"` | SDK 源码（Java / Python / Go） | code_parser.py |

### 3.3 code_parser.py —— Java AST 解析器

**职责**：解析 Java SDK 源码仓库，提取 pom.xml 坐标和所有 public 方法签名。

**入口函数**：`parse_java_repo(repo_dir)` → `[chunk, ...]`

**处理流水线**：

```
repo_dir/
  ├── pom.xml              ──→  extract_pom_info()         → Maven 坐标
  ├── .sdk-versions.json   ──→  _load_version_overrides() → 版本覆盖表
  └── src/**/*.java        ──→  parse_java_file()         → 方法 chunk
                                     │
                                     ├── javalang AST 解析
                                     ├── 仅 public 方法 + 构造函数
                                     ├── _extract_javadocs() 正则提取注释
                                     └── _extract_calls() 调用链
```

**输出 chunk 示例**：
```json
{
  "id": "15d19109473f",
  "type": "api",
  "content": "public String deduct(String bizId, String uid, int points)\n\n积分扣减\n@param bizId 业务幂等键",
  "title": "PointsClient.deduct",
  "module": "points-sdk",
  "source_path": "src/main/java/com/example/sdk/PointsClient.java",
  "meta_json": "{\"knowledge_source\": \"sdk_code\", \"sdk\": \"points-sdk\", \"version\": \"2.3.0\", \"class_name\": \"com.example.sdk.PointsClient\", \"method\": \"deduct\", \"return_type\": \"String\", \"calls\": [\"validateParams\"]}"
}
```

**关键字段**：

| meta 字段 | 来源 | 说明 |
|-----------|------|------|
| `knowledge_source` | chunker / code_parser | 数据来源：`doc` 或 `sdk_code` |
| `sdk` | pom.xml `<artifactId>` | SDK 标识 |
| `version` | 版本解析链（见 3.3.1） | 真实发布版本 |
| `class_name` | `package.OuterClass.InnerClass` | 完整 import 路径 |
| `method` | AST `MethodDeclaration.name` | 方法名 |
| `return_type` | AST 返回类型 | void / String / int... |
| `calls` | AST `MethodInvocation` | 方法体内调用的方法名列表 |

#### 3.3.1 版本解析链

解决 pom.xml 中占位符版本（SNAPSHOT / `${revision}` / 空）无法作为真实版本的问题。

```
.sdk-versions.json（权威覆盖）
     ↓ 未命中
pom.xml 字面量（非占位符时采用）
     ↓ 占位符
空字符串（兜底，检索时不按版本过滤）
```

**`.sdk-versions.json` 格式**：
```json
{
  "points-sdk": "2.3.0",
  "snapshot-lib": "2.5.0"
}
```

**占位符检测**：正则匹配 `${.*}` / `.*-SNAPSHOT` / 空字符串，命中后记录 INFO 日志并降级为空。

**兜底行为**：版本为空字符串时，BM25 检索不施加版本过滤——任何匹配的方法签名都返回。

#### 3.3.2 Javadoc 提取

在 javalang 解析前用正则预提取 `/** ... */` 注释块，按紧邻 public 方法的行号关联。清洗后的文本附加到 chunk.content 中，使方法签名 + 注释可同时被全文检索命中。

### 3.4 orchestrator.py —— 编排器

**职责**：串联全部预处理步骤，输出到三个轻量后端。

**入口函数**：`run(data_dir, patterns)` → `dict(stats)`

**流水线**：

```
1. load_files(data_dir, patterns)   → 文档文件列表
2. chunk_text() × N                 → 文档 chunk 列表
3. parse_java_repo(data_dir)        → Java 方法 chunk 列表（自动发现 pom.xml）
4. LiteBM25Searcher.index()         → 写入 SQLite FTS5
5. embedding.embed() + FAISS        → 写入 FAISS 索引
6. LiteGraphSearcher.add_entity/relation() → 写入 NetworkX 图
```

**输出统计**：
```json
{
  "doc_files": 7,
  "doc_chunks": 9,
  "java_chunks": 4,
  "total_chunks": 13,
  "indexed_bm25": 13,
  "indexed_vector": 13,
  "graph_nodes": 13
}
```

**运行方式**：
```bash
# 摄入当前项目文档 + Java SDK 源码
python -m search_service.pipeline.orchestrator .

# 指定数据目录和文件模式
python -m search_service.pipeline.orchestrator /path/to/data

# 编程调用
from search_service.pipeline.orchestrator import run
stats = run("/path/to/repo", patterns=["doc/**/*.md"])
```

---

## 4. 数据流与接口约定

所有组件通过统一的 chunk 字典格式传递数据：

```python
chunk = {
    "id": str,          # 唯一标识（MD5）
    "type": str,        # document | api | best_practice ...
    "content": str,     # 可检索的文本内容（签名 + Javadoc / 分块文本）
    "title": str,       # 展示标题（文件名 / ClassName.method）
    "module": str,      # 所属模块（目录名 / artifact_id）
    "source_path": str, # 源文件相对路径
    "meta_json": str,   # JSON，存储结构化元数据（含 knowledge_source、版本、类名等）
}
```

chunk 字典可直接喂给 `LiteBM25Searcher.index_documents()`（写入 SQLite）、`LiteVectorSearcher.index_vectors()`（写入 FAISS）、`LiteGraphSearcher.add_entity()`（写入 NetworkX）。

---

## 5. 配置项

所有路径通过 `search_service/config.py` 的 `ServiceConfig` 管理：

| 环境变量 | 默认值 | 使用方 |
|----------|--------|--------|
| `BACKEND_MODE` | `lightweight` | factory |
| `SQLITE_DB_PATH` | `./data/knowledge.db` | lite/bm25.py |
| `FAISS_INDEX_DIR` | `./data/faiss` | lite/vector.py |
| `GRAPH_STORAGE_PATH` | `./data/graph.json` | lite/graph.py |

---

## 6. 扩展点

### 6.1 新增文件类型支持

在 `loader.py` 的 `patterns` 参数中添加 glob 模式，在 `orchestrator.py` 的类型推断逻辑中添加扩展名判断。

### 6.2 新增语言 AST 解析

参照 `code_parser.py` 的模式：
1. 读取构建文件提取包名/版本（Python 读 `setup.py` / `pyproject.toml`）
2. 用对应语言的 AST 库解析源码（Python 用 `ast` 标准库）
3. 输出统一 chunk 格式
4. 在 `orchestrator.py` 中调用

### 6.3 对接生产后端

将 `BACKEND_MODE` 设为 `production`，pipeline 通过 factory 创建的将是 ES/Milvus/Neo4j 实例。但 `orchestrator.py` 当前直接实例化 Lite* 类——这是已知限制（见 review LOW 优先级），生产模式下需调整。

### 6.4 增量更新

当前 orchestrator 每次全量覆写。增量更新需：
1. 追踪 source_path → chunk_id 的映射
2. 对比文件修改时间，仅重处理变更文件
3. 删除已移除文件对应的旧 chunk
