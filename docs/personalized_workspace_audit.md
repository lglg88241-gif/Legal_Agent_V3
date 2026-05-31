# Surgery H0：个性化法律工作空间能力审计

## 1. 审计结论摘要

| 能力 | 当前状态 | 风险等级 | 建议 |
|---|---|---|---|
| SQL 会话与用户隔离 | `User -> Conversation -> Message` 主链路基本成立，核心查询大多带 `user_id` 约束 | 中 | 保留 SQL 主存设计，继续把“案卷”从 `Conversation` 中解耦 |
| 案卷 Dossier 模型 | 不存在独立 `Dossier/Case` 模型，前端把 `Conversation` 直接命名成“卷宗” | 高 | H2 新增 `Dossier` 层，支持一个案卷下多个 conversation |
| 模式识别与用户同意 | 只有 `GREETING / GENERAL_CONSULT / CASE_UPDATE / OUT_OF_SCOPE` 四轨路由；存在“新案情边界检测 + 询问是否新建卷宗”的雏形，但没有“是否长期保存”的同意层 | 高 | H2/H6 增加显式模式和持久化同意机制 |
| 图谱隔离与权限 | Neo4j 动态图写入带 `user_id`，但图谱 API 和内部图谱工具存在全局查询面 | 严重 | 优先封住未鉴权图谱 API，并把模型查图谱改成 user-scoped |
| 图谱同步与删除一致性 | 图谱更新异步跑 Celery；SQL 删除和 Neo4j 删除不在同一事务；失败时接口仍可能返回 success | 严重 | H1/H3 增加显式同步状态、失败记录和跨库清理策略 |
| 会话/案卷管理体验 | 前端支持创建、切换、重命名、单个删除、清空当前会话；无搜索、无筛选、无批量、无归档 | 高 | H1 先补搜索、批量删除、归档和清空语义修正 |
| 模型查图谱能力 | 当前没有“查询当前用户当前案卷图谱”的专用 tool；只有全局 `query_neo4j_graph(entity_name)` | 高 | H4 新增 scoped graph tool，并把 graph block 注入 advisor |
| 用户个性化 | 不存在 `UserProfile/Preference` 存储；回答不会按用户偏好动态调整 | 中 | H5 增加 `user_profiles` 与 profile service |
| 多存储一致性 | SQL、Neo4j、ChromaDB 各自承担不同职责，但缺少统一生命周期和删除协议 | 高 | H1/H3/H6 定义统一 ID、删除协议和测试隔离策略 |

## 2. 数据库与用户隔离现状

### 2.1 当前核心表/模型

| 对象 | 当前是否存在 | 实现位置 | 现状说明 |
|---|---|---|---|
| `User` | 存在 | `app/models/entities.py` | 基础用户表，含 `id / username / hashed_password / created_at` |
| `Conversation` | 存在 | `app/models/entities.py` | 当前实际承担“会话 + 伪卷宗”双重职责，含 `title / case_summary / case_state_json / case_type / session_id` |
| `Message` | 存在 | `app/models/entities.py` | 通过 `conversation_id` 关联会话，消息元数据放在 JSON `metadata` |
| `Case / Dossier` | 不存在 | 无 | 没有独立案卷表；前端“卷宗”只是 `Conversation` 的产品命名 |
| `CaseSummary` 独立模型 | 不存在 | 无 | `case_summary` 只是 `Conversation` 的一列 |
| `Graph / Entity / Relation` SQL 模型 | 不存在 | 无 | 动态案情图和法条引用图都在 Neo4j，不在 SQL ORM 中 |
| `UploadedFile / Evidence` | 不存在 | 无 | 目前没有文件上传、证据材料表，也没有附件生命周期 |
| `UserProfile / Preference` | 不存在 | 无 | 没有偏好、角色、地区、输出样式等个性化表 |
| `Task / Todo` | 不存在 | 无 | 没有待办、流程推进、案卷状态任务表 |
| `Feedback` | 存在 | `app/database/models.py` | 额外表，按 `session_id` 存评分和评论，但没有 `user_id` 外键 |
| 迁移系统 | 不存在 | 无 Alembic | 当前依赖 `Base.metadata.create_all()` 和运行时 `ALTER TABLE` 补列 |

### 2.2 `user_id` 当前如何流转

