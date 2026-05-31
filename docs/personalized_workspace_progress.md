# 个性化法律工作台进展

## 1. 产品定位

Legal Agent V3 当前的定位，不是单纯把系统做成“律师软件”或“法律版聊天机器人”，而是做成一个面向普通用户与专业用户的个性化智能法律助手。

它需要同时支持三类场景：

- 纯法律问答：例如“劳动合同法第八十七条规定了什么”
- 案情分析：例如“公司未签劳动合同，现在又通知我明天不用来了怎么办”
- 持续跟踪：用户希望围绕同一案情长期整理、追问、补充事实、维护摘要和查看结构化上下文

因此，这条产品线的核心不是“再做一个聊天框”，而是把法律问答、案情组织、持续跟踪、用户确认、图谱背景和数据边界组织成一个稳定闭环。

## 2. H2：模式识别与同意机制

H2 解决的是“系统如何区分普通法律问答和持续案情跟踪，以及何时允许结构化持久化”。

### 2.1 会话模式

当前 `Conversation.conversation_mode` 使用四种模式：

| mode | 含义 |
|---|---|
| `UNKNOWN` | 默认状态，尚未形成明确模式判断 |
| `LEGAL_QA` | 纯法律问答，例如法条、流程、期限、标准咨询 |
| `CASE_ANALYSIS` | 用户在描述具体案情，但尚未进入持续跟踪 |
| `TRACKED_CASE` | 用户已经明确同意持续跟踪的案情会话 |

### 2.2 mode consent 兼容策略

`/dialog` 支持可选字段 `mode_consent_enabled`，并且默认值保持为 `false`。

这个默认值很关键，因为它保证了：

- 旧客户端无需改动即可继续使用
- benchmark 默认路径不被打断
- 只有前端显式开启时，才会出现 mode consent 弹窗与用户确认流程

这意味着 H2 是增量能力，而不是破坏性升级。

### 2.3 三种持久化语义

当前 `Conversation.persistence_consent` 使用三种语义：

| consent | 当前语义 |
|---|---|
| `temporary` | 只保存普通消息，不写结构化案情和图谱 |
| `tracked` | 允许持续跟踪，写结构化案情和图谱 |
| `unknown` | 为兼容旧客户端和 benchmark，保持旧行为 |

对应执行边界如下：

- `temporary`
  - 保存普通 `messages`
  - 不更新 `case_summary`
  - 不更新 `case_state_json`
  - 不更新 `case_type`
  - 不触发 `update_graph_task`
  - 不写用户案情图谱
- `tracked`
  - 保持持续跟踪语义
  - 允许更新结构化案情
  - 允许触发图谱更新
- `unknown`
  - 继续兼容旧路径
  - 不会因为“不是 tracked”而被误拦截

### 2.4 H2 的安全边界

H2 后续热修也补齐了两个重要边界：

- 纯法律问答不会再污染用户案情图谱
- 显式法条问答支持 exact lookup fast path，避免 query expansion 和 rerank 冲掉“法名 + 条号”的硬约束

可以概括为：

> 语义检索解决“我该怎么办”；精确法条定位解决“第几条是什么”。

## 3. H3：Dossier 案卷闭环

H3 的目标，是把持续跟踪能力从“只挂在 conversation 上”升级为“有正式案卷对象承载”。

### 3.1 Dossier 模型

当前 `Dossier` 是持续案情的正式聚合对象，核心字段包括：

- `id`
- `user_id`
- `title`
- `case_type`
- `summary`
- `auto_summary`
- `auto_summary_updated_at`
- `auto_summary_source_count`
- `participant_aliases_json`
- `status`
- `created_at`
- `updated_at`

其中：

- `summary` 是用户手动维护摘要
- `auto_summary` 是 AI 或 fallback 规则生成的智能摘要
- `participant_aliases_json` 用于保存案卷内用户确认的称呼锚点

### 3.2 会话绑定与基于案卷新建会话

`Conversation` 新增了 `dossier_id`。

这带来了两类能力：

- 可以把现有会话绑定到某个案卷
- 可以直接基于某个案卷创建新会话

当前后端能力包括：

- `GET /dossiers`
- `POST /dossiers`
- `GET /dossiers/{dossier_id}`
- `PATCH /dossiers/{dossier_id}`
- `DELETE /dossiers/{dossier_id}`，当前为 `archived` 语义
- `POST /conversations/{conversation_id}/attach-dossier`
- `POST /conversations` 支持可选 `dossier_id`

当通过 `dossier_id` 创建 conversation 时，后端会直接将其初始化为：

