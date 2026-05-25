# 私域知识赋能AI Coding工具 —— PPT大纲

---

## 第一部分：项目背景与整体思路（约 3-4 页）

### P1. 封面
- 标题：私域知识赋能AI Coding工具 —— 技术方案与实践
- 副标题：从通用大模型到业务专家型 Copilot

### P2. 痛点分析
- 通用 AI 编程模型缺乏对企业内部术语、私有 SDK、历史技术决策的理解
- 开发者需多次"调教"才能获得可用代码，返工率高（常见需 5 轮以上修改）
- 63% 开发者反馈现有工具存在"项目级理解缺失"
- **核心矛盾**：通用大模型能力强，但对企业私域知识一无所知

### P3. 建设目标
- 构建以**私域知识工程**为核心的技术体系
- AI 自动理解企业术语、API、业务规范与历史经验
- 覆盖三大场景：**需求开发 / 问题定位 / 代码检视**
- 通过**运营闭环**持续积累与优化知识
- 一句话总结：让 AI 从"通用码农"升级为"业务专家型 Copilot"

### P4. 核心设计思想：Spec + RAG 双引擎
- **Spec 引擎**（硬约束）：将接口契约、数据契约、行为规范作为结构化约束，生成前校验
- **RAG 引擎**（软智能）：语义检索动态注入非结构化知识（历史方案、API 文档、最佳实践）
- 两者互补：Spec 保证合规底线，RAG 提供上下文智能

---

## 第二部分：总体架构（约 4-5 页）

### P5. 五层架构总览
```
数据源层 → 知识加工层 → 知识存储层 → 检索与生成层 → AI Coding工具
                                                      ↑
                                               运营反馈闭环
```
- 数据源层：业务需求文档、API/SDK 文档、代码仓库、历史工单
- 知识加工层：文档解析分块、AST 分析、语义向量化、知识图谱构建
- 知识存储层：向量数据库 + 全文索引 + 图数据库 + 结构化知识库
- 检索与生成层：混合检索 → MCP 协议层 → 上下文组装 → Prompt 组装
- 反馈闭环：会话记录 → 质量评估 → 人工审核 → 反哺知识加工

### P6. 数据流转全景图
- 展示从数据源到最终生成代码的完整链路
- 重点标注关键节点：混合检索、MCP Server、Prompt 组装

### P7. 部署拓扑
- 开发者本地：OpenCode IDE + MCP Server（每 IDE 一个进程，stdio 通信）
- 团队服务器：Search Service（FastAPI 微服务，团队共享）
- 双模式支持：轻量模式（SQLite/FAISS/NetworkX）vs 生产模式（ES/Milvus/Neo4j）

### P8. 知识分类体系
| 知识类别 | 内容构成 | 示例 |
|----------|----------|------|
| 术语/业务词汇库 | 术语名、定义、同义词、关联术语 | 幂等键、业务流水号 |
| SDK/API 文档库 | 接口签名、参数、返回值、示例代码 | PointsClient.deduct() |
| 代码规范与最佳实践 | 命名规则、异常处理、性能优化 | 支付回调必须实现幂等 |
| 历史上下文库 | 缺陷记录、架构评审、技术决策 | INC-4829 积分少扣 |

---

## 第三部分：技术方案详解（约 6-7 页）

### P9. 混合检索引擎 —— 三路并行检索
- **BM25 全文检索**（SQLite FTS5 / Elasticsearch）：关键词精确匹配，OR 语义提升召回
- **向量语义检索**（FAISS / Milvus）：bge-large-zh Embedding，语义相似度匹配
- **图遍历检索**（NetworkX / Neo4j）：实体关联关系，代码调用链分析
- 三路并行（asyncio.gather），各路由 _safe_search 超时保护（300ms）

### P10. 融合排序策略
- 合并去重：按 ID 去重，保留最高分
- 三级加权重排序：
  - **类型 boost**：api(1.0) > best_practice(0.95) > document(0.90) > security_rule(0.85) > term(0.80)
  - **SDK 角色 boost**：entry_point(1.0) > public_api(0.92) > internal(0.80)
  - **关键词命中加成**：每个命中 +0.02
- 结果集内相对归一化，min_score 过滤 + Top-K 截断

