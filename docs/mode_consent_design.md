# H2a：智能模式识别与用户同意保存设计

## 1. 设计目标

本阶段目标不是把 Legal Agent V3 改造成“默认建卷”的系统，而是在现有 `conversation` 基础上补齐三件事：

1. 区分纯法律问答和具体案情分析。
2. 在用户开始描述具体案情时，系统可以识别并建议进入更合适的模式。
3. 任何“持续跟踪案情”的结构化保存，都必须经过用户明确同意。

设计上需要坚持以下边界：

- `route_intent` 解决“本轮怎么处理”。
- `conversation_mode` 解决“这个会话整体是什么类型”。
- `persistence_consent` 解决“是否允许持续跟踪保存”。

这三个概念不能混用。

## 2. 现有模式与边界检测能力

### 2.1 当前 route_intent 能力

当前主工作流实际使用的是四类 `route_intent`：

| route_intent | 含义 | 主要去向 |
|---|---|---|
| `GREETING` | 寒暄/问候 | 直接固定回复 |
| `GENERAL_CONSULT` | 一般法律咨询 | 向量检索 + advisor |
| `CASE_UPDATE` | 在既有案情上下文中补充、修正、追问 | fact_updater + 检索 + reflector |
| `OUT_OF_SCOPE` | 境外法律或非法律请求 | 直接拒绝 |

补充说明：

- `app/agent/nodes/router.py` 内部还存在 `CHITCHAT` / `TOOLS` 这样的中间判断常量，但对主图流程真正生效的是上表四类。
- `app/agent/graph.py` 根据 `route_intent` 决定是否进入 `fact_updater`、`vector_researcher`、`reflector`。

### 2.2 当前 case_boundary 能力

`app/services/case_boundary.py` 当前负责判断“是不是新案情”。

输入：

- `current_graph_summary`
- `recent_history`
- `user_input`

输出：

- `SAME_CASE`
- `NEW_CASE`

当前接入点：

- `app/api/dialog.py` 在 `/dialog` 里先调用 `detect_case_boundary(...)`
- 若结果为 `NEW_CASE` 且 `force_current_case = false`，返回：
  - `status = needs_confirmation`
  - `action = suggest_new_case`

前端当前处理方式：

- `frontend/src/App.vue` 只识别 `action === 'suggest_new_case'`
- 用一个确认弹窗让用户选择：
  - 新建会话
  - 继续当前会话

也就是说，现有系统已经有“边界确认”，但没有“模式确认”或“保存同意”。

### 2.3 当前精准案情梳理开关

当前前端只有一个开关，文案类似“精准案情梳理”，但它实际上混合了两层语义：

| 字段 | 当前作用 |
|---|---|
| `is_precise_mode` | 在 router 中几乎等价于“直接按 `CASE_UPDATE` 处理” |
| `proactive_graph_mode` | 在 reflector 中控制是否更激进地要求实体澄清、图谱侧缺失信息 |

当前问题：

- `frontend/src/App.vue` 发送消息时，把同一个 `proactiveGraphMode` 值同时传给了 `is_precise_mode` 和 `proactive_graph_mode`。
- 这是一种“Agent 推理策略开关”，不是产品层面的“会话模式”。

结论：

- H2 不能复用 `is_precise_mode` / `proactive_graph_mode` 作为产品模式。
- 产品模式必须单独设计。

### 2.4 当前 Conversation 可承载字段

`Conversation` 目前已有：

| 字段 | 现状 |
|---|---|
| `title` | 已有 |
| `case_summary` | 已有 |
| `case_state_json` | 已有 |
| `case_type` | 已有 |
| `updated_at` | 已有 |

当前没有：

| 字段 | 现状 |
|---|---|
| `conversation_mode` | 不存在 |
| `persistence_consent` | 不存在 |
| `mode_confirmed_at` | 不存在 |
| `mode_reason` | 不存在 |

## 3. 推荐模式枚举

### 3.1 推荐的最小版本

H2b 推荐只落四种 `conversation_mode`：

| mode | 含义 | 是否持久化为持续跟踪 | 触发条件 | 前端显示文案 |
|---|---|---|---|---|
| `UNKNOWN` | 初始状态，尚未形成明确判断 | 否 | 新建会话或未判定 | 未分类 |
| `LEGAL_QA` | 纯法律问答、法条、程序、标准咨询 | 否 | 无具体个人案情，仅一般法律咨询 | 法律问答 |
| `CASE_ANALYSIS` | 用户在描述具体案情，但尚未同意持续跟踪保存 | 否 | 出现较明确案情事实，但未同意 tracked | 案情分析 |
| `TRACKED_CASE` | 用户明确同意持续跟踪的案情会话 | 是 | 用户手动确认保存或手动切换 | 持续跟踪 |

