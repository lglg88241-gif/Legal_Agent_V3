# Dossier Intelligence Agent Design

## 1. 当前系统链路梳理

当前系统已经具备会话级图谱、案卷级聚合、participant overlay、摘要和 advisor 注入，但这些能力分散在多个服务里，没有一个统一的案卷智能整理层。

### 1.1 从用户输入到回答

当前主执行链路由 [app/celery_app.py](C:/Users/123/Legal_Agent_V3/app/celery_app.py) 驱动：

用户输入
-> `ask_agent_task()`
-> `_run_task_async()`
-> 加载 `chat_history` / `case_summary` / `saved_case_state`
-> 加载 `dossier_context`
-> 构建 `dossier_graph_summary`
-> 按需加载 `dossier_graph_context`
-> 调用 `legal_agent.ask()`
-> 返回 answer / route_intent / case_summary / case_state_snapshot
-> `_persist_dialog_turn_async()`
-> 如满足条件则 `update_graph_task.delay(...)`

责任文件：

- [app/celery_app.py](C:/Users/123/Legal_Agent_V3/app/celery_app.py)
  - 会话上下文装配
  - route/persist/graph update 控制流
  - `agent_actions` 粗粒度记录
- [app/services/legal_agent.py](C:/Users/123/Legal_Agent_V3/app/services/legal_agent.py)
  - advisor 主图
  - 接收 `dossier_context`、`dossier_graph_summary`、`dossier_graph_context`
- [app/services/case_followup.py](C:/Users/123/Legal_Agent_V3/app/services/case_followup.py)
  - `is_case_fact_update_message()`
  - `is_dossier_followup_question()`
  - `should_persist_structured_case_message()`

### 1.2 从用户输入到会话图谱

当前 conversation raw graph 仍然是独立的 source of truth，链路如下：

用户事实输入
-> `ask_agent_task()` 判定需持久化
-> `update_graph_task()`
-> `refresh_case_graph()`
-> `extract_incremental_graph_actions()` / `extract_case_triples()`
-> `execute_graph_actions()`
-> Neo4j `DynamicCaseEntity` / `CASE_RELATION`

责任文件：

- [app/celery_app.py](C:/Users/123/Legal_Agent_V3/app/celery_app.py)
  - 触发 `update_graph_task`
- [app/services/graph_extractor.py](C:/Users/123/Legal_Agent_V3/app/services/graph_extractor.py)
  - triple / action extraction
  - graph sync
  - `get_case_graph()`

现状判断：

- conversation raw graph 仍是按 `conversation_id` 写入
- dossier 层目前没有反向写回 Neo4j
- H6b-1 之后 explicit entity 保真已回到 extractor 层

### 1.3 从会话图谱到案卷图谱

当前 dossier graph 是只读聚合，不是独立写库：

`dossier_id`
-> `list_dossier_conversations()`
-> 收集 `conversation_ids`
-> 查询 Neo4j 中这些会话的 `DynamicCaseEntity` / `CASE_RELATION`
-> 返回 nodes / edges / stats
-> 用 `participant_mappings` 给节点附加 `display_label`

责任文件：

- [app/api/dossiers.py](C:/Users/123/Legal_Agent_V3/app/api/dossiers.py)
  - `GET /dossiers/{id}/graph`
  - `GET /dossiers/{id}/participant-candidates`
- [app/services/dossier_graph.py](C:/Users/123/Legal_Agent_V3/app/services/dossier_graph.py)
  - `_fetch_dossier_graph_sync()`
  - `get_dossier_graph_payload()`
  - `_apply_participant_mappings_to_nodes()`
  - `build_dossier_graph_summary()`

现状判断：

- dossier graph 只是 raw aggregation + mapping overlay
- 还没有单独的 display graph builder
- 还没有“已确认同一主体则前端只显示一个 display node”的中间层

### 1.4 从案卷图谱到主体确认候选

当前主体确认候选由 [app/services/conversation_store.py](C:/Users/123/Legal_Agent_V3/app/services/conversation_store.py) 负责生成：

`graph_payload`
-> `_build_resolution_node_record()`
-> unresolved raw-label task
-> cross-conversation candidate builder
-> 返回 `raw_label_resolution` + `cross_conversation_entity_resolution`