- `conversation_mode = TRACKED_CASE`
- `persistence_consent = tracked`
- `mode_reason = "created_from_dossier"`

这样可以避免基于案卷新建会话后再次弹出 mode consent。

### 3.3 案卷摘要

H3e 以后，案卷摘要采用“双摘要并存”设计：

- 手动摘要 `summary`
  - 由用户明确编辑和维护
- 智能摘要 `auto_summary`
  - 由用户手动点击生成
  - 不覆盖 `summary`
  - LLM 不可用时可降级为规则型 fallback

当前智能摘要只作为整理能力，不替代法律依据，也不替代用户自己的案卷表述。

### 3.4 dossier_context 注入 advisor

当当前会话绑定了 `dossier_id`，后端会在进入 advisor 前加载 `dossier_context` 并注入 prompt。

`dossier_context` 当前主要包含：

- dossier 标题
- dossier 案由
- dossier 手动摘要
- dossier 状态
- 绑定会话列表
- 绑定会话的 `case_summary`
- participant aliases / confirmed participants

但这里有一个明确约束：

- dossier 上下文只作为案情背景
- 不能替代 RAG 检索到的法律依据
- 如果 dossier 背景与当前输入冲突，优先澄清，而不是盲目沿用旧结论

## 4. H4：案卷级图谱与个性化上下文

H4 不改 Neo4j 写入 schema，也不重写 `update_graph_task`，而是在现有 `conversation_id` 写入模型上，增加 dossier 视角的只读聚合与展示。

### 4.1 案卷级只读图谱聚合

当前已经支持：

- `GET /dossiers/{dossier_id}/graph`

聚合逻辑是：

1. 找到该 dossier 绑定的 conversation ids
2. 只聚合当前用户、且属于这些 conversation ids 的动态案情图谱节点和关系
3. 返回 nodes、edges、stats、participant aliases、warning 等信息

这里的关键设计点是：

- 不改变 Neo4j 写入 schema
- 不做 dossier-level graph 写回
- 不做跨会话实体合并
- 只做 dossier 视角的只读聚合

这让 H4 能在低风险前提下快速形成可展示、可注入、可扩展的图谱层。

### 4.2 dossier_graph_summary 注入 advisor

在 dossier 绑定会话中，advisor 除了能读 `dossier_context`，还能读 `dossier_graph_summary`。

该摘要当前包含：

- 图谱统计
- 主要实体
- 主要关系
- participant aliases
- 截断提示和 warning

但它的角色仍然被限制为：

- 案情背景
- 用于提醒模型已有结构化上下文
- 不能替代法律检索结果
- 不能把图谱中的推断关系当作已证实事实

因此，图谱是案情背景，不是法律依据。

### 4.3 前端图谱状态与预览

前端当前已经形成最小可演示闭环：

- 图谱状态卡片
  - conversation_count
  - node_count
  - edge_count
  - generated_at
  - warning
- 图谱预览卡片
  - 节点 chips
  - 关系列表
  - 空图 / warning / 截断提示

这不是完整图可视化，但已经能清楚展示“案卷下已经聚合出哪些结构化案情信息”。

## 5. H4g：participant resolution

H4g 解决的是案卷内主体同一性问题，也就是“我 / 公司 / 老板 / HR / 对方”在不同会话中如何稳定对齐。

### 5.1 案卷内主体 registry / mapping

当前系统引入了两层 SQL 结构：

- `DossierParticipant`
  - 表示案卷内用户确认过的稳定主体
- `DossierParticipantMention`
  - 把原始 mention 映射到某个 confirmed participant

这套设计的意义在于：

- 不要求真实姓名
- 不让 LLM 静默猜实名
- 不直接改 Neo4j 底层图谱实体
- 只在案卷层做“用户确认的稳定主体”管理

### 5.2 候选只建议，不自动确认

系统当前可以根据 dossier graph 和 aliases 生成 candidate，例如：

- 公司 / 用人单位 / 对方公司
- 我 / 本人 / 劳动者 / 员工

但这些 candidate 只会作为建议输出，不会自动变成 confirmed mapping。

真正的稳定主体，必须由用户确认后才成立。

### 5.3 明确的身份边界

H4g 的身份策略是保守的：

- 不要求真实姓名
- 不让 LLM 静默猜身份
- 不跨案卷自动合并
- 同名主体也不默认是同一实体
- 未确认 mention 不能直接当作稳定主体使用

这使得 participant resolution 更像“案卷内用户确认的身份锚点层”，而不是“自动实体融合层”。

## 6. H4i：schema hardening

H4i 解决的是个性化工作台落地后的数据库稳定性问题。

### 6.1 索引补强

