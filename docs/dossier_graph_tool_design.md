# Dossier Graph Tool 设计文档

## 1. 背景

到 H4 为止，个性化法律工作台已经具备以下基础能力：

- H3：`Dossier` / 案卷模型、会话绑定、案卷摘要、基于案卷新建会话
- H4a-H4c：dossier graph aggregation、图谱状态、图谱预览
- H4d：`dossier_graph_summary` 注入 advisor
- H4g：participant resolution，支持案卷内 confirmed participant 与 mention mapping

当前 advisor 在案卷会话中已经能读取：

- `dossier_context`
- `dossier_graph_summary`

但这两类信息仍然是“被动注入”的背景块，存在两个限制：

1. 上下文是预先整理好的摘要，粒度固定，不适合按需查看某个主体或某类事实。
2. advisor 不能在回答过程中主动检查“当前 dossier 下还有哪些结构化事实或主体关系”。

因此，H5 的目标不是让模型获得任意图数据库权限，而是提供一个受控、只读、范围严格限定的 dossier graph query 工具，使 advisor 在必要时可以主动查询当前案卷图谱，用于补充案情背景、回忆历史事实、检查主体关系。

## 2. 核心原则

H5 的设计必须坚持以下原则：

### 2.1 图谱只作为案情背景，不作为法律依据

- 图谱负责“案情记忆”和“结构化案情组织”
- 法律依据仍必须来自 laws / cases RAG
- 不允许把图谱内容直接当作法律规则来源

### 2.2 查询范围严格限制在当前用户、当前 dossier

- 只能查询当前登录用户拥有的 dossier
- 只能查询当前会话绑定的 dossier
- 不允许跨案卷查询
- 不允许跨用户查询

### 2.3 temporary 不应被图谱工具重新放大

- `temporary` 会话本身不应写结构化案情和图谱
- 工具不能反向绕过 H2c 语义
- 如果某个会话没有形成可查询图谱，工具应返回空或不调用

### 2.4 participant label 只是案卷内确认称呼

- confirmed participant label 代表用户在当前 dossier 内确认的称呼
- 不等于真实姓名
- 不要求真实姓名
- 未确认主体不能被默认视为同一主体

### 2.5 冲突时优先澄清

如果：

- 图谱中的历史事实与用户本轮输入冲突
- confirmed participant 称呼与用户本轮说法不一致
- 历史案情和当前补充事实出现明显冲突

则 advisor 应优先澄清，而不是强行沿用旧图谱内容。

## 3. 工具边界

H5 工具必须是严格受限的只读工具。

本阶段明确不做以下事情：

- 不写 Neo4j
- 不写 SQL
- 不创建 participant
- 不自动合并主体
- 不修改 conversation / dossier 状态
- 不返回其他用户或其他 dossier 数据
- 不提供任意 Cypher 执行能力

因此，这个工具的定位不是“图数据库代理”，而是“当前案卷结构化背景查询接口”。

## 4. 建议工具接口

建议工具接口如下：

```python
query_dossier_graph(
    dossier_id: int,
    query: str,
    focus_participant_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]
```

### 4.1 参数含义

- `dossier_id`
  - 当前案卷 ID
  - 仅用于后端校验当前会话绑定的 dossier
  - 不应由 LLM 自由指定
- `query`
  - 用户侧自然语言意图，例如“公司相关事实”“有哪些主体”“有哪些证据线索”
  - 后端只做有限模式匹配和过滤
- `focus_participant_id`
  - 可选
  - 用于查看某个 confirmed participant 相关的事实、邻接关系、mentions
- `limit`
  - 结果上限
  - 需要做强限制，防止过大返回

### 4.2 dossier_id 的控制原则

这里最关键的一点是：

- dossier_id 不应由 LLM 自由构造
- dossier_id 应由后端从当前 conversation 上下文注入或校验
- 如果当前 conversation 没有绑定 dossier，则工具不可调用

也就是说，LLM 不是在“选择查哪个 dossier”，而是在“当前 dossier 已确定的前提下，选择是否查询以及查什么方向”。