相关职责集中在：

- `update_dossier_participant_aliases()`
- `resolve_dossier_participant_mentions()`
- `build_dossier_participant_candidates()`
- `_build_cross_conversation_entity_candidates()`

现状判断：

- participant registry 已经具备 graph provenance
- cross-conversation candidate 已经存在
- 但 identity candidate、role/affiliation candidate、display graph overlay 仍未解耦

### 1.5 从案卷内容到摘要

当前案卷摘要是独立链路，不和 participant resolution 统一编排：

`POST /dossiers/{id}/generate-summary`
-> `generate_dossier_auto_summary()`
-> `build_dossier_summary_materials()`
-> LLM or fallback
-> 写回 `dossier.auto_summary`

责任文件：

- [app/api/dossiers.py](C:/Users/123/Legal_Agent_V3/app/api/dossiers.py)
- [app/services/dossier_summary.py](C:/Users/123/Legal_Agent_V3/app/services/dossier_summary.py)

现状判断：

- summary 读取 conversation summaries / case_state / recent messages
- 不直接消费 participant identity result
- 不直接消费 display graph

### 1.6 前端图谱和主体确认

当前前端主要集中在 [frontend/src/App.vue](C:/Users/123/Legal_Agent_V3/frontend/src/App.vue)：

- dossier graph preview 直接展示 `graph.nodes` / `graph.edges`
- 如果节点带 `display_label`，就优先显示 `display_label`
- participant candidate dialog 既要展示候选、又要做绑定、又要做手动创建、又要做调试折叠
- `currentAnalysis.actions` 直接读取后端 `agent_actions`

前端调用层在 [frontend/src/api/index.js](C:/Users/123/Legal_Agent_V3/frontend/src/api/index.js)：

- `getDossierGraph()`
- `getDossierParticipantCandidates()`
- `resolveDossierParticipants()`
- `generateDossierSummary()`

## 2. 当前职责混杂点

### 2.1 participant resolution 职责过载

当前 participant resolution 同时承担了四类职责：

1. raw label 处理
2. 跨会话主体 identity candidate
3. 手动新建主体 fallback
4. graph 展示 overlay 的一部分语义

这导致：

- `conversation_store.py` 已经承担了过多案卷智能整理逻辑
- candidate 的语义既像任务队列，又像 registry 管理，又像 display adapter
- API 里 `participant-candidates` 和 `graph` 之间没有统一 display abstraction

### 2.2 dossier_summary 与 participant resolution 分离

当前 auto summary 由 [app/services/dossier_summary.py](C:/Users/123/Legal_Agent_V3/app/services/dossier_summary.py) 单独生成，participant resolution 结果只存在 SQL overlay 和 graph preview 层。

结果是：

- 摘要未必知道哪些主体已经被用户确认是同一主体
- 摘要也不知道哪些节点仍待确认
- “公司的人事”这类角色/组织关系不会被 summary 显式整理

### 2.3 dossier_graph 目前偏 raw aggregation

[app/services/dossier_graph.py](C:/Users/123/Legal_Agent_V3/app/services/dossier_graph.py) 现在做的是：

- 查询 raw nodes / edges
- 把 `participant_mappings` 映射到 node 上
- 给 node 添加 `display_label`

但它还不是真正的 display graph：

- 未确认同名节点仍然只是平铺 raw nodes
- 已确认 identity 的多个 raw nodes 也不会折叠成一个 display node
- role/affiliation 没有单独建模层

### 2.4 App.vue 负担过重

当前 [frontend/src/App.vue](C:/Users/123/Legal_Agent_V3/frontend/src/App.vue) 同时承担：

- dossier detail 展示
- participant candidate 过滤和排序
- resolve payload 组装
- 手动 participant 创建
- raw graph preview
- advanced/debug 折叠

结果是：

- UI 状态和语义判断耦合
- cross conversation identity 与 raw-label fallback 混在一个组件
- 未来如果增加 affiliation candidate，当前单文件继续膨胀

### 2.5 本轮分析面板粒度过粗

当前 `agent_actions` 仍是：

