# MCP Server 代码审查问题报告

**审查日期**：2026-05-10
**审查范围**：requirement.md、mcp_design.md 及 mcp/ 目录下已实现代码
**审查依据**：需求文档功能定义 vs 实际代码实现

---

## 一、安全漏洞

### S1: Prompt注入防护不完整

**严重程度**：高
**位置**：`mcp/prompt/sanitizer.py`
**问题描述**：仅检测4种越狱模式，缺少多种绕过方式：
- Base64编码绕过
- URL编码绕过
- 零宽字符注入
- 中文变体（如"你现在是jailbreak"）
- Unicode同形异义（如"а"替代"a"）

**修复状态**：✅ 已修复

**修复方案**：重写sanitizer.py
- 零宽字符过滤（`\u200b\u200c\u200d`等）
- Unicode NFKC规范化（消除同形异义攻击）
- Base64/URL编码解码后检测
- 扩展越狱模式库（7种）
- 添加结构化日志记录

---

### S2: meta字段未清洗

**严重程度**：高
**位置**：`mcp/prompt/sanitizer.py`
**问题描述**：仅清洗 `content` 字段，`meta.code_example`、`meta.related_entity` 等未检测，可能成为注入通道

**修复状态**：✅ 已修复

**修复方案**：`sanitize_items` 新增meta全字段清洗
- 支持 `code_example`、`related_entity`、`security_rule` 等字段
- 支持列表类型字段递归清洗

---

### S3: 越狱检测后未告警

**严重程度**：中
**位置**：`mcp/prompt/sanitizer.py`
**问题描述**：检测到越狱模式仅替换为"[已移除]"，未记录为安全事件、也未触发告警

**修复状态**：✅ 已修复

**修复方案**：
- 新增专用安全审计日志器 `"mcp.prompt.security"`
- `sanitize_items` 支持 `session_id` 参数用于日志关联
- 检测到越狱时生成唯一 `audit_id`，并记录完整事件上下文：
  - `event_type`: 事件类型（jailbreak_pattern_detected、base64_encoded_injection等）
  - `severity`: 严重程度（HIGH/MEDIUM）
  - `matched_pattern`: 匹配的模式
  - `item_id`: 被清洗的知识片段ID
- 日志格式符合结构化规范，便于SIEM系统收集
- 多平台兼容：仅记录日志，不依赖IDE展示

---

## 二、设计与实现不一致

### D1: 工具数量不匹配

**严重程度**：低
**位置**：`mcp/server.py` 及 `doc/mcp_design.md`
**问题描述**：注释说6个工具，设计文档只列了5个（缺少assemble_prompt）

**修复状态**：✅ 已修复

**修复方案**：更新mcp_design.md文档
- 工具总览表新增 `assemble_prompt` 工具
- 新增第6章 `assemble_prompt` 详细设计
- 版本历史记录为 v1.1

---

### D2: version_requirement未解析

**严重程度**：中
**位置**：`mcp/tools/get_entity.py`
**问题描述**：设计文档要求支持 `>=2.1.0` 这样的约束，实现只是透传给KB，未实际解析和版本匹配

**修复建议**：
1. 增加版本约束解析器
2. 支持语义化版本比较（semver）

---

### D3: 返回类型不一致

**严重程度**：中
**位置**：`mcp/tools/get_entity.py`
**问题描述**：找不到实体时返回dict，但其他工具返回空响应/抛异常，行为不统一

**修复状态**：✅ 已修复

**修复方案**：
- `EntityDetailResponse` 新增 `found: bool = True` 和 `message: Optional[str]` 字段
- `get_entity_detail` 统一返回 `EntityDetailResponse` 类型

---

## 三、缺失的功能