### 3.2 不建议 H2b 立即落地的模式

`DOCUMENT_ASSIST` 建议先保留为后续扩展，而不是 H2b 的最小落地模式。

原因：

- 目前文书生成并不是独立持久化闭环。
- 它更像“能力入口”而不是“会话底层状态”。
- 过早加入会让前端和后端状态机复杂化。

建议：

- H2b 不新增 `DOCUMENT_ASSIST`
- 在 H3/H4 再把它作为 `conversation_submode` 或任务态引入

### 3.3 route_intent 与 conversation_mode 的边界

| 维度 | 定义 | 生命周期 |
|---|---|---|
| `route_intent` | 本轮用户输入应该如何进入 Agent 流程 | 单轮 |
| `conversation_mode` | 当前会话整体是什么类型 | 会话级 |
| `persistence_consent` | 用户是否允许持续跟踪保存 | 会话级 |

示例：

- 用户在 `CASE_ANALYSIS` 会话中继续补充证据，本轮依然可能是 `CASE_UPDATE`。
- 用户在 `TRACKED_CASE` 会话里提一个纯法条问题，本轮也可能是 `GENERAL_CONSULT`。

因此：

- `CASE_UPDATE` 不等于 `TRACKED_CASE`
- `GENERAL_CONSULT` 也不等于 `LEGAL_QA`

## 4. 用户同意交互流程

### 4.1 场景 A：纯法律问题

示例：

> 劳动合同试用期最长多久？

推荐行为：

- 直接回答
- `detected_mode = LEGAL_QA`
- `conversation_mode` 可更新为 `LEGAL_QA`
- 不弹保存提示

### 4.2 场景 B：首次描述具体案情

示例：

> 我工作 5 年，月薪 12000，公司违法辞退我

推荐行为：

- 系统识别为 `CASE_ANALYSIS`
- 不自动进入 `TRACKED_CASE`
- 弹出轻量确认，不阻断回答太久

推荐提示文案：

> 我注意到你正在描述一个具体案情。是否要保存为持续跟踪的案情会话？

推荐动作：

1. `仅本次分析`
2. `保存并持续跟踪`

其中：

- 选择“仅本次分析”后，继续回答，但不进入 tracked 持久化
- 选择“保存并持续跟踪”后，会话升级为 `TRACKED_CASE`

### 4.3 场景 C：继续补充同一案情

示例：

> 我还有聊天记录和工资流水

推荐规则：

- 若当前 `conversation_mode = TRACKED_CASE`：
  - 直接继续更新案情
  - 不再重复提示

- 若当前 `conversation_mode = CASE_ANALYSIS` 且 `persistence_consent = temporary`：
  - 不要每轮都弹
  - 建议采用“保守二次提醒”策略：
    - 只有当用户第二次或第三次持续补充结构化案情时，再给一次非强制提示

推荐原因：

- 连续每轮弹窗会严重打断体验
- 但完全不再提示，又会失去“升级为持续跟踪”的机会

建议阈值：

- 首次 case-like 输入提示一次
- 用户明确选择“仅本次分析”后，本会话至少间隔 2 轮 case-like 更新才可再次轻提示

### 4.4 场景 D：可能开启新案情

当前系统已有 `suggest_new_case`。

H2 需要解决的问题不是“再加一个弹窗”，而是“把新案情边界确认和保存同意合并成一套确认决策”。

推荐策略：

#### 优先级规则

1. `NEW_CASE` 判断优先于 `TRACKED_CASE` 建议
2. 同一次请求最多只返回一个确认弹层
3. 不允许“新案情确认”和“是否持续跟踪保存”连续叠两个弹窗

#### 推荐合并方式

如果满足：

- `case_boundary = NEW_CASE`
- 且 `detected_mode = CASE_ANALYSIS`

则返回一个合并确认：

- 是否新建会话
- 若新建，会不会持续跟踪

可选动作建议：

1. `新建并持续跟踪`
2. `新建但仅本次分析`
3. `继续在当前会话`

### 4.5 推荐 dialog response 字段

```json
{
  "status": "needs_confirmation",
  "action": "mode_consent",
  "detected_mode": "CASE_ANALYSIS",
  "conversation_mode": "LEGAL_QA",
  "suggested_mode": "TRACKED_CASE",
  "requires_persistence_consent": true,
  "case_boundary": "SAME_CASE",
  "consent_prompt": "我注意到你正在描述一个具体案情。是否要保存为持续跟踪的案情会话？",
  "consent_actions": [
    {"label": "仅本次分析", "value": "temporary"},
    {"label": "保存并持续跟踪", "value": "tracked"}
  ]
}
```