| 环节 | 当前实现 | 结论 |
|---|---|---|
| API 层取用户 | `app/core/security.py` 解析 JWT，得到 `AuthenticatedUser(user_id, username)` | 正常 |
| `/dialog` 接收用户 | `app/api/dialog.py` 用 `get_current_user`，并校验 `conversation_id` 属于该用户 | 正常 |
| Celery 主任务 | `ask_agent_task(user_id, conversation_id, session_id, ...)` 显式接收 `user_id` | 正常 |
| SQL Conversation 绑定用户 | `Conversation.user_id -> users.id`，查询统一通过 `get_conversation_for_user()` | 正常 |
| SQL Message 绑定用户 | `Message` 本身没有 `user_id`，通过 `conversation_id` 间接绑定 | 基本安全，但依赖会话校验 |
| 图谱写入绑定用户 | `update_graph_task.delay(conversation_id, user_id)`，Neo4j 节点/边写 `user_id` 属性 | 设计存在 |
| 图谱查询绑定用户 | 仅 `get_user_cases_graph_overview(user_id)` / `get_cross_case_entities(user_id)` 显式带 `user_id` | 部分成立 |
| 模型内部查图谱 | `query_neo4j_graph(entity_name)` 是全局查询，不带 `user_id`/`conversation_id` | 高风险 |
| 图谱 API 用户隔离 | `/graph/user/{user_id}/cases` 和 `/graph/user/{user_id}/cross-case-entities` 没有鉴权依赖 | 严重风险 |

### 2.3 `conversation` 和 `case/dossier` 是否是同一个概念

当前答案是：产品文案里基本当成同一个概念，但技术上并没有真正的 `Dossier`。

| 问题 | 当前答案 |
|---|---|
| 当前是否只有 `conversation_id`，没有 `case_id` | 是 |
| `case_summary` 存在哪里 | `Conversation.case_summary` |
| `case_state` 存在哪里 | `Conversation.case_state_json` |
| `case_type` 存在哪里 | `Conversation.case_type` |
| 是否支持一个案卷下多个 conversation | 不支持 |
| 是否支持案卷标题 | 只支持 `Conversation.title` |
| 是否支持案由/类型 | 只有 `case_type` 粗粒度推断 |
| 是否支持案卷状态 | 不支持 |
| 是否支持标签 | 不支持 |
| 是否支持当事人字段 | SQL 不支持；只能在 Neo4j 动态图里抽取 |

补充结论：

- `Conversation` 现在兼具“普通咨询线程”和“案件卷宗”两种含义。
- `ask_agent_task` 会在非 `GREETING` 路由下持久化消息；`case_summary` 只在 `CASE_UPDATE` 时更新，但 `case_state_json` 和 `case_type` 并没有严格受同样约束。
- 因此，系统目前没有真正的“纯问答不入卷”能力，只有“所有咨询都先进入 conversation，再决定是否继续在同一 conversation 内积累案情”。

### 2.4 删除会话时会删除哪些数据

| 操作 | SQL 消息 | SQL Conversation | `case_summary` | `case_state_json` | Neo4j 动态图 | ChromaDB | 软删除/批量 |
|---|---|---|---|---|---|---|---|
| `DELETE /conversations/{id}` | 会，随 conversation 级联删除 | 会 | 会，随行删除 | 会，随行删除 | 会尝试 `delete_case_graph(conversation_id)` | 不涉及当前用户数据 | 无软删除，无批量 |
| `DELETE /conversations/{id}/messages` | 会 | 不会 | 会被清空 | **不会清空** | 会尝试 `delete_case_graph(conversation_id)` | 不涉及当前用户数据 | 无批量 |

关键风险：

- `clear_conversation_messages()` 只清空消息和 `case_summary`，不会清空 `case_state_json` 和 `case_type`。  
  这意味着用户点击“清空当前会话”后，隐藏的结构化案件状态仍然保留，后续回答仍可能被旧事实影响。
- 删除 conversation 或清空消息时，Neo4j 清理发生在 SQL `commit` 之后，且 `delete_case_graph()` 内部会吞掉异常并仅打 warning。  
  结果是：接口可能返回 success，但图谱实际没删干净。
- `Feedback` 表按 `session_id` 存储，不随 conversation 删除自动清理。

### 2.5 benchmark / 测试是否污染真实用户数据

结论：会，尤其是 `quality_benchmark.py`。