### 4.3 query 的约束

`query` 可以是自然语言，但后端只应支持有限解释，例如：

- 查询主体列表
- 查询某个 participant 的相邻事实
- 按关键词过滤节点或关系
- 返回 dossier graph 摘要与统计

不允许：

- 将 query 拼接为任意 Cypher
- 将 query 当作数据库表达式执行
- 开放式图遍历

## 5. 查询能力分级

为了控制复杂度，建议把能力分为三个层级。

### Level 1：返回案卷图谱摘要和统计

能力：

- conversation_count
- node_count
- edge_count
- confirmed participants
- participant aliases
- 主要节点和主要关系的摘要

适用问题：

- “这个案卷目前都有哪些主体和事实？”
- “这个案子现在图谱里大概记录了什么？”

### Level 2：按 participant 查询相邻节点和关系

能力：

- 基于 `focus_participant_id` 返回该 confirmed participant 对应的 raw mentions
- 返回与其相关的节点、边、关系摘要
- 支持查看“这个主体在当前 dossier 中出现过哪些结构化关系”

适用问题：

- “这个案卷里之前提到的公司是谁？”
- “我和公司之间有哪些关键事实？”
- “A公司相关的关系有哪些？”

### Level 3：按关键词过滤节点和关系

能力：

- 根据 query 中的关键词过滤节点
- 根据 relation / label / raw_label / display_label 做有限匹配
- 返回命中的结构化事实片段

适用问题：

- “这个案子里有哪些证据相关信息？”
- “之前提到过哪些解除事实？”
- “有哪些和工资相关的历史记录？”

### 本阶段明确不做

- 任意 Cypher
- 跨案卷实体搜索
- LLM 自动写图谱
- 自动主体合并

## 6. 安全策略

H5 的安全重点是“工具权限受限”和“输出语义受限”。

### 6.1 当前用户校验

后端必须强制：

- `current_user` 校验
- dossier ownership 校验
- 当前 conversation ownership 校验

### 6.2 当前会话绑定关系校验

后端应强制保证：

- `conversation.dossier_id == dossier_id`

这意味着即使用户拥有多个 dossier，当前会话中也不能随意查另一个 dossier。

### 6.3 只接受当前会话上下文中的 dossier_id

从实现策略上，建议：

- 工具层不直接暴露一个“任意 dossier_id”接口给 LLM
- dossier_id 应由后端从当前 state 注入
- 如果 state 中无 dossier_id，工具直接不可用

### 6.4 输出条数限制

必须设置：

- 默认 `limit`
- 最大 `limit`
- 节点数、边数、关系数上限

避免：

- prompt 膨胀
- 大量无关历史信息污染回答
- 通过工具变相导出全量 dossier 图谱

### 6.5 source 标记

工具输出应明确标记：

- `source = graph_context`

这样后续 advisor 在引用内容时，可以清楚区分：

- 这是图谱背景
- 不是法律依据

### 6.6 prompt 侧限制

advisor prompt 中必须明确：

- `graph_context` 不能作为法律依据引用
- 不允许写出“根据图谱可知法律规定……”
- 图谱中的关系可能包含系统推断，不等于已经证实

## 7. Advisor 使用策略

这个工具不是默认总调用，而是只在特定场景下按需调用。

### 7.1 适合调用的场景

例如：

- “这个案卷里之前提到的公司是谁？”
- “我和公司之间有哪些关键事实？”
- “这个案子目前有哪些证据和主体？”
- “根据已有案情，下一步该补充什么事实？”

这些问题本质上是在问：

- dossier 里记住了什么
- 当前 dossier 下的主体关系是什么
- 哪些事实已经有结构化记录

### 7.2 不适合调用的场景

以下场景不应调用：

- 纯法律问题
- 显式法条查询
- 法律依据检索
- benchmark 常规问答

原因很简单：

- 这些问题需要 laws / cases RAG
- 图谱不能提供法律依据

### 7.3 当前会话无 dossier_id 时不调用

如果当前会话没有绑定 dossier：