合并新案情时可返回：

```json
{
  "status": "needs_confirmation",
  "action": "case_transition",
  "detected_mode": "CASE_ANALYSIS",
  "conversation_mode": "TRACKED_CASE",
  "case_boundary": "NEW_CASE",
  "requires_persistence_consent": true,
  "consent_prompt": "看起来你开启了一个新的具体案情。是否新建为持续跟踪的案情会话？",
  "consent_actions": [
    {"label": "新建并持续跟踪", "value": "new_tracked"},
    {"label": "新建但仅本次分析", "value": "new_temporary"},
    {"label": "继续当前会话", "value": "stay_current"}
  ]
}
```

## 5. 数据库最小改造方案

### 5.1 方案 1：直接在 Conversation 上加字段

建议字段：

| 字段 | 类型 | 建议默认值 |
|---|---|---|
| `conversation_mode` | `VARCHAR(32)` | `UNKNOWN` |
| `persistence_consent` | `VARCHAR(32)` | `unknown` |
| `mode_confirmed_at` | `DATETIME NULL` | `NULL` |
| `mode_reason` | `TEXT NULL` | `NULL` |

优点：

- 与当前 `create_all + runtime ALTER TABLE` 机制最兼容
- 修改点少
- H2b 可以快速闭环
- 前端列表、搜索、badge 展示都容易接入

缺点：

- 如果以后有复杂模式历史、审计记录，会显得不够细
- 和未来 Dossier 模型存在一定重复

### 5.2 方案 2：新增 ConversationModeState 表

建议结构：

- `conversation_id`
- `mode`
- `persistence_consent`
- `confirmed_at`
- `reason`
- `updated_by`

优点：

- 更规范
- 更适合后续做 mode history / consent history
- 未来切 Dossier 时更容易解耦

缺点：

- 当前项目没有 Alembic，新增表和服务层改动更重
- H2b 只是“轻量闭环”，上来就单独建表 ROI 不高

### 5.3 推荐方案

H2b 推荐使用方案 1：直接扩展 `Conversation`。

原因：

- 当前系统还没有 Dossier 模型
- H2b 的目标是最小闭环，不是最终范式
- 与现有 `case_summary / case_state_json / case_type` 放在同一实体上更自然

建议把复杂的 consent history 留到后续 Dossier 阶段再抽表。

## 6. 后端实现方案

### 6.1 数据模型

在 `Conversation` 增加：

- `conversation_mode`
- `persistence_consent`
- `mode_confirmed_at`
- `mode_reason`

并在 `ensure_storage_schema()` 里沿用 runtime `ALTER TABLE` 补列。

### 6.2 `/dialog` 请求扩展

为了不破坏现有调用，建议新增一个兼容字段：

```json
{
  "mode_consent_enabled": false
}
```

推荐默认值：

- 默认 `false`
- 只有前端 H2 UI 准备好后才显式传 `true`

原因：

- 当前 benchmark 和旧客户端都假设 `/dialog` 会直接回答或仅触发现有 `suggest_new_case`
- 若默认开启 consent 提示，会直接改变链路行为

### 6.3 `/dialog` 返回扩展

当 `mode_consent_enabled = true` 时：

- 后端在 `case_boundary` 之后、任务投递之前，先做 `detected_mode` 判断
- 若需要用户确认，则返回 `status = needs_confirmation`
- 若不需要确认，则继续现有 `task_id` 流程

### 6.4 新增 consent endpoint

建议新增：

`POST /conversations/{id}/mode-consent`

请求体：

```json
{
  "mode": "TRACKED_CASE",
  "consent": "tracked"
}
```

推荐语义：

- `mode`
  - 目标会话模式
- `consent`
  - `unknown`
  - `temporary`
  - `tracked`

返回：

```json
{
  "success": true,
  "conversation": {
    "id": 123,
    "conversation_mode": "TRACKED_CASE",
    "persistence_consent": "tracked"
  }
}
```

### 6.5 detected_mode 判定建议

建议不要把检测逻辑塞进 `route_intent`。

推荐单独增加一个轻量函数，例如：

- `detect_conversation_mode(...)`

输入：

- 当前会话的 `conversation_mode`
- 当前 `case_summary`
- 当前 `chat_history`
- 新的 `user_input`

输出：