- `tool_router`
- `fact_updater`
- `retrieval_tools`
- `reflector`
- `legal_advisor`

它没有体现出真实后端执行节点：

- graph_extractor
- graph_sync
- dossier_graph aggregation
- participant_resolution
- summary_update
- future dossier_intelligence

这会让用户误以为系统只做了问答，没有做 dossier-level intelligence orchestration。

## 3. 设计原则

### 3.1 Conversation raw graph is source of truth for each conversation

会话图谱必须保真，不能被案卷层确认反向改写。

要求：

- raw Neo4j node / edge 永远保持 conversation-level 事实来源
- dossier overlay 不回写 conversation raw graph label
- display graph 只是展示层，不是 raw graph 替代物

### 3.2 Dossier-level participant registry is an overlay

`DossierParticipant` / `DossierParticipantMention` 只是案卷层映射，不是 raw graph 本身。

要求：

- registry 保存用户确认过的 dossier-level identity anchor
- mention 保存 provenance
- registry 可以驱动 display overlay、summary、advisor memory
- registry 不能被当成 raw graph source of truth

### 3.3 User-facing primary action is confirming identity between two existing graph entities

用户主流程应是确认两个已有图谱实体是否同一，而不是手动新建主体。

主问题示例：

- 会话 #674 的 A公司 是否与 会话 #675 的 A公司 是同一个主体？
- 会话 #674 的 当事人 是否与 会话 #675 的 当事人 是同一个人？
- 会话 #676 的 公司 是否指前面已确认的 A公司？

### 3.4 Manual participant creation is advanced fallback only

手动新建主体只应是高级兜底：

- 当没有可绑定 participant
- 当两个节点确实是同一主体但系统没有形成可用候选
- 当用户希望先建立一个 dossier-level identity anchor

它不应成为普通用户主入口。

### 3.5 Confirmed identity should affect display graph, advisor memory, and summary

一旦确认同一主体，影响应体现在三层：

1. display graph 合并显示
2. advisor 读取 dossier intelligence overlay 时使用统一 display label
3. auto summary 使用确认后的 dossier-level identity

### 3.6 Unconfirmed same-label nodes remain separate

未确认时，即使 label 相同，也必须保留分开展示。

例如两个会话都出现 `A公司`：

- 没确认前显示两个 raw entity
- 标记“待确认”
- 不自动平铺成一个 display participant

### 3.7 Role/affiliation is not identity

“公司的人事”与 “A公司” 不是同一主体。

正确建模：

- `公司的人事` 是 person / role-like entity
- `A公司` 是 organization
- 两者之间可能是 `represents` / `works_for` / `belongs_to` / `agent_of`

绝不能直接 identity merge。

### 3.8 Non-participant nodes never become participant confirmation tasks

以下节点不进入主体确认主流程：

- event
- fact
- document
- evidence
- amount / money
- time / date
- law / statute
- procedure / claim / issue

它们可以进入 display graph，但不进入 participant identity task。

## 4. Dossier Intelligence Agent 职责

建议新增一个 dossier-level intelligence orchestration 层，名称可以是：

- `DossierIntelligenceAgent`
- 或 `dossier_intelligence.py`

它不替代 graph extractor，不替代 advisor，而是统一整理案卷层智能结果。

### 4.1 Auto Summary

统一生成或更新：

- `auto_summary`
- `timeline`
- `key_facts`
- `disputed_facts`
- `evidence_gaps`
- `risk_points`
- `next_steps`

要求：

- 不覆盖用户手写 `summary`
- 默认消费 conversation summaries、recent messages、display graph、confirmed participant overlay
- 与 participant identity 和 affiliation 结果保持一致

### 4.2 Cross-conversation Entity Identity

负责判断不同会话图谱节点是否可能指向同一主体。

输入：

- raw graph nodes / edges
- `conversation_id`
- `graph_node_id`
- `graph_node_label`
- `graph_node_type`
- existing `DossierParticipant` mappings
- negative / dismissed decisions

输出 identity candidate：

- `source_node`
- `target_node`
- `suggested_display_label`
- `confidence`
- `reason`
- `requires_user_confirmation`
- `can_auto_apply`
- `can_confirm_same`
- `can_confirm_different`

原则：