本轮补强了 personalized workspace 相关表的核心索引：

- `dossiers`
  - `user_id`
  - `user_id + status`
  - `updated_at`
- `conversations`
  - `user_id`
  - `dossier_id`
  - `user_id + dossier_id`
  - `user_id + updated_at`
  - `conversation_mode`
- `dossier_participants`
  - `dossier_id`
  - `user_id`
  - `dossier_id + label`
- `dossier_participant_mentions`
  - `dossier_id`
  - `conversation_id`
  - `participant_id`
  - `dossier_id + raw_label`
  - `dossier_id + conversation_id`

注意：

- 没有强加 `UNIQUE(dossier_id, label)`
- 保留用户在同一案卷中维护多个同名主体的可能性

### 6.2 runtime schema ensure 幂等

当前 schema 策略是：

- `create_all()` 负责新表创建
- runtime ensure 负责旧库补列、补索引

这套逻辑已经做到：

- 多次启动幂等
- SQLite 测试环境可用
- MySQL 运行路径兼容

也就是说，H2/H3/H4 的新字段与新索引，既能落在新库上，也能安全补到旧库上。

### 6.3 conversation 删除一致性

H4i 还修复了两个删除一致性问题：

- 删除 conversation 时，会清理相关 `dossier_participant_mentions`
- 清空 conversation messages 时，也会清理相关 `dossier_participant_mentions`

这样可以避免出现：

- SQL conversation 已删，但 mention 仍指向无效 `conversation_id`
- 清空对话后，图谱映射层仍残留无效 mention

### 6.4 dossier archive 语义

当前 dossier 删除仍然保持 `archived` 语义：

- 不硬删 conversations
- 不硬删 participants
- 不硬删 mentions

这保留了历史数据和后续恢复空间，也避免了个性化工作台早期阶段的大范围删除副作用。

## 7. 当前技术闭环

到 H4i 为止，个性化法律工作台已经形成了一条比较完整的技术闭环：

1. 用户发起法律问答或案情描述
2. 系统识别 `LEGAL_QA / CASE_ANALYSIS / TRACKED_CASE / UNKNOWN`
3. 如需持续跟踪，走 mode consent，而默认旧路径仍兼容
4. tracked 会话可绑定或创建 dossier
5. dossier 提供摘要、会话组织和 participant aliases
6. dossier 视角可读取只读图谱聚合
7. advisor 可同时读 dossier context 和 graph summary，但法律结论仍由 RAG 负责
8. participant registry 解决案卷内主体确认与 mention 对齐
9. schema hardening 保证旧库、新库和删除路径都能稳定运行

这条闭环对应的产品价值可以概括为：

> 语义检索解决“我该怎么办”；精确法条定位解决“第几条是什么”；  
> Dossier 解决“这个案子跟到哪了”；participant resolution 解决“我 / 公司 / 老板是不是同一主体”。

## 8. 答辩亮点

如果从答辩角度总结，当前 personalized workspace 最值得讲的点有五个：

- 它不是简单把聊天记录存起来，而是显式区分普通问答、案情分析和持续跟踪
- 它在持续持久化前加入了用户同意边界，而不是默认无限写入结构化案情
- 它把持续跟踪对象从 `conversation` 升级成了 `Dossier`
- 它没有冒险重构 Neo4j 写入，而是先通过 dossier-level read-only aggregation 形成稳定图谱视角
- 它把主体同一性问题放在用户确认的 SQL registry 层解决，而不是让 LLM 静默猜身份或自动跨案卷合并

## 9. 当前不做的事情

为了保持范围稳定，当前阶段明确不做：

- 不做完整律师案管系统
- 不自动创建 dossier
- 不让 `temporary` 写结构化案情或图谱
- 不让 dossier graph 替代法律 RAG
- 不做跨案卷主体自动合并
- 不改 Neo4j 写入 schema
- 不在当前阶段引入 Alembic 强迁移链路

## 10. 当前验证基线

当前基线如下：

- `pytest`: 399 passed
- `npm run build`: 通过
- 分支：`feat/personalized-legal-workspace`

其中：

- `pytest 399 passed` 已覆盖 H2/H3/H4/H4i 的核心回归
- 前端构建已通过，现有 warning 仍以既有 chunk size warning 为主

## 11. 后续路线

下一阶段可以沿着三条线继续推进：

- H5：让模型以工具方式读取 dossier 子图和 participant registry
- H6：引入用户 profile personalization，例如用户偏好、职业背景、风险偏好和常用输出格式
- 生产化阶段：从 runtime ensure 逐步过渡到 Alembic migration，形成更标准的 schema 演进链路