| 项目 | 当前现状 | 风险 |
|---|---|---|
| `quality_benchmark.py` 登录用户 | 固定尝试 `admin/admin123` 或 `lg/123456` | 会把 benchmark 数据写进固定账号空间 |
| benchmark 会话创建 | `new_conv()` 每次直接创建真实 conversation | 会污染 SQL 会话列表 |
| benchmark 图谱写入 | 多轮/CASE_UPDATE 场景会走 `update_graph_task` | 会污染 Neo4j 动态图 |
| cleanup 机制 | 没有 | 数据会长期残留 |
| 其他 e2e 脚本 | 一部分用随机用户名注册，一部分也直接登录固定账号 | 仍然没有统一 cleanup |

额外证据：

- `e2e_tests/quality_benchmark.py` 中的 `login()` 使用固定账号。
- `e2e_tests/logs/latency_backend.err.log` 已出现大量 `user_id=1 conversation_id=...` 的排队记录，说明测试流量写进了真实持久层。

建议：

- 后续引入 `benchmark_user` / `ephemeral test user`。
- benchmark 会话统一打测试标记并在结束后清理 SQL + Neo4j。
- 生产/演示环境禁止直接复用 `admin` 账号跑 benchmark。

## 3. 模式识别与用户同意机制

### 3.1 当前是否存在目标模式

| 目标模式 | 当前是否存在 | 当前对应实现 |
|---|---|---|
| `legal_question` / 纯法律提问 | 部分存在 | 实际对应 `GENERAL_CONSULT` 路由 |
| `case_analysis` / 案情分析 | 部分存在 | 实际对应 `CASE_UPDATE` 路由 |
| `dossier_management` / 案卷管理 | 不存在 | 只有 conversation CRUD，没有 dossier 层 |
| `document_generation` / 文书辅助 | 不存在 | 只有 `app/services/document_service.py` placeholder，未接入 API/UI |
| `professional_workspace` / 专业工作模式 | 不存在 | 没有律师/法务多案卷工作区模型 |

### 3.2 当前系统是否识别用户正在描述具体案情

有，但识别的是“是不是在开启一个新案件”以及“是不是在更新当前案件”，不是正式的产品模式。

当前链路：

1. `/dialog` 在入队前调用 `detect_case_boundary()`。
2. 如果判断为 `NEW_CASE`，接口直接返回：
   - `status = needs_confirmation`
   - `action = suggest_new_case`
3. 前端弹确认框：
   - 确认：新建一个新的 `Conversation`
   - 取消：带 `force_current_case=true` 重投到当前 `Conversation`

已经具备的能力：

- 能识别“新案情”和“继续补充当前案情”。
- 能在“是否新建新卷宗”这一步征求用户确认。

仍然缺失的能力：

- 没有“纯问答 / 案情分析 / 案卷管理 / 文书辅助 / 专业模式”的正式模式枚举。
- 没有“是否长期保存”的同意机制。  
  当前所有对话都必须附着到一个真实 `Conversation`，天然就是持久化的。
- 没有 `suggested_mode` / `mode_reason` 这类结构化响应。
- 没有把“新建卷宗”升级为真正的 `Dossier` 创建流程。

### 3.3 当前前端是否允许用户手动选择模式

没有正式模式选择器。

当前前端只有两个相关开关：

- `本地模型加速`：这是模型 provider toggle，不是产品模式。
- `精准案情梳理`：这是单个布尔开关，但它被同时传给：
  - `is_precise_mode`
  - `proactive_graph_mode`

这意味着：

- 当前 UI 没有模式 dropdown / tabs / toggle group。
- 用户看不到“我现在处于纯问答模式还是案情分析模式”。
- “精准案情梳理”实际上同时影响路由和 reflector 的实体歧义检测，职责混在一起。
- 没有“创建案卷 / 仅本次咨询”的确认 UI。

### 3.4 最小改造点

如果要实现“系统识别案情后请求用户同意创建案卷”，最小改造建议是：

| 改造点 | 最小方案 |
|---|---|
| Router / dialog 返回结构 | 在 `/dialog` 的 `needs_confirmation` 响应里增加 `suggested_mode`、`requires_persistence_consent`、`suggested_action` |
| 前端交互 | 从单一 confirm 升级为模式弹窗：`继续临时咨询 / 建立案卷 / 取消` |
| 服务层 | 新增 `dossier service`，不要再直接把“新建 conversation”当成“建卷” |
| 数据层 | 新增 `Dossier` 模型，`Conversation` 只做线程；`conversation.dossier_id` 可空 |
| 无持久化模式 | 增加临时咨询通道，或引入 `ephemeral_conversations` / `save_case_preference` |