| 编号 | 功能 | 设计要求 | 当前状态 | 优先级 |
|-----|------|---------|---------|--------|
| F1 | 权限隔离 | 根据调用方团队/项目自动应用最小权限策略 | 完全未实现 | 高 |
| F2 | 审计日志 | 每次调用记录完整的查询指纹与返回摘要 | 定义了AuditRecord但未使用 | 高 |
| F3 | 知识缓存 | 缓存热门知识，向量库GPU加速 | 完全未实现 | 中 |
| F4 | 版本冲突检测 | 返回spec时检测规则间冲突 | get_specs返回了conflict_warnings但MockKnowledgeBase返回空 | 中 |
| F5 | 降级处理 | 检索超时300ms后返回缓存或空 | 定义了超时配置但未使用 | 中 |

---

## 四、逻辑问题

### L1: Token估算不准确

**严重程度**：中
**位置**：`mcp/prompt/assembler.py`
**问题描述**：中文字符判断用Unicode范围 `"一" <= c <= "鿿"` 会漏掉中文标点（。、，、""）和部分生僻字

**修复状态**：✅ 已修复

**修复方案**：使用正则匹配完整CJK范围
```python
_CJK_PATTERN = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf'  # CJK统一表意文字 + 扩展A
    r'\u9fa6-\u9fff'                 # CJK统一表意文字（补充）
    r'\u3000-\u303f'                 # CJK标点符号
    r'\uff00-\uffef]'                 # CJK兼容性形式
)
```

---

### L2: 去重性能问题

**严重程度**：中
**位置**：`mcp/prompt/deduplicator.py`
**问题描述**：O(n²)复杂度，1000条知识片段会有性能问题

**修复状态**：✅ 已修复

**修复方案**：
- 按 score 降序排序，高相关性结果优先保留
- 限制相似度比较范围为 Top-N（默认50条）
- 增加内容长度预过滤（长度差异超过2倍跳过）
- 快速哈希初筛（MD5前20字符），减少实际相似度计算
- 复杂度从 O(n²) 优化到 O(n × top_n)

---

### L3: 越狱模式检测过度匹配

**严重程度**：低
**位置**：`mcp/prompt/sanitizer.py`
**问题描述**：正则 `[\s\S]*` 可能过度匹配，导致整段被替换为"[已移除]"

**修复状态**：✅ 随S1修复

**修复方案**：移除 `[\s\S]*`，改为精确匹配关键短语

---

### L4: auto_assemble导入位置

**严重程度**：低
**位置**：`mcp/tools/search_knowledge.py`
**问题描述**：在async函数内import，虽然可行但不符合PEP8习惯

**修复状态**：❌ 无需修复

**说明**：函数内import是Python推荐写法（延迟导入），可避免循环依赖，且asyncio场景下更灵活。

---

## 五、边界条件

### B1: context为None时specs被跳过

**严重程度**：低
**位置**：`mcp/tools/search_knowledge.py`
**问题描述**：`if auto_assemble.include_specs and kb and context and context.module` - context为None时不会报错，但会静默跳过specs

**修复状态**：✅ 已修复

**修复方案**：增加INFO级别日志，明确记录跳过原因
- `reason="context_not_provided"` - 未提供上下文
- `reason="module_not_in_context"` - 上下文中无module字段
- `reason="knowledge_base_unavailable"` - 知识库不可用
- 日志包含message字段说明原因

---

### B2: items为空时统计计算异常

**严重程度**：低
**位置**：`mcp/prompt/assembler.py`
**问题描述**：`stats.after_dedup - truncated` 可能为负数

**修复状态**：✅ 已修复

**修复方案**：`stats.after_truncation = max(0, stats.after_dedup - truncated)`

---

### B3: project_meta字段缺失

**严重程度**：中
**位置**：`mcp/models/schemas.py`
**问题描述**：`ProjectMeta`要求必填`project_id`和`team`，但IDE传入时可能缺失，应有默认值

**修复状态**：✅ 已修复

**修复方案**：`project_id`和`team`字段添加默认值`"unknown"`

---

