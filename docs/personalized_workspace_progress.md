# 个性化法律工作台阶段进展

## 1. 产品定位

Legal Agent V3 当前正在从“单轮法律问答系统”升级为“面向普通用户与专业用户的个性化、智能化法律助手”。

这套工作台能力的核心目标不是把系统做成传统律师案管软件，而是在保留法律问答能力的前提下，补齐以下闭环：

- 支持纯法律问答，不强制进入案卷或持续跟踪
- 支持用户描述具体案情后的分析、整理和后续补充
- 支持在用户明确同意后，将会话升级为可持续维护的案卷型工作流
- 支持围绕案卷组织多个会话，而不是把所有上下文都压在单个 conversation 上

因此，当前产品定位是：

- 面向普通用户：提供法律问答、案情分析、持续案卷跟踪
- 面向专业用户：提供更结构化的案情整理与会话组织能力
- 不等同于完整律师事务所案管系统

## 2. H2 模式与同意机制

H2 的目标，是把“所有咨询都只是 conversation”升级为“区分会话模式，并在持续保存前征求用户同意”。

### 2.1 会话模式枚举

当前 `Conversation.conversation_mode` 使用四种模式：

| mode | 含义 |
|---|---|
| `UNKNOWN` | 默认状态，尚未形成明确模式判断 |
| `LEGAL_QA` | 纯法律问答，例如法条、期限、程序、标准咨询 |
| `CASE_ANALYSIS` | 用户在描述具体案情，但尚未进入持续跟踪 |
| `TRACKED_CASE` | 用户已明确同意持续跟踪的案情会话 |

### 2.2 mode consent 协议

`/dialog` 当前支持可选字段：

- `mode_consent_enabled`

其默认值为 `false`，这是 H2 保持兼容性的关键：

- 旧客户端不需要改动即可继续工作
- benchmark 默认路径不被改变
- 只有前端显式开启时，才会触发模式提示和用户同意交互

### 2.3 persistence consent 语义

当前 `Conversation.persistence_consent` 使用三种值：

| consent | 当前语义 |
|---|---|
| `temporary` | 只保留普通消息历史，不写结构化案情与图谱 |
| `tracked` | 允许持续跟踪，写结构化案情和图谱 |
| `unknown` | 兼容旧行为，不主动阻断现有持久化链路 |

实际执行规则如下：

- `temporary`
  - 保存普通聊天 messages
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
  - 为兼容旧客户端和 benchmark，保持旧行为
  - 不作为阻断条件使用

### 2.4 H2 的边界价值

H2 解决的是“产品协议”和“保存同意”问题，而不是完整案卷承载问题。  
持续跟踪的正式承载对象在 H3 由 `Dossier` 补上。

## 3. H3 案卷闭环

H3 的目标，是把持续跟踪从“只挂在 conversation 上”升级为“有正式案卷对象承载”。

### 3.1 Dossier 模型

当前 `Dossier` 主要字段为：

| 字段 | 说明 |
|---|---|
| `id` | 案卷主键 |
| `user_id` | 所属用户 |
| `title` | 案卷标题 |
| `case_type` | 案由/类型 |
| `summary` | 用户手动维护摘要 |
| `auto_summary` | AI 自动生成摘要 |
| `auto_summary_updated_at` | 自动摘要更新时间 |
| `auto_summary_source_count` | 自动摘要使用的来源会话数 |
| `status` | `active / archived / closed` |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

### 3.2 Conversation 与 Dossier 绑定

`Conversation` 当前新增：

- `dossier_id`

含义：

- 普通法律问答、临时分析会话可以不绑定 dossier
- 已持续跟踪或用户希望整理到某个案卷中的会话，可以绑定 dossier

### 3.3 已完成的后端闭环

当前已完成：

- Dossier CRUD
- `Conversation.dossier_id`
- `POST /conversations/{id}/attach-dossier`
- `POST /conversations` 支持可选 `dossier_id`
- `GET /dossiers/{id}` 返回案卷详情与绑定会话列表

关键 API 如下：

| API | 说明 |
|---|---|
| `GET /dossiers` | 获取当前用户案卷列表 |
| `POST /dossiers` | 创建案卷 |
| `GET /dossiers/{dossier_id}` | 获取案卷详情 |
| `PATCH /dossiers/{dossier_id}` | 更新案卷基础信息 |
| `DELETE /dossiers/{dossier_id}` | 当前实现为 archived 语义 |
| `POST /dossiers/{dossier_id}/generate-summary` | 手动生成/刷新智能摘要 |
| `POST /conversations/{conversation_id}/attach-dossier` | 将当前会话绑定到案卷 |
| `POST /conversations` + `dossier_id` | 基于案卷直接创建新会话 |

### 3.4 已完成的前端闭环

当前前端已经具备：

- 案卷抽屉入口
- 创建案卷
- 将当前会话绑定到某个案卷
- 在案卷详情中查看绑定会话列表
- 在案卷详情中编辑手动摘要与基础字段
- 基于某个案卷新建会话

因此，H3 已经形成了一个最小但完整的案卷工作流：

1. 创建案卷  
2. 绑定当前会话，或直接基于案卷新建会话  
3. 在案卷详情中查看已绑定会话  
4. 手动维护案卷摘要  
5. 使用 AI 刷新案卷智能摘要

