# 私域知识 MCP Server —— 部署与接入指南

## 版本历史
| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-05-11 | 初始版本：分机部署流程 + OpenCode 接入配置 |

---

## 1. 架构与部署拓扑

```
┌─ 开发者本地机器 ─────────────────────┐      ┌─ 团队服务器 ──────────┐
│                                      │      │                       │
│  OpenCode IDE                        │      │  Search Service       │
│       │                              │      │  (FastAPI :8080)      │
│       ▼                              │      │       │               │
│  MCP Server (inner_sdk_search_mcp, stdio)          │ HTTP │       ├─ SQLite/FAISS │
│  配置: SEARCH_SERVICE_URL=server:8080 │─────→│       ├─ (or ES)     │
│                                      │      │       └─ (or Milvus)  │
└──────────────────────────────────────┘      └───────────────────────┘
```

- **Search Service**：部署在团队服务器上，承载混合检索 + 知识库数据
- **MCP Server**：安装在每个开发者的本地机器上，通过 HTTP 调用 Search Service
- MCP Server 与 Search Service 之间没有内网要求，只要 HTTP 可达即可

---

## 2. 前提条件

### 开发者本地机器

| 条件 | 要求 |
|------|------|
| Python | 3.10+ |
| pip | 25.0+ |
| OpenCode（或其他支持 MCP 的 IDE） | 最新版 |
| 网络 | 能访问 Search Service 的 HTTP 端口 |

### 团队服务器

| 条件 | 要求 |
|------|------|
| Python | 3.10+ |
| 端口 | 8080（可自定义） |
| 数据 | 已完成 pipeline 导入的知识库（`data/` 目录） |
| 可选 | Docker（生产模式 ES + Milvus + Neo4j） |

---

## 3. Search Service 部署（服务器端）

### 3.1 获取代码

```bash
git clone <repo_url>
cd private_domain_knowledge_search
```

### 3.2 安装依赖

```bash
pip install -r search_service/requirements.txt
```

### 3.3 导入知识库数据

```bash
# 将你的企业知识数据放在 data_source/ 目录下
# 按照推荐结构组织：
#   data_source/api/          REST API 文档（.md / .json）
#   data_source/sdk/          SDK 源码（Java Maven 项目）
#   data_source/spec/         Spec 契约文件
#   data_source/best_practice/ 最佳实践文档

# 运行 pipeline 导入
python -m search_service.pipeline.orchestrator data_source/

# 输出示例:
#   loaded 15 doc files
#   chunked into 42 doc chunks
#   parsed 18 java method chunks
#   bm25 indexed: 60
#   vector indexed: 60
```

### 3.4 启动服务

```bash
# 轻量模式（默认，免外部服务，SQLite + FAISS + NetworkX）
uvicorn search_service.main:app --host 0.0.0.0 --port 8080

# 生产模式（需先 docker compose up 启动 ES + Milvus + Neo4j）
BACKEND_MODE=production uvicorn search_service.main:app --host 0.0.0.0 --port 8080
```

### 3.5 验证服务可用

```bash
# 在服务器本地或开发者机器上执行
curl http://<server_ip>:8080/health

# 预期输出: {"status":"ok","es":true,"milvus":true,"neo4j":true}
# 轻量模式下 es/milvus/neo4j 为 false，status 为 degraded

# 搜索测试
curl -X POST http://<server_ip>:8080/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"FileManager upload","top_k":3}'
```

---

## 4. MCP Server 安装（开发者本地机器）

### 4.1 方式一：pip 安装（推荐）

```bash
# 从代码仓库安装
cd private_domain_knowledge_search
pip install -e .

# 或指定 Search Service 地址后安装
SEARCH_SERVICE_URL=http://<server_ip>:8080 pip install -e .
```

安装后可通过 `inner-sdk-search-mcp` 命令直接启动。

### 4.2 方式二：直接运行（开发验证用）

```bash
cd private_domain_knowledge_search
pip install -r pyproject.toml
python -m inner_sdk_search_mcp.server
```

### 4.3 配置 Search Service 地址

通过环境变量配置，支持三种方式：