### P11. MCP 工具矩阵（6 大工具）
| 工具 | 用途 | 方向 |
|------|------|------|
| search_private_knowledge | 通用混合检索 | IDE→MCP |
| get_entity_detail | 精确查询实体定义 | IDE→MCP |
| get_applicable_spec | 获取适用规范契约 | IDE→MCP |
| recommend_context | 项目上下文预加载 | IDE→MCP |
| report_feedback | 知识反馈上报 | IDE→MCP |
| assemble_prompt | 清洗、去重、组装 Prompt | IDE→MCP |

### P12. Prompt 组装流水线
```
输入 → 安全清洗 → 去重 → SDK分组(按返回值链) → 文档分组 → Token裁剪 → 4段式组装 → 输出
```
- **安全清洗**：零宽字符过滤、越狱模式检测、Base64/代码注入防护、meta 深度递归清洗
- **去重**：ID 精确 + 内容相似度(>0.92) + MD5 快速预检
- **来源分组**：SDK 源码（方法签名子节） vs 文档（参考文档子节）
- **4 段式结构**：System(角色+规范) → Background(方法签名+参考文档) → User Request → Constraints

### P13. SDK 角色体系与返回值类型链
- 三级角色推断（6 条分层规则）：
  - `entry_point`：根包/service 子包 + Factory/Manager/Client 后缀或静态工厂方法
  - `public_api`：service/api/client 子包
  - `internal`：dao/config/impl 子包，或纯 getter/setter 类
- 返回值类型链分组：entry_point 方法绑定其返回值接口的方法，正向（入口→子方法）+ 反向（"获取此实例"提示）
- 条件入口提示：有 entry_point 类时注入 SDK 使用提示

### P14. 三大应用场景工作流
- **需求开发**（已测试通过）：检索关联 API + 历史方案 + 规范 → 生成符合企业规范的代码骨架
- **问题定位**（待验证）：异常堆栈匹配历史缺陷 → 拉取最新变更 → 生成排查建议
- **代码检视**（待验证）：拉取 Spec 规范 → 解析代码差异 → 标记违规并建议修复

### P15. 数据预处理管道
- loader → classifier → chunker → code_parser → orchestrator → 三路索引
- **文档分块**：语义边界识别 + 重叠窗口，单块 3K-10K 字符
- **Java AST 解析**：提取 Maven 坐标、public 方法/构造函数、Javadoc、调用链、SDK 角色
- **版本解析链**：.sdk-versions.json → pom.xml 字面量 → 空字符串兜底

---

## 第四部分：调试问题与解决策略（约 6-7 页）

### P16. 问题总览
共 6 个关键问题，分为两类：

**Pipeline/引擎层（来自 5 号文档）：**

| # | 问题 | 影响 | 严重程度 |
|---|------|------|----------|
| 1 | FTS5 多词查询命中率极低 | 搜索结果几乎全部被过滤 | 严重 |
| 2 | test 目录代码污染 | 94% 索引数据是测试代码（fastjson） | 中等 |
| 3 | SDK 角色推断 bug：class_path 误用 | 子包检测失效，角色标注不准 | 中等 |

**MCP/工具层（来自 3 号文档）：**

| # | 问题 | 影响 | 严重程度 |
|---|------|------|----------|
| 4 | LLM 不主动调用 MCP 工具 | 用户问内部 SDK 但 LLM 不走私域检索 | 严重 |
| 5 | LLM 生成代码时 import 幻觉 | 编造不存在的包路径，代码无法编译 | 严重 |
| 6 | 入口方法返回值链路断裂 | 开发者不知道拿到实例后能调用哪些方法 | 中等 |

### P17. 问题1：FTS5 多词查询命中率极低
- **现象**：搜索 `"JSONArray parse"` 只命中 1 条，归一化得分 0.092 < min_score=0.7，全部被过滤
- **根因**：
  - FTS5 MATCH 多词默认 AND 语义，两个词同时出现的文档极少
  - sigmoid 公式 `1/(1+e⁻ˣ)` 对低分零值映射为 0.5，经 ranker 加成后仍低于阈值
- **解决**：
  - FTS5 查询词用 OR 连接（`"JSONArray OR parse"`）
  - 分数归一化从 sigmoid 改为结果集内线性相对映射（最高→1.0，最低→0.1）
- **效果**：召回率大幅提升，不再因低分被误过滤

### P18. 问题2：test 目录代码污染索引
- **现象**：`src/test/java` 下单测代码被当作 SDK API 索引，fastjson 测试代码占总量 94%
- **解决**：`parse_java_repo()` 中过滤路径包含 `/test/` 的 .java 文件
- **效果**：索引质量大幅提升，只剩真正的 SDK 公开 API