## 4. 智能化能力

H3 不是只增加一个“案卷容器”，还增加了围绕案卷的智能辅助能力。

### 4.1 advisor dossier_context 注入

当当前会话绑定了 `dossier_id` 时，后端会在进入 advisor 前加载案卷上下文，并注入到 advisor prompt。

`dossier_context` 当前主要包含：

- dossier title
- dossier case_type
- dossier summary
- dossier status
- 绑定会话简要列表
- 绑定会话的 `case_summary` 等摘要信息

### 4.2 dossier_context 的使用边界

当前对 advisor 的约束非常明确：

- `dossier_context` 只作为背景上下文
- 不能替代 RAG 检索到的法律依据
- 法律结论仍必须基于检索到的法条、案例和 grounded prompt
- 如果案卷上下文与当前用户输入冲突，应优先询问澄清，而不是直接沿用旧案情

这保证了案卷上下文只提升连续性，不侵蚀法律依据的可靠性。

### 4.3 AI 自动摘要

H3e 新增了 dossier 智能摘要能力：

- 字段为 `auto_summary`
- 用户显式点击后生成
- 不自动后台刷新
- 不覆盖用户手动 `summary`

自动摘要生成输入来源包括：

- dossier 标题
- dossier 类型
- dossier 手动摘要
- 绑定 conversations 的
  - 标题
  - `conversation_mode`
  - `persistence_consent`
  - `case_type`
  - `case_summary`
  - `case_state_json`
  - 最近几条消息

生成结果采用结构化中文整理，当前建议结构包括：

- 案情概览
- 已知事实
- 争议焦点
- 证据线索
- 已讨论法律问题
- 后续待确认事项

### 4.4 双摘要机制

当前 dossier 摘要能力采用“双摘要并存”设计：

- `summary`
  - 用户手动维护
  - 体现用户自己的表达和主观整理

- `auto_summary`
  - AI 基于绑定会话自动整理
  - 只作为辅助视图
  - 不覆盖用户输入

这个设计的价值是：

- 保留用户可控性
- 避免 AI 自动整理覆盖用户重点
- 同时保留系统级智能汇总能力

## 5. 数据隔离与安全边界

H2/H3 的所有新增能力，都建立在严格的用户隔离之上。

### 5.1 current_user 隔离

当前系统中：

- `Conversation` 按 `current_user` 隔离
- `Dossier` 按 `current_user` 隔离
- conversation 绑定 dossier 时，必须同时校验两者都属于当前用户
- 跨用户的 dossier / conversation 访问会返回失败

### 5.2 图谱边界

当前图谱写入仍然按 `conversation_id` 工作：

- H3 不做 dossier-level graph 写入
- H3 不重构现有 `update_graph_task`
- H3 不把多个 conversation 自动聚合进 dossier 级图谱

这意味着当前案卷层是“工作台层”和“上下文组织层”，不是新的图谱写入层。

### 5.3 法律问答与案情图谱隔离

H2 热修后，纯法律问答已经从主链路上与案情图谱隔离：

- 纯法条/法律规定问题走 `GENERAL_CONSULT`
- 不触发 `fact_updater`
- 不触发 `update_graph_task`
- 不写用户案情图谱

这条边界对 H3 非常重要，因为案卷上下文不能反过来污染纯法律问答的 grounding 逻辑。

## 6. 当前不做的事情

为保证范围稳定，当前 H3 明确不做以下事项：

- 不做完整律师案管系统
- 不自动创建案卷
- 不让 `temporary` 写结构化案情或图谱
- 不让案卷摘要替代法律 RAG
- 不在 H3 引入 dossier-level graph
- 不做 dossier 级自动定时摘要刷新
- 不做基于 dossier 的复杂多会话自动聚合推理链

这些边界是当前阶段质量稳定的前提。

## 7. H4 方向

H4 可以沿着“案卷从工作台容器升级为更强的知识组织对象”的方向继续推进。

优先方向包括：

- dossier-level graph aggregation
- 案卷图谱同步状态与回填机制
- 案卷图谱可视化
- 大模型工具化查询案卷子图

更具体地说，H4 可以考虑：

- 将多个绑定 conversation 的图谱聚合为 dossier 级只读视图
- 展示 dossier 级实体、关系和时间线
- 为 advisor / planner 增加 dossier 子图查询工具
- 为 dossier 自动摘要引入更稳定的刷新策略和差异更新策略

## 8. 当前阶段结论

到 H3e 为止，个性化法律工作台已经具备以下最小闭环：

- 能区分纯法律问答、案情分析、持续跟踪
- 能在持续保存前征求用户同意
- 能用 `temporary / tracked / unknown` 控制结构化持久化边界
- 能以 `Dossier` 承载持续案情
- 能把多个会话组织到同一案卷下
- 能在会话回答阶段读取案卷背景上下文
- 能同时支持用户手动摘要与 AI 自动摘要

这意味着系统已经从“只有 conversation 的法律聊天系统”，升级为“具备案卷对象、持续跟踪协议和智能整理能力的个性化法律工作台”。

## 9. 当前基线

当前阶段基线如下：

- `pytest`: 361 passed
- `npm run build`: 通过
- 前端构建只有既有 chunk size warning，无新增构建错误

当前分支：

- `feat/personalized-legal-workspace`