## 4. 会话与案卷管理体验

### 4.1 当前前端能力

| 能力 | 当前状态 | 说明 |
|---|---|---|
| 会话列表展示 | 支持 | `SessionList.vue` 左侧列表 |
| 更新时间显示 | 支持 | 列表项显示 `updated_at` |
| 标题重命名 | 支持 | 前端 prompt + `PUT /conversations/{id}` |
| 单个删除 | 支持 | 列表项单删 |
| 清空当前会话 | 支持 | 调 `DELETE /conversations/{id}/messages` |
| 搜索 | 不支持 | 前端没有搜索框 |
| 按案由/标签筛选 | 不支持 | 前端无筛选 UI |
| 批量删除 | 不支持 | 只能一条条删 |
| 归档 | 不支持 | 无状态字段 |
| 显示案情摘要 | 部分支持 | 只显示当前选中会话的 `case_summary`，不是列表级摘要 |
| 区分普通会话和案卷 | 不支持 | 所有条目统一叫“卷宗” |
| 导出 | 支持 | 前端本地导出 JSON |

### 4.2 当前 API 能力矩阵

| API 能力 | 当前状态 | 备注 |
|---|---|---|
| list conversations | 支持 | `GET /conversations` |
| delete conversation | 支持 | `DELETE /conversations/{id}` |
| batch delete | 不支持 | 无 |
| rename conversation | 支持 | `PUT /conversations/{id}` |
| archive conversation | 不支持 | 无 |
| search conversations | 部分支持 | `GET /conversations/search`，但前端未接入 |
| get conversation summary | 部分支持 | `POST /conversations/{id}/summary` 生成临时摘要，不是 dossier summary API |
| clear conversation messages | 支持 | 但不会清空 `case_state_json` |
| create dossier | 不支持 | 无 |
| link conversation to dossier | 不支持 | 无 |

### 4.3 如果用户有很多会话，目前如何找旧案

当前主要靠滚动左侧列表。

现状：

- 后端已有 `GET /conversations/search?case_type=&keyword=`，可以按 `case_type` 和 `title/case_summary` 搜。
- 前端没有接这个接口，因此用户侧基本还是滚动查找。
- 不能全文搜索消息内容。
- 不能按图谱事实搜索。
- 不能按当事人、公司、标签、案卷状态筛选。

### 4.4 主要体验问题

- 所有条目都被称为“卷宗”，但底层其实只是 `Conversation`，语义会误导用户。
- 只能一个一个删除，案卷多了管理成本高。
- “清空当前会话”并不是真正清空，隐藏的 `case_state_json` 仍会保留。
- 用户不知道当前会话是否只是普通咨询，还是已经被长期当成“卷宗”记忆。
- 没有把普通咨询升级为正式案卷的显式动作。
- 没有图谱同步状态提示，用户也不知道右侧图谱是否是最新的。

## 5. 实时子图存储与同步

### 5.1 实时子图由谁生成

当前实时子图主要由 LLM 驱动的图谱抽取链生成：

- `update_graph_task` 负责后台触发。
- `refresh_case_graph()` 负责主流程。
- 抽取路径：
  - 先走 `extract_incremental_graph_actions()`，使用 `INCREMENTAL_EXTRACTION_PROMPT`
  - 如果拿不到动作，再 fallback 到 `extract_case_triples()`，使用 `GRAPH_EXTRACTION_PROMPT`
  - 最后再走 `plan_graph_gc()` 做语义清理，使用 `SEMANTIC_GC_PROMPT`

结论：

- 当前是“LLM graph_updater + 少量规范化规则”的方案。
- 不是纯 rule-based extractor。
- `scripts/build_article_reference_graph.py` 只负责静态法条引用图谱 `LawArticle-REFERENCES-LawArticle` 的离线构建，不参与实时案情图更新。

### 5.2 实时子图写到哪里

| 存储位置 | 当前现状 |
|---|---|
| Neo4j | 写入 `DynamicCaseEntity` 节点和 `CASE_RELATION` 关系 |
| SQL | 不单独存图结构；只存 `case_summary` / `case_state_json` / `case_type` |
| Memory / Redis | 仅用于 LangGraph checkpoint，不是图谱主存 |
| JSON field | 没有图谱 JSON 持久化字段 |

绑定关系：