### P19. 问题3：SDK 角色推断 bug 修复
- **现象**：`_detect_class_role()` 仅用 `class_path`（类名）做子包匹配，导致子包检测失效
- **根因**：`class_path` 只包含类名（如 `FileService`），不包含包路径信息，无法判断所属子包
- **解决**：
  - 新增 `package_name` 入参（如 `com.company.file.service`）
  - 从 `package_name` 提取叶子包（`service`），与预定义子包集合匹配
  - 补充 3 条辅助检测函数：`_all_getters_setters()`、`_has_static_factory()`、`_has_self_returning_methods()` + `_has_build_method()`
  - 规则从 3 条扩展到 6 条分层规则

### P20. 问题4：LLM 不主动调用 MCP 工具
- **现象**：用户在 IDE 中问"FileManager 怎么用"，LLM 直接基于通用知识回答，没有调用 `search_private_knowledge` 检索私域知识库
- **根因**：工具描述过于抽象（原描述只是"搜索私域知识库"），LLM 无法判断何时应该触发
- **解决**：
  - 工具 `description` 增加中文触发关键词："内部SDK、私有SDK、自研、封装、对接、接入、怎么用、方法签名、com.、Factory、Manager、Helper"
  - 增加具体场景说明："【何时必须调用此工具】用户提到内部SDK、私有SDK、公司自研组件时"
- **效果**：触发率明显提升，后续还规划了 IDE system prompt 注入和会话启动预热两个方向进一步优化

### P21. 问题5：LLM 生成代码时 import 幻觉
- **现象**：LLM 拿到检索结果中的简短调用签名（如 `DeductPoints(bizId, uid, points)`），生成的代码中 import 路径是编造的，无法编译
- **根因**：code_parser 生成的 content 原来只有方法名+参数，没有全限定类名。LLM 被迫"猜测" import，而通用模型对私有 SDK 的包结构一无所知
- **解决**：
  - code_parser content 格式改为：`import com.example.sdk.PointsClient;\nPointsClient.deduct(String bizId, String uid, int points) → String`
  - assembler 在 Prompt 中显式标注每个 SDK 条目的 import 路径、返回类型、SDK 版本号
  - LLM 只需从 Prompt 中复制 import，不再自行编造
- **效果**：import 幻觉基本消除，生成的代码可以直接编译通过

### P22. 问题6：入口方法返回值链路断裂
- **现象**：开发者拿到入口方法返回值类型（如 `FileServiceManager.getSystemService()` → `FileService`）后，不知道 `FileService` 下面有哪些可用方法。只能反复检索或自行猜测 API，导致调用链不完整
- **根因**：assembler 原来只按 score 排序展示结果，entry_point 方法和其返回值接口的方法之间没有关联关系
- **解决**：
  - 新增 `_group_by_return_chain()`：按返回值类型建立方法分组
  - 入口方法的 return_type 与其对应接口的方法绑定为父子关系
  - 双向引用：正向（入口 → 子方法"返回值接口可用方法"列表），反向（子方法标注"获取此实例: XxxManager.xxxMethod()"）
- **效果**：一次检索即可获得完整调用链，开发者不需要二次检索

---

## 第五部分：总结与展望（约 2-3 页）

### P23. 当前成果
- 完整技术架构设计与文档体系（5 份设计文档）
- 混合检索引擎实现（BM25 + FAISS + NetworkX，轻量模式可运行）
- MCP Server 6 工具完整实现
- Java SDK AST 解析 + 角色推断
- Prompt 组装流水线（安全清洗 → 去重 → 分组 → 组装）
- 反馈闭环基础设施（数据记录已就绪，权重调整待实现）

### P24. 待完成事项（Roadmap）
- **P1 高优先级**：企业私域数据准备、全链路端到端验证
- **P2 中优先级**：JSON API 文档 loader、Python SDK AST 解析、LLM 文档分类、生产后端部署对接、反馈闭环权重调整、MCP 工具触发率优化
- **P3 低优先级**：增量更新管道、orchestrator 生产模式适配

### P25. 经验总结
- **渐进式架构**：轻量模式（零依赖）→ 生产模式（Docker 集群），降低试点门槛
- **优雅降级**：每个环节都有兜底策略（embedding 零向量、search mock 数据、超时不中断）
- **数据质量优先**：test 过滤、版本管理、角色标注，数据决定最终效果的上限
- **安全内建**：Prompt 注入防护从 Day1 就内建到流水线中