- `detected_mode`
- `suggested_mode`
- `requires_persistence_consent`
- `reason`

### 6.6 持久化策略建议

H2b 推荐把“聊天历史保存”和“案情持续跟踪保存”分开。

#### 层 1：聊天历史

保持现状：

- 非 `GREETING` 仍保存 user/assistant message

原因：

- 这是当前产品的基础能力
- benchmark、会话列表、导出、回看都依赖它

#### 层 2：持续跟踪结构化保存

只有在 `persistence_consent = tracked` 时，才执行：

- 更新 `case_summary`
- 更新 `case_state_json`
- 更新 `case_type`
- 触发 `update_graph_task`

换言之，H2b 对“仅本次分析”的定义建议是：

- 保存聊天记录
- 不保存结构化持续跟踪状态
- 不触发图谱更新

这是 H2b 最重要的边界。

## 7. 前端实现方案

### 7.1 顶部模式 Badge

在当前会话顶部展示一个轻量 badge：

- 法律问答
- 案情分析
- 持续跟踪
- 未分类

### 7.2 手动模式切换

建议允许用户手动切换，但必须保守：

- 用户可手动切到 `LEGAL_QA`
- 用户可手动切到 `CASE_ANALYSIS`
- 切到 `TRACKED_CASE` 时必须再次确认

原则：

- 自动识别只做建议，不做强制
- 最终选择权在用户

### 7.3 consent 弹层

推荐新增一个统一弹层组件，替代未来多个并列确认框。

弹层文案建议明确区分两层保存：

> 当前聊天记录会保存在会话历史中，方便你继续查看和追问。  
> 只有在你同意后，系统才会把案情整理为可持续跟踪的结构化案情。

这样可以解释“用户拒绝 tracked，但 conversation 仍存在”并不矛盾。

### 7.4 SessionList 展示

在 H1b 基础上补充：

- 列表项显示 mode 标签
- `case_type` 与 `conversation_mode` 分开

建议：

- `case_type` 表示法律领域，如“劳动争议”
- `conversation_mode` 表示会话状态，如“持续跟踪”

### 7.5 与 H1b 的关系

- H1b 的摘要展示继续保留
- H1b 的搜索能力继续保留
- mode 筛选可放到后续，不必在 H2b 一次做完

## 8. 与现有 case_boundary 的合并策略

### 8.1 合并原则

- `case_boundary` 解决“是不是新案情”
- `mode_consent` 解决“要不要持续跟踪保存”
- 两者不能各弹一次

### 8.2 推荐优先级

| 条件 | 优先处理 |
|---|---|
| `case_boundary = NEW_CASE` | 先处理新案情边界 |
| `case_boundary = SAME_CASE` 且 `detected_mode = CASE_ANALYSIS` | 再处理保存同意 |

### 8.3 推荐实现方式

统一由 `/dialog` 返回一个 `needs_confirmation` 包：

- `action = case_transition`
- `action = mode_consent`

不要再让前端自己拼多个弹窗。

### 8.4 对现有 `force_current_case` 的建议

`force_current_case` 先保留，但语义收窄为：

- 仅用于“用户已明确选择继续当前会话”

不要让它承担“我同意保存 tracked”的语义。

## 9. 风险与缓解

### 9.1 风险：当前所有消息都会入库，“仅本次分析”定义不清

现状：

- `ask_agent_task` 对非 `GREETING` 会持久化 user/assistant message
- 当前 `CASE_UPDATE` 还会更新 `case_summary` / `case_state_json` / `case_type`
- 还会触发 `update_graph_task`

风险：

- 用户以为“仅本次分析 = 不保存任何东西”
- 但当前系统其实仍会保存完整聊天记录

缓解建议：

- H2b 明确区分两层保存：
  - 聊天历史保存
  - 持续跟踪保存
- UI 必须写清楚：
  - “仅本次分析”并不等于“无痕模式”
  - 它表示“不进入结构化持续跟踪”

是否后续做真正 ephemeral：

- 建议后续再做
- 不建议 H2b 直接上真正 ephemeral

原因：

- 当前持久化链路、benchmark、会话列表都依赖 `conversation + messages`
- 现在强做无痕会显著扩大改动面

推荐结论：

- H2b：先不做真正 ephemeral
- H2c/H3：再设计“无痕咨询 / 临时会话”

### 9.2 风险：用户拒绝保存，但 conversation 已创建，看起来矛盾

不矛盾。

推荐解释方式：

- `conversation` 是聊天容器
- `TRACKED_CASE` 是结构化持续跟踪状态

推荐 UI 文案：