- 动态图节点和边都写 `conversation_id`
- 同时写 `user_id`
- 当前没有 `source_message_id`
- 当前没有 `confidence`
- 当前没有 `graph_version`

### 5.3 图谱 schema

#### 动态案情图

| 项目 | 当前现状 |
|---|---|
| 节点 label | 实际存储只有 `DynamicCaseEntity` |
| 节点分类 | 通过 `category` / `node_type` 属性区分 `主体 / 客体 / 事件 / 后果` |
| 边类型 | 实际存储只有 `CASE_RELATION` |
| 关系语义 | 通过 `rel.relation` 属性表达，如“工作时长 / 主张 / 原因” |
| 节点属性 | `conversation_id / user_id / name / category / node_type / created_at / updated_at` |
| 边属性 | `conversation_id / user_id / relation / source_name / target_name / created_at / updated_at` |
| 类型约束 | 只有代码层 `ALLOWED_CATEGORIES` 与 action 校验，没有数据库约束 |
| confidence | 没有 |
| source_message_id | 没有 |
| graph_version | 没有 |

#### 静态法条引用图

| 项目 | 当前现状 |
|---|---|
| 节点 label | `LawArticle` |
| 边类型 | `REFERENCES` |
| 来源 | `scripts/build_article_reference_graph.py` 离线写入 |
| 是否用户级 | 否，完全全局共享 |

### 5.4 图谱更新是同步还是异步

当前是异步。

| 项目 | 当前现状 |
|---|---|
| 更新触发 | `ask_agent_task` 在 `CASE_UPDATE` 后 `delay(update_graph_task)` |
| 执行方式 | Celery 后台任务 |
| retry | 没有显式 `autoretry_for` 或任务状态表 |
| 失败记录 | 只有日志，没有用户可见状态 |
| 前端可见性 | 前端只轮询 `GET /conversations/{id}/graph`，不知道 Celery 是否完成 |
| 用户提示 | 没有“同步中 / 已同步 / 同步失败”正式提示 |

额外高风险点：

- `update_graph_task` 只接收 `conversation_id` 和 `user_id`，真正执行时再去 SQL 里找“最新一条 user message”。  
  如果同一 conversation 短时间内连续发消息，旧任务可能会读到新消息，导致图谱增量错位。

### 5.5 删除会话或案卷时图谱是否同步删除

部分支持，但不是强一致：

- `DELETE /conversations/{id}` 会在 SQL commit 后尝试删 Neo4j 动态图。
- `DELETE /conversations/{id}/messages` 也会尝试删 Neo4j 动态图。
- 失败不会回滚 SQL，也不会把接口改成失败。

这是高风险点。

### 5.6 实时子图数据流图

```mermaid
flowchart LR
    U[User Input] --> API[/POST /dialog/]
    API --> CELERY[ask_agent_task]
    CELERY --> SQL1[(SQL Conversation/Message)]
    CELERY --> AGENT[LegalGraphAgent]
    AGENT --> RESP[Assistant Response]
    RESP --> SQL1

    CELERY -->|route_intent = CASE_UPDATE| GQ[update_graph_task]
    GQ --> SQL2[Load latest user message + case_summary]
    GQ --> EXTRACT[LLM Graph Extraction]
    EXTRACT --> GC[Semantic GC]
    GC --> NEO[(Neo4j DynamicCaseEntity/CASE_RELATION)]

    NEO --> GRAPHAPI[/GET /conversations/{id}/graph/]
    GRAPHAPI --> UI[Frontend Graph Sidebar]
```

## 6. 模型查图谱能力

### 6.1 当前是否有图谱查询 tool

| 工具 | 当前是否存在 | 说明 |
|---|---|---|
| `query_case_graph(conversation_id)` | 不存在 | 没有 |
| `get_case_entities(conversation_id)` | 不存在 | 没有 |
| `get_case_relations(conversation_id)` | 不存在 | 没有 |
| `search_cases_by_graph(...)` | 不存在 | 没有 |
| `query_neo4j_graph(entity_name)` | 存在 | 但这是全局 Neo4j 查询，不是当前用户/当前案卷图谱工具 |

### 6.2 Advisor 生成最终回答时是否读取案情图谱

当前会读“图谱事实的文本快照”，但不是以结构化图查询方式读取。