```bash
# 方式 A：环境变量（推荐）
export SEARCH_SERVICE_URL=http://192.168.1.100:8080

# 方式 B：在 OpenCode MCP 配置中设置（见第 5 节）

# 方式 C：.env 文件（项目根目录）
echo 'SEARCH_SERVICE_URL=http://192.168.1.100:8080' > .env
```

不设置 `SEARCH_SERVICE_URL` 时，MCP Server 使用 Mock 实现（返回空结果），不发起远程调用。

---

## 5. OpenCode 接入配置

### 5.1 MCP 配置文件位置

OpenCode 的 MCP 配置文件位于：

| 系统 | 路径 |
|------|------|
| Windows | `%USERPROFILE%\.opencode\mcp.json` |
| macOS | `~/.opencode/mcp.json` |
| Linux | `~/.opencode/mcp.json` |

### 5.2 配置内容

```json
{
  "mcpServers": {
    "private-knowledge": {
      "command": "python",
      "args": ["-m", "inner_sdk_search_mcp.server"],
      "cwd": "/path/to/private_domain_knowledge_search",
      "env": {
        "SEARCH_SERVICE_URL": "http://192.168.1.100:8080"
      }
    }
  }
}
```

如果已通过 `pip install -e .` 安装，可使用入口点：

```json
{
  "mcpServers": {
    "private-knowledge": {
      "command": "inner-sdk-search-mcp",
      "env": {
        "SEARCH_SERVICE_URL": "http://192.168.1.100:8080"
      }
    }
  }
}
```

### 5.3 配置项说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `command` | 是 | `python` 或 `inner-sdk-search-mcp`（pip 安装后） |
| `args` | 否 | Python 模块参数（仅 `python` 模式需要） |
| `cwd` | 否 | 工作目录，仅在直接运行时需要 |
| `env.SEARCH_SERVICE_URL` | 推荐 | Search Service 地址 |
| `env.BACKEND_MODE` | 否 | `lightweight`（默认）或 `production` |

---

## 6. 验证端到端

### 6.1 确认 MCP Server 正常启动

在 OpenCode 中打开 MCP 面板，应看到 `private-knowledge` 显示为 Connected 状态，以及 6 个可用工具：

- `search_private_knowledge`
- `get_entity_detail`
- `get_applicable_spec`
- `recommend_context`
- `report_feedback`
- `assemble_prompt`

### 6.2 验证远程检索

在 OpenCode 中输入测试问题：

> com-ext-file SDK 中的 FileManager 工具类有哪些方法？

预期：MCP Server 调用远程 Search Service，返回该 SDK 的 public 方法签名列表。

### 6.3 验证 auto_assemble

> 实现文件上传功能，先判断目录是否存在，不存在则创建

预期：返回 assembled prompt，包含 SDK 方法签名 + 约束条件。

---

## 7. 常见问题

### Q: OpenCode 显示 MCP Server 未连接

1. 检查 Python 版本 >= 3.10
2. 检查依赖已安装：`pip list | grep -E "mcp|pydantic|httpx"`
3. 手动运行测试：`python -m inner_sdk_search_mcp.server`（应监听 stdio 无报错）

### Q: 工具返回空结果

1. 检查 `SEARCH_SERVICE_URL` 是否正确
2. 确认 Search Service 可通过网络访问：`curl http://<server_ip>:8080/health`
3. 检查 Server 端知识库是否已导入：`ls data/knowledge.db`
4. 未设置 `SEARCH_SERVICE_URL` 将使用 Mock（返回空结果）

### Q: 服务器防火墙问题

- Search Service 需要开放 8080 端口（或自定义端口）
- MCP Server → Search Service 是单向 HTTP 请求，无需双向网络

### Q: 如何更新知识库数据

1. 在服务器端更新 `data_source/` 目录
2. 重新运行 pipeline 导入（全量覆写）
3. 新数据即时生效，无需重启 Search Service

---

## 8. 安全建议

- Search Service 部署在内网，不暴露到公网
- 生产环境加 HTTPS + 基础认证或 JWT 验证
- 当前版本无权限隔离，所有接入 IDE 共享同一知识库
- `SEARCH_SERVICE_URL` 中不要包含认证信息（后续版本支持 header 注入）