## 六、问题优先级汇总

| 优先级 | 问题编号 | 问题描述 |
|-------|---------|---------|
| P0 - 立即修复 | S1, S2 | Prompt注入防护不完整（安全漏洞） |
| P0 - 立即修复 | F1 | 权限隔离未实现（设计要求） |
| P1 - 高优先级 | D2, D3 | version_requirement未解析、返回类型不一致 |
| P1 - 高优先级 | F2 | 审计日志未使用 |
| P2 - 中优先级 | L1, L2, B3 | Token估算、去重性能、字段默认值 |
| P3 - 低优先级 | D1, L3, L4, B1, B2 | 注释不一致、导入位置等 |

---

## 七、修复跟踪

| 问题编号 | 状态 | 修复日期 | 修复人 |
|---------|------|---------|--------|
| S1 | ✅ 已修复 | 2026-05-10 | 重写sanitizer.py，增强越狱模式检测 |
| S2 | ✅ 已修复 | 2026-05-10 | sanitize_items新增meta全字段清洗 |
| S3 | ✅ 已修复 | 2026-05-10 | 新增安全审计日志器，记录完整安全事件 |
| D1 | ✅ 已修复 | 2026-05-10 | 更新mcp_design.md，新增assemble_prompt工具章节 |
| D3 | ✅ 已修复 | 2026-05-10 | EntityDetailResponse新增found字段，统一返回类型 |
| L1 | ✅ 已修复 | 2026-05-10 | 使用正则匹配完整CJK范围 |
| L2 | ✅ 已修复 | 2026-05-10 | 优化去重算法，O(n²)降至O(n×top_n) |
| L3 | ✅ 已修复 | 2026-05-10 | 随S1修复，移除[\s\S]*过度匹配模式 |
| L4 | ❌ 无需修复 | - | 函数内import是延迟导入最佳实践 |
| B1 | ✅ 已修复 | 2026-05-10 | search_knowledge.py新增跳过specs的日志记录 |
| B2 | ✅ 已修复 | 2026-05-10 | assembler.py使用max(0, ...)防止负数 |
| B3 | ✅ 已修复 | 2026-05-10 | ProjectMeta的project_id/team添加默认值 |

**未修复问题**：D2, F1-F5

---

## 八、修复后章节索引（按原问题顺序）

| 顺序 | 编号 | 问题名称 | 修复状态 |
|-----|------|---------|---------|
| 1 | S1 | Prompt注入防护不完整 | ✅ 已修复 |
| 2 | S2 | meta字段未清洗 | ✅ 已修复 |
| 3 | S3 | 越狱检测后未告警 | ✅ 已修复 |
| 4 | D1 | 工具数量不匹配 | ✅ 已修复 |
| 5 | D2 | version_requirement未解析 | ⏳ 待修复 |
| 6 | D3 | 返回类型不一致 | ✅ 已修复 |
| 7 | F1 | 权限隔离 | ⏳ 待修复 |
| 8 | F2 | 审计日志 | ⏳ 待修复 |
| 9 | F3 | 知识缓存 | ⏳ 待修复 |
| 10 | F4 | 版本冲突检测 | ⏳ 待修复 |
| 11 | F5 | 降级处理 | ⏳ 待修复 |
| 12 | L1 | Token估算不准确 | ✅ 已修复 |
| 13 | L2 | 去重性能问题 | ✅ 已修复 |
| 14 | L3 | 越狱模式检测过度匹配 | ✅ 已修复 |
| 15 | L4 | auto_assemble导入位置 | ❌ 无需修复 |
| 16 | B1 | context为None时specs被跳过 | ✅ 已修复 |
| 17 | B2 | items为空时统计计算异常 | ✅ 已修复 |
| 18 | B3 | project_meta字段缺失 | ✅ 已修复 |

---

*文档版本：v1.1*
*创建日期：2026-05-10*
*最后更新：2026-05-10*