| 数据来源 | 当前是否使用 | 方式 |
|---|---|---|
| `case_summary` | 使用 | 直接注入 advisor prompt |
| `saved_case_state` | 使用 | 用于构建/补全 `CaseState` |
| 历史 messages | 部分使用 | 主要在 router / boundary / fact_updater 阶段，不是 advisor 主上下文 |
| Neo4j 图谱事实 | 使用，但间接 | 通过 `format_graph_memory(conversation_id)` 变成文本 `graph_context` |
| 结构化 graph query | 不使用 | 没有 current-case graph tool |

### 6.3 Graph updater 写完后，下一轮对话能否使用图谱事实

能，但当前路径是“把图谱转成文本”，不是“模型主动查图谱”。

当前链路：

- `ask_agent_task` 下一轮启动前，会加载 `graph_memory = format_graph_memory(conversation_id)`。
- `vector_researcher_node` 也会再次拉 `format_graph_memory(conversation_id)`。
- advisor 最终读取的是 `graph_context / graph_memory` 文本块。

因此结论是：

- 下一轮能用图谱事实。
- 但依赖的是文本注入，不是 scoped graph query。
- 这套路径不适合后续做“按实体查图”“按事实查案卷”“图谱证据解释”等高级能力。

### 6.4 最小实现方案

不改代码设计层面的最小方案如下：

1. 新增 user-scoped tool  
   例如 `query_case_graph(conversation_id, user_id, entity_name=None, relation=None, limit=20)`。

2. tool 自动透传上下文  
   不让 LLM 自己填 `user_id`，由框架从当前 `AgentState` 自动注入。

3. advisor prompt 增加 `graph_block`  
   把结构化图查询结果单独整理成“当前案卷图谱事实”区块，而不是只塞一段字符串。

4. router 增加轻量 `GRAPH_LOOKUP` 或内部 graph-needed 标记  
   不一定要新增用户可见模式，但至少要让系统知道什么时候应触发 scoped graph lookup。

5. API 增加图谱详情接口  
   例如 `GET /dossiers/{id}/graph` 或 `GET /conversations/{id}/graph/facts`。

6. 前端增加图谱视图状态  
   在右侧图谱侧栏之外，再增加“事实列表 / 最新同步状态 / 图谱查询”能力。

## 7. 用户个性化能力

### 7.1 当前是否存储用户偏好

当前基本没有。

| 偏好项 | 当前是否存储 |
|---|---|
| 常问领域 | 没有 |
| 常用地区 | 没有 |
| 输出格式偏好 | 没有 |
| 语言风格 | 没有 |
| 是否默认保存案情 | 没有 |
| 风险提示风格 | 没有 |
| 常用模板 | 没有 |
| 专业身份（普通用户/律师/法务/学生） | 没有 |

当前唯一接近“个性化”的内容：

- 用户自己的历史 conversations
- 用户自己的动态案情图谱实体历史
- `historical_reference` 会把用户过往案卷实体简要带入当前轮

但这不等于 profile / preference。

### 7.2 当前回答是否根据用户偏好调整

没有。

当前回答只会根据：

- 当前问题
- 当前 conversation 的 `case_summary`
- 当前 conversation 的 Neo4j 图谱文本
- 用户历史案卷实体列表（仅作背景参考）

不会根据以下内容调整：

- 用户角色
- 地区
- 输出长短
- 证据清单风格
- 风险提示强度
- 是否偏好保存案情

### 7.3 建议支持的 profile

建议第一版最小支持这些字段：

| 字段 | 说明 |
|---|---|
| `user_role` | `consumer / lawyer / in_house / student / hr` |
| `practice_areas` | 常问领域，如劳动、合同、婚姻、刑事 |
| `default_region` | 默认地区，如北京/上海/深圳 |
| `preferred_output_style` | `concise / structured / litigation_style / checklist` |
| `save_case_preference` | `always_ask / default_yes / default_no` |
| `risk_tolerance` | `conservative / balanced / assertive` |
| `warning_style` | `strong / moderate / light` |
| `evidence_checklist_style` | `brief / detailed / litigation_ready` |
| `document_template_preferences` | 常用文书偏好，JSON |

### 7.4 个性化数据应该放哪里

推荐方案：单独建 `user_profiles`，不要继续把偏好塞进 `users` 或 `Conversation`。