- 默认不永久自动合并
- high confidence exact same explicit label 可 auto-suggest
- 已有 confirmed mapping 时不重复生成

### 4.3 Role / Affiliation Relation

负责识别“不是同一主体，但存在归属或代表关系”的情况。

例子：

- “公司的人事” 不是 A公司
- 但它可能 `represents_or_belongs_to` A公司

输出 relation candidate：

- `subject_node`
- `relation`
- `object_node`
- `confidence`
- `reason`
- `requires_user_confirmation`

这部分职责不应放进 participant identity merge。

### 4.4 Display Graph Builder

负责生成案卷图谱展示层，而不是让前端直接平铺 raw aggregation。

输出：

- `display_nodes`
- `display_edges`
- `merged_participant_nodes`
- `unresolved_raw_nodes`
- `event/fact/document nodes`
- `source_conversation_ids`
- `raw_node_ids`
- `pending_confirmation_tasks`

要求：

- confirmed identity 可以折叠
- unresolved same-label raw nodes 仍然分开
- raw graph 始终可回看

### 4.5 Graph Quality Warnings

只做提示，不直接改 raw graph。

例如：

- 某 event 被误当主体候选
- 某 candidate 缺少 `graph_node_id`
- 某 raw label 过于泛化
- 某 identity candidate 已被否定但重复出现
- 某 participant overlay 缺少 provenance

## 5. Display Graph 设计

### 5.1 未确认状态

如果两个会话都有 `A公司`，但用户未确认：

display graph 应显示：

- `A公司 会话 #674 待确认`
- `A公司 会话 #675 待确认`

重点：

- 不自动合并
- 不因为 label 相同就只画一个框
- 在节点或任务区标记 `pending_confirmation`

### 5.2 已确认状态

如果用户确认它们是同一主体：

display graph 应显示一个节点：

- `A公司`
  - 来源会话：`#674 #675`
  - raw nodes: `[...]`

而不是继续显示两个并列 `A公司`。

### 5.3 当事人 / 我 / 本人

同一案卷中多个会话的 `当事人 / 我 / 本人` 应支持合理显示名：

- 默认：`我方当事人`
- 或 `劳动者本人`
- 或 `申请人`

用户后续可改为：

- `我`
- `张三`
- `李四`
- 其他化名

但如果案情中存在多个自然人：

- 不应盲目自动合并为一个“当事人”
- 应生成 identity confirmation task

### 5.4 张三李四场景

如果用户输入：

`张三在A公司工作，李四是主管`

display graph 不应把：

- 张三
- 李四
- 当事人

混成一个主体。

正确方式：

- 张三、李四保留为不同 raw/display entity
- 仅在有足够 evidence 时生成 identity / affiliation 候选

### 5.5 公司的人事场景

“公司的人事”应作为角色主体显示：

- node kind: `role_entity` 或 `person_or_role`
- 可与 A公司 建立 `affiliation` / `represents` relation

而不是和 A公司 做 identity merge。

## 6. 推荐数据结构

### 6.1 Dossier Intelligence Result

```json
{
  "dossier_id": 51,
  "trigger": "graph_updated",
  "summary_update": {
    "auto_summary": "...",
    "timeline": [],
    "key_facts": [],
    "evidence_gaps": []
  },
  "identity_candidates": [],
  "affiliation_candidates": [],
  "display_graph": {
    "display_nodes": [],
    "display_edges": [],
    "unresolved_raw_nodes": []
  },
  "graph_quality_warnings": [],
  "agent_trace": []
}
```

### 6.2 Identity Candidate

```json
{
  "task_type": "entity_identity",
  "source_node": {
    "conversation_id": 674,
    "graph_node_id": "...",
    "label": "A公司",
    "type": "organization"
  },
  "target_node": {
    "conversation_id": 675,
    "graph_node_id": "...",
    "label": "A公司",
    "type": "organization"
  },
  "suggested_display_label": "A公司",
  "confidence": "high",
  "reason": "两个会话同属一个案卷，且显式组织名完全一致。",
  "requires_user_confirmation": true
}
```

### 6.3 Affiliation Candidate