- 不调用工具
- 直接沿用现有 advisor / RAG 路径

### 7.4 LEGAL_QA 不调用

如果当前 mode 是 `LEGAL_QA`：

- 不调用 dossier graph 工具
- 保持法律问答纯净路径

### 7.5 本轮用户输入优先级更高

如果用户本轮明确提供新事实，例如：

- “实际入职时间不是 2021 年，是 2022 年”
- “对方不是 A 公司，是 B 公司”
- “那件事后来已经撤回”

则：

- 优先以本轮输入为准
- 必要时提示：`历史案情显示……，请确认该信息是否仍然成立`

## 8. 与 RAG 的关系

H5 必须明确把“案情记忆”和“法律依据”分层。

### 8.1 graph_context 负责案情记忆

graph_context 适合回答：

- 之前记录过哪些主体
- 谁和谁之间有什么关系
- 哪些事实曾被结构化记录

### 8.2 laws/cases RAG 负责法律依据

laws / cases RAG 适合回答：

- 法条规定是什么
- 法律上能主张什么
- 风险、流程、仲裁请求、证据要求的法律依据是什么

### 8.3 最终回答应显式区分信息来源

advisor 最终回答建议区分为两类表述：

- `根据你案卷中已确认的信息……`
- `根据检索到的法律依据……`

不允许出现：

- `根据图谱可知法律规定……`
- `根据图谱可以认定你依法一定胜诉……`

图谱可以帮助模型记住案情，但不能替代法律推理依据。

## 9. 测试计划

H5 后续实施时，建议至少覆盖以下测试点：

### 9.1 调用前置条件

- 非 dossier 会话不会调用工具
- `LEGAL_QA` 不调用工具
- `temporary` 不产生可查询结构化图谱

### 9.2 正常可用路径

- tracked dossier 会话可查询当前 dossier 图谱
- 按 participant 查询时仅返回当前 dossier 内相关内容
- 按关键词过滤时结果受限且结构稳定

### 9.3 权限与边界

- 跨用户 dossier_id 被拒绝
- 跨案卷 dossier_id 被拒绝
- `conversation.dossier_id != dossier_id` 被拒绝
- query 不可注入任意 Cypher
- 返回结果包含 `graph_context` 标记

### 9.4 advisor 行为

- advisor 回答不会把 graph_context 当法律依据
- 当历史图谱与当前用户输入冲突时，优先澄清
- 有 dossier graph 工具后，既有 pytest 和 benchmark 不应退步

## 10. 实施拆分

建议拆分为四步：

### H5a：设计文档

- 输出本设计说明
- 明确边界、权限和使用策略

### H5b：后端只读 query_dossier_graph service/tool

- 实现只读 graph query service
- 加入 ownership / dossier / conversation 校验
- 只支持有限查询能力

### H5c：advisor policy 接入

- 在 advisor / agent state 中注入受控工具
- 明确何时可调用、何时不可调用
- prompt 加入 graph_context 使用约束

### H5d：测试与手测矩阵更新

- 增加单元测试、接口测试、策略测试
- 更新手测矩阵，加入 dossier graph tool 的联调验收项

## 11. 生产化补充方向

本设计首先追求“可控可答辩”，生产化阶段还可以进一步增强：

- 更强的权限审计
- 工具调用日志与追踪
- tool input / output 观测
- 更细粒度的 rate limit 和 result truncation
- dossier graph tool 的调用统计和误用分析

这些能力在毕设阶段不是阻塞项，但对后续真实部署很重要。

## 12. 结论

H5 的核心不是让 advisor 获得“更强的图数据库能力”，而是让它在当前 dossier 内、当前用户权限下、以受控方式读取结构化案情背景。

这条能力链的定位可以概括为：

- H3 解决“案卷承载”
- H4 解决“案卷图谱聚合与主体确认”
- H5 解决“advisor 何时、如何、在什么边界内主动读取案卷图谱”

最终目标是：

> 让 advisor 更好地记住案情，而不是让图谱替代法律依据。  
> 让工具增强连续性，而不是扩大权限边界。