建议 schema：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | bigint PK | 主键 |
| `user_id` | bigint unique FK | 对应 `users.id` |
| `user_role` | varchar(32) | 用户角色 |
| `default_region` | varchar(64) | 默认地域 |
| `preferred_output_style` | varchar(32) | 输出风格 |
| `save_case_preference` | varchar(32) | 保存偏好 |
| `risk_tolerance` | varchar(32) | 风险偏好 |
| `warning_style` | varchar(32) | 风险提示风格 |
| `evidence_checklist_style` | varchar(32) | 证据清单风格 |
| `practice_areas_json` | JSON | 常问领域 |
| `document_template_preferences_json` | JSON | 常用模板偏好 |
| `created_at / updated_at` | datetime | 审计字段 |

理由：

- `Conversation` 适合存当前线程状态，不适合存用户长期偏好。
- `users` 应保持轻量，避免 profile 横向膨胀。
- 独立 `user_profiles` 更容易做权限、缓存和前端设置页。

## 8. 多存储一致性：SQL / ChromaDB / Neo4j

| 存储 | 当前用途 | 是否用户级 | 是否案卷级 | 风险 |
|---|---|---|---|---|
| SQL | 用户、conversation、message、case_summary、case_state_json、case_type、feedback | 是 | 只有 conversation 级，没有真正 dossier 级 | conversation 与 dossier 混用；删除和反馈不完全一致 |
| ChromaDB | 全局法律法规库 + 全局案例库向量检索 | 否 | 否 | 当前不存用户材料；后续如果支持用户上传，需要新 collection 策略 |
| Neo4j | 一套库里同时放静态法条引用图和动态用户案情图 | 部分 | 是，按 `conversation_id` | 查询面缺少作用域隔离，删除不同步，静态图与动态图混库 |

### 8.1 统一 ID 现状

| 维度 | 当前现状 |
|---|---|
| SQL -> Neo4j | 通过 `conversation_id` / `user_id` 建立弱关联 |
| SQL -> ChromaDB | 没有用户或 conversation 级关联 |
| Neo4j 静态图 -> ChromaDB | 没有统一 document ID，只能靠 `law_title/article` 等语义字段对照 |
| Dossier 级统一 ID | 不存在 |

### 8.2 一致性风险清单

- Neo4j 里混放静态法条图和动态用户案情图，但查询权限策略没分开。
- `query_neo4j_graph()` 是全局工具，不区分当前用户、当前案卷、全局知识图。
- conversation 删除后 Neo4j 清理不是事务性行为。
- `clear messages` 不清 `case_state_json`，SQL 内部就已经不一致。
- benchmark / e2e 会写真实 SQL conversation，也可能触发真实 Neo4j 动态图写入。
- 当前 ChromaDB 还是纯全局知识库，没有用户上传材料；这反而是安全的，但也意味着后续 dossier 上传能力要重新设计。

## 9. 发现的问题清单

### P0

- 图谱 API 存在未鉴权暴露面：`/graph/subgraph`、`/graph/user/{user_id}/cases`、`/graph/user/{user_id}/cross-case-entities` 当前都不绑定 `current_user`。
- 模型内部的 `query_neo4j_graph(entity_name)` 是全局查询，不带 `user_id` / `conversation_id`，动态用户案情图存在跨用户命中风险。
- `DELETE /conversations/{id}/messages` 不会清空 `case_state_json` 和 `case_type`，用户以为“清空了会话”，但隐藏案件状态仍保留。
- SQL 删除与 Neo4j 删除不是强一致事务；Neo4j 删除失败时接口仍可能返回成功，存在“SQL 已删、图谱残留”的一致性问题。
- `update_graph_task` 不是 message-bound，而是在执行时抓“最新 user message”，存在多次连续发言时图谱增量错位的风险。

### P1

- 当前没有真正的 `Dossier` 模型，`Conversation` 被同时当成普通咨询线程和案件卷宗。
- 所有对话都要求先有真实 `Conversation`，没有“仅本次咨询、不长期保存”的无状态模式。
- 前端没有搜索、筛选、批量删除、归档；大量会话时管理效率会急剧下降。
- 当前“精准案情梳理”开关同时驱动 `is_precise_mode` 与 `proactive_graph_mode`，模式语义混乱。
- benchmark 尤其是 `quality_benchmark.py` 会写固定账号，没有 cleanup，会污染真实工作空间。
- `Feedback` 只按 `session_id` 存储，没有 `user_id` 外键和删除联动。

### P2