```json
{
  "task_type": "affiliation_relation",
  "subject_node": {
    "label": "公司的人事",
    "type": "person_or_role",
    "conversation_id": 676
  },
  "relation": "represents_or_belongs_to",
  "object_node": {
    "label": "A公司",
    "type": "organization",
    "participant_id": 12
  },
  "confidence": "medium",
  "reason": "该表述出现在同一劳动争议案卷中，语境上承接 A公司 解除/补签争议。",
  "requires_user_confirmation": true
}
```

### 6.4 Display Node

```json
{
  "id": "participant:12",
  "kind": "participant",
  "display_label": "A公司",
  "entity_type": "organization",
  "source_conversation_ids": [674, 675],
  "raw_node_ids": ["...", "..."],
  "confirmed": true,
  "badges": ["会话 #674", "会话 #675"]
}
```

### 6.5 Unresolved Raw Node

```json
{
  "id": "raw:...",
  "kind": "raw_entity",
  "display_label": "A公司",
  "entity_type": "organization",
  "source_conversation_ids": [676],
  "confirmed": false,
  "pending_confirmation": true
}
```

## 7. 前端 UX 设计

### 7.1 图谱预览

前端不应直接平铺 raw nodes。

优先展示 `display_graph`：

- confirmed participant: 合并节点，显示多个会话 badge
- unresolved raw entity: 单独节点，显示待确认
- event/fact/document: 作为事件/事实节点展示，但不进入主体确认主任务
- role/affiliation: 通过关系边或提示卡展示

同时保留：

- raw graph debug 视图
- participant mappings debug 视图

### 7.2 主体确认主流程

普通用户主按钮不应叫“手动新建主体”，而应叫：

- `处理主体关系`
- `确认同一主体`
- `案卷智能整理`

主任务应变成：

- 这两个实体是同一个主体吗？
- 这个角色是否代表/隶属于某组织？

手动新建主体只放在高级设置或调试区。

### 7.3 确认后的效果

用户确认同一主体后：

- identity candidate 消失
- display graph 合并节点
- 合并节点显示多个会话来源
- advisor / summary 使用合并后的 display label

### 7.4 确认不同

用户确认不是同一个后：

- 两个 raw nodes 继续分开展示
- 记录 dismissed / negative decision
- 避免重复提示
- 不改 raw graph

## 8. 本轮分析 / 系统状态 / 组件状态 面板设计

### 8.1 本轮分析面板

当前前端只展示粗粒度 `agent_actions`。建议改成可追踪的多智能体执行轨迹：

- `router`
  - `route_intent`
  - `conversation_mode`
- `fact_updater`
  - `message_is_fact_update`
  - `case_summary_updated`
- `graph_extractor`
  - `extracted_nodes_count`
  - `extracted_edges_count`
- `graph_sync`
  - `task_id`
  - `actions_applied`
  - `status`
- `dossier_intelligence`
  - `identity_candidates_count`
  - `affiliation_candidates_count`
  - `summary_updated`
  - `graph_quality_warnings_count`
- `advisor`
  - `used_rag`
  - `used_dossier_context`
  - `used_graph_context`

### 8.2 系统状态 / 组件状态

建议补充状态项：

- Chroma
- Neo4j
- LLM
- Celery
- Graph Sync
- Dossier Intelligence Agent
- Summary capability

这样用户能区分：

- 图谱抽取是否工作
- 案卷智能整理是否工作
- 摘要是否更新

## 9. 分阶段实现建议

### H6c-0: Architecture Design

只写设计文档，不改业务代码。

### H6c-1: Backend Display Graph Builder

新增或扩展服务：

- `app/services/dossier_display_graph.py`
- 或集成到 `app/services/dossier_intelligence.py`

目标：

- 基于 raw graph + participant mappings 生成 `display_graph`
- confirmed nodes 合并展示
- unresolved nodes 分开展示
- 不改 raw graph

### H6c-2: Dossier Intelligence Agent Skeleton

新增：

- `app/services/dossier_intelligence.py`

首版建议：

- 先规则
- 再加轻量 LLM 辅助解释
- 不要一开始就把判断完全交给 LLM

输出：

- `identity_candidates`
- `affiliation_candidates`
- `graph_quality_warnings`
- `auto_summary_update`