> 当前聊天仍会保存在会话历史中，方便你回看和继续提问。  
> 只有在你同意后，系统才会把案情整理为持续跟踪的结构化案情。

### 9.3 风险：mode 检测误判

缓解建议：

- 默认只做建议，不自动升级为 `TRACKED_CASE`
- 允许用户手动切换
- 使用保守提示，不强打断
- 对首次 case-like 输入才提示，避免频繁打断

结论：

- 误判的成本必须低于“自动保存误伤”的成本
- 宁可漏提示，也不要误保存

### 9.4 风险：影响 benchmark 和现有 65/80

这是 H2b 最大工程风险。

现状：

- `quality_benchmark.py` 现在会在 `/dialog` 返回 `needs_confirmation` 时自动重试 `force_current_case = true`
- benchmark 默认假设系统会继续答题，而不是停在新的 consent 流程里

风险：

- 若 H2b 把 mode consent 默认插入 `/dialog`
- benchmark 可能被迫走确认链路
- 直接影响 D3、D4、D5、D8

缓解建议：

#### 建议 1：新增 `mode_consent_enabled`，默认关闭

- 旧客户端和 benchmark 不传时，行为保持现状
- 只有 H2 前端显式开启后，才出现 mode consent 流程

#### 建议 2：quality_benchmark 不默认 tracked

不建议让 `quality_benchmark` 默认进入 `TRACKED_CASE`。

原因：

- benchmark 目标是评估答题质量，不是评估保存同意 UX
- tracked 应该是单独专项测试，不应强行并入当前 80 分制

#### 建议 3：不要改变 `CASE_UPDATE` 的判定语义

`CASE_UPDATE` 继续只表示：

- 本轮是在补充/追问当前案情

不要把它绑定成：

- “用户已同意持续跟踪”

这样可以避免打坏现有 routing 质量。

### 9.5 风险：temporary 模式削弱多轮连续性

如果用户选择“仅本次分析”，而系统不更新：

- `case_summary`
- `case_state_json`
- 图谱

那么多轮连续性确实会弱于 `TRACKED_CASE`。

这是预期内 tradeoff，不是 bug。

缓解建议：

- 继续保存原始消息，保留最基础上下文
- 在用户多轮补充后，再轻提示升级为持续跟踪
- benchmark 保持旧链路，不受该 tradeoff 影响

## 10. H2b 实施建议

### 10.1 是否建议直接实现

建议直接实现 H2b，但必须按“兼容优先、默认不破坏现有链路”的方式做。

不建议一步到位实现：

- 真正 ephemeral
- Dossier 模型
- 复杂 mode history
- 搜索按 mode 筛选

### 10.2 推荐分阶段实现

#### Phase 1：后端兼容字段与只读返回

目标：

- 为 `Conversation` 增加 mode / consent 字段
- `/dialog` 支持返回 `detected_mode` 等信息
- 默认 `mode_consent_enabled = false`

涉及文件：

- `app/models/entities.py`
- `app/services/conversation_store.py`
- `app/api/dialog.py`

收益：

- 先把协议准备好
- 不影响现有 benchmark

风险：

- 很低

#### Phase 2：consent endpoint + 前端弹层

目标：

- 增加 `POST /conversations/{id}/mode-consent`
- 前端支持“仅本次分析 / 保存并持续跟踪”
- 顶部显示 mode badge

涉及文件：

- `app/api/dialog.py`
- `app/api/conversations.py`
- `frontend/src/App.vue`
- `frontend/src/api/index.js`

收益：

- 产品闭环形成

风险：

- 中等，主要在交互一致性

#### Phase 3：持久化 gating

目标：

- `temporary` 不更新 `case_summary / case_state_json / case_type`
- `temporary` 不触发图谱更新
- `tracked` 保持现有结构化能力

涉及文件：

- `app/celery_app.py`
- `app/services/conversation_store.py`

收益：

- 真正实现“持续跟踪保存需同意”

风险：

- 中等偏高
- 需要重点回归 D3 / D5 / D8

#### Phase 4：专项评测

目标：

- 不改现有 `quality_benchmark` 主逻辑
- 单独增加 mode-consent 专项用例

收益：

- 保持 65/80 可比性
- 新功能单独评估

风险：

- 低

### 10.3 H2b 的推荐边界

H2b 推荐做到：

- mode 字段
- consent 字段
- UI 建议和确认
- tracked 与 temporary 的持久化分流

H2b 暂不做：

- 真正 ephemeral / 无痕咨询
- Dossier 新表
- Document Assist 独立模式
- mode 历史审计表
- benchmark 主评分体系改造