- 没有 `UserProfile / Preference`，个性化助手能力还停留在“历史会话和历史图谱实体”层面。
- 文书辅助只有 placeholder service，没有 API、UI、模板和案卷绑定。
- 前端右侧图谱只显示当前图快照，没有正式的“同步中 / 已同步 / 失败”状态机。
- 后端虽然有 `/conversations/search`，但前端未接；也没有消息全文搜索和图谱事实搜索。

## 10. 下一阶段改造路线建议

### H1：会话清理与管理体验

- 目标：先把现有 `Conversation` 工作区做干净，补齐搜索、批量删除、清空一致性。
- 涉及文件：
  - `frontend/src/components/SessionList.vue`
  - `frontend/src/App.vue`
  - `frontend/src/api/index.js`
  - `app/api/conversations.py`
  - `app/services/conversation_store.py`
- 预期收益：
  - 先解决“案卷多了不好找”和“只能一个个删”的现实痛点。
  - 修复“清空当前会话”仍残留 `case_state_json` 的隐藏状态问题。
- 风险：
  - 需要明确“清空消息”和“彻底重置案卷状态”是不是两个不同动作。

### H2：案卷 Dossier 模型与用户同意创建

- 目标：把 `Dossier` 从 `Conversation` 中独立出来，并补齐“是否建卷 / 是否持久化”的用户同意机制。
- 涉及文件：
  - `app/models/entities.py`
  - `app/services/conversation_store.py`
  - `app/api/dialog.py`
  - `app/api/conversations.py`
  - `frontend/src/App.vue`
  - 新增 `dossier_service.py` / `dossiers` API
- 预期收益：
  - 纯法律问答和持续案卷管理真正分层。
  - 为“一案多会话”“从咨询升级为案卷”打基础。
- 风险：
  - 需要做旧 `Conversation` 到新 `Dossier` 的迁移或兼容映射。

### H3：图谱同步状态与可视化

- 目标：让图谱同步从“后台黑盒”变成“前台可见状态机”。
- 涉及文件：
  - `app/celery_app.py`
  - `app/services/graph_extractor.py`
  - `app/api/conversations.py` 或新增 `graph_status` API
  - `frontend/src/components/AnalysisPanel.vue`
  - `frontend/src/components/KnowledgeGraphPanel.vue`
- 预期收益：
  - 用户知道图谱是否已经同步、是否失败、何时完成。
  - 为后续 graph audit 和案卷可视化打基础。
- 风险：
  - 要引入 task status 或 graph sync record，异步复杂度会上升。

### H4：模型查图谱工具

- 目标：让模型能查“当前用户当前案卷图谱”，而不是全局扫 Neo4j。
- 涉及文件：
  - `app/agent/tools.py`
  - `app/agent/nodes/_llm.py`
  - `app/services/graph_extractor.py`
  - `app/agent/nodes/researcher.py`
  - `app/agent/nodes/advisor.py`
  - `app/agent/nodes/router.py`
- 预期收益：
  - 支持“按实体追问”“按事实补充”“图谱回查解释”。
  - 降低跨用户图谱误读风险。
- 风险：
  - 作用域控制必须先做好，否则会把 P0 隐私问题放大。

### H5：用户个性化 profile

- 目标：把系统从“记住历史会话”升级为“理解用户偏好”。
- 涉及文件：
  - `app/models/entities.py` 或新增 `user_profiles.py`
  - `app/api/auth.py` / 新增 `profile` API
  - 新增 `profile_service.py`
  - `frontend/src/App.vue` 或新增设置页组件
  - `app/services/legal_agent.py` / `app/agent/nodes/advisor.py`
- 预期收益：
  - 回答风格、风险提示、保存偏好、地区适用性可以按用户定制。
  - 为律师/法务/普通用户三类体验分层提供基础。
- 风险：
  - profile 注入过重会污染 grounded 回答，需要严格把偏好限制在表达层。

### H6：案卷搜索与智能模式切换

- 目标：补齐“搜索老案 + 模式切换 + 案卷升级”闭环。
- 涉及文件：
  - `app/api/dialog.py`
  - `app/api/conversations.py`
  - 新增 `dossiers` / `search` API
  - `frontend/src/App.vue`
  - `frontend/src/components/SessionList.vue`
  - `app/services/case_boundary.py`
  - `app/agent/nodes/router.py`
- 预期收益：
  - 用户可以在纯问答、案情分析、案卷管理之间切换。
  - 老案可以按标题、摘要、图谱事实、案由快速召回。
- 风险：
  - 模式太多会增加 UX 负担，需要先做最小模式集合和默认策略。