### H6c-3: API

新增或扩展：

- `GET /dossiers/{dossier_id}/intelligence`
- `GET /dossiers/{dossier_id}/display-graph`

或者：

- `GET /dossiers/{dossier_id}/graph?view=display`

建议：

- raw graph 与 display graph API 分开
- 避免把 overlay 语义继续塞进旧 `/graph` 返回里

### H6c-4: Frontend

前端图谱预览改用 `display_graph`：

- confirmed participant 合并节点
- unresolved raw node 分开
- 多会话 badge
- event/fact 节点不进主体确认
- 手动新建主体降级为高级功能

### H6c-5: Summary Migration

把 auto summary 统一迁移到 Dossier Intelligence Agent。

要求：

- 保留旧 `POST /dossiers/{id}/generate-summary` 兼容
- 后台实现切换到 dossier intelligence orchestration

### H6c-6: Tests and Benchmark

新增测试：

- confirmed same participant merges display nodes
- unconfirmed same label remains separate
- self anchor display label
- 张三李四不误合并
- 公司的人事生成 affiliation candidate，而不是 identity candidate
- event nodes not in identity tasks
- display graph does not mutate raw graph

benchmark 方向：

- workspace participant benchmark 继续以 cross-conversation identity 为主
- 后续增加 display graph overlay benchmark

## 10. 风险

### 10.1 自动合并过度

风险：

- 同名不同实体被误并
- “当事人”类 generic node 被过早锚定

规避：

- 默认不自动永久合并
- 需要用户确认
- negative decision 可持久化

### 10.2 LLM 误判 affiliation

风险：

- 把 role relation 错判为 identity
- 把组织归属关系解释过度

规避：

- affiliation 首版先规则优先
- LLM 只给 reason，不直接写库
- relation candidate 和 identity candidate 严格分流

### 10.3 前端展示复杂度上升

风险：

- display graph / raw graph / candidates / debug 视图过多

规避：

- 普通用户默认只看 display graph
- raw/debug 视图折叠
- 手动主体创建降级到高级入口

### 10.4 display graph 与 raw graph 不一致

风险：

- overlay 展示看起来像改写了原图

规避：

- raw graph immutable
- display graph 明确标注 overlay 来源
- 保留 raw node ids / source conversation ids

### 10.5 旧 benchmark 需要调整

风险：

- 旧 benchmark 仍假设 raw graph 节点平铺
- 旧前端断言仍把“称呼任务”当主流程

规避：

- display graph 相关 benchmark 单独新增
- 保持 raw `/graph` 与新 `/display-graph` 并行一段时间

### 10.6 数据库 schema 迁移复杂度

风险：

- 如果加入 negative decisions / affiliation decisions / display cache，schema 复杂度会上升

规避：

- H6c 首阶段可先不做持久化 cache
- intelligence result 可以先按需计算
- 真正需要持久化的只有 user decisions

## 11. 结论

结论如下：

1. 应该新增 `Dossier Intelligence Agent`。
   原因是当前 dossier-level summary、participant identity、affiliation、display overlay、quality warning 已经形成一个独立的 orchestration 问题，不适合继续分散在 `conversation_store.py`、`dossier_graph.py` 和 `App.vue` 中。

2. 应该取消普通用户主流程中的“手动新建主体”。
   它应保留，但只能作为高级兜底功能。普通用户主流程应改为确认 identity / affiliation。

3. 应该引入 display graph overlay。
   当前 raw aggregation + `display_label` 只能做到轻微映射，无法表达“已确认合并显示、未确认分开展示”的核心产品语义。

4. 下一步最小实现应是 H6c-1。
   先做 backend display graph builder，而不是先做更复杂的 LLM agent。只有 display graph 出来，前端和 summary 才有稳定消费面。

建议的最小安全顺序：

1. H6c-0：完成本设计文档
2. H6c-1：实现 display graph builder，不改 raw graph
3. H6c-2：抽出 dossier intelligence skeleton，先只整合 identity candidates + display graph + warnings
4. H6c-3：暴露 `/display-graph` 和 `/intelligence`
5. H6c-4：前端切换到 display graph
6. H6c-5：summary 迁移到 dossier intelligence

