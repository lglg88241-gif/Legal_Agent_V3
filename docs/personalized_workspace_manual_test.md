# 个性化法律工作台手测矩阵

## 1. 目的

本手测矩阵用于答辩前对 `feat/personalized-legal-workspace` 分支的 H2-H4 个性化法律工作台能力进行真实前后端联调验收。

适用范围：

- H2：mode consent、`temporary / tracked / unknown`
- H3：Dossier / 案卷、会话绑定、摘要、基于案卷新建会话
- H4：案卷级图谱聚合、图谱预览、participant resolution、schema hardening、Neo4j `elementId()` 替换

## 2. 使用说明

- 建议测试人：`_______`
- 测试日期：`_______`
- 当前分支：`feat/personalized-legal-workspace`
- 当前登录用户：`_______`
- 当前后端地址：`_______`
- 当前前端地址：`_______`

通过/失败记录建议格式：

- `[] 通过`
- `[] 失败`
- `问题记录：_______`

本手测矩阵只记录验收过程，不提交以下内容：

- `.env`
- `e2e_tests/logs/`
- 临时 probe / latency 文件
- benchmark 运行中生成但尚未确认需要入库的报告文件

## 3. 观测建议

手测时建议同时打开以下观测面：

- 前端页面
- 浏览器 Network / Console
- 后端日志
- MySQL 数据检查工具
- Neo4j Browser / Cypher Shell

建议重点关注以下字段或现象：

- `conversation_mode`
- `persistence_consent`
- `case_summary`
- `case_state_json`
- `case_type`
- `dossier_id`
- dossier `summary / auto_summary`
- dossier graph `nodes / edges / stats`
- `confirmed_participants / participant_mappings / display_label / raw_label`

## 4. 手测矩阵

### 环境准备

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| 环境准备 | 1. 执行 `git status --short`。<br>2. 启动后端，例如 `python -m uvicorn main:app --reload`。<br>3. 如需异步任务，启动 Celery worker。<br>4. 启动前端，例如 `cd frontend && npm run dev`。<br>5. 登录测试账号并记录当前用户。<br>6. 检查 Neo4j / MySQL / 云端 LLM 配置可用。<br>7. 确认 `.env` 仅作本地使用。 | 1. 工作区预期只剩 `M .env`。<br>2. 前后端可正常访问。<br>3. 登录用户清晰可确认。<br>4. Neo4j / MySQL / LLM 配置可用。<br>5. `.env` 不进入提交范围。 | `[] 通过` `[] 失败` | 若本地 vLLM 未启动，不应阻塞系统初始化；云端 LLM 可用即可。 |

### A. 纯法律问答

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| A：纯法律问答 | 1. 在普通新会话中输入：`劳动合同法第八十七条规定了什么？`。<br>2. 观察前端交互、Network、后端日志。<br>3. 必要时查看数据库中该 conversation 的结构化字段是否变化。 | 1. 正常回答《劳动合同法》第八十七条内容。<br>2. 不弹 `mode consent`。<br>3. 不写 `case_summary / case_state_json / case_type`。<br>4. 不触发 `graph update`。<br>5. 回答中引用的“第四十七条”不会被 phantom citation guard 误删。 | `[] 通过` `[] 失败` | 可顺手再测：`劳动合同法第82条规定了什么？`，验证 exact lookup 稳定性。 |

### B. 临时案情分析 temporary

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| B：temporary 案情分析 | 1. 输入一个具体劳动争议案情，例如：`我在公司工作两年，公司一直没签劳动合同，昨天通知我明天不用来了。`<br>2. 当出现 mode consent 时，选择“仅本次分析”。<br>3. 等待回答返回。<br>4. 刷新页面并检查历史。<br>5. 必要时检查数据库和图谱。 | 1. 普通 `user / assistant messages` 被保存。<br>2. 不写 `case_summary / case_state_json / case_type`。<br>3. 不更新图谱。<br>4. 后端日志出现 `temporary` skip structured persistence / skip graph update 类似信息。<br>5. 刷新页面后聊天记录仍在，但不形成持续案卷图谱。 | `[] 通过` `[] 失败` | 若前端关闭或取消弹窗，草稿应恢复，不应静默丢失。 |

### C. 持续跟踪 tracked

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| C：tracked 持续跟踪 | 1. 输入具体案情。<br>2. 当出现 mode consent 时，选择“保存并持续跟踪”。<br>3. 完成 tracked onboarding。<br>4. 等待回答与后台任务完成。 | 1. `conversation_mode = TRACKED_CASE`。<br>2. `persistence_consent = tracked`。<br>3. 写入 `case_summary / case_state_json / case_type`。<br>4. 触发 `graph update`。<br>5. 可以进入案卷绑定或创建流程。 | `[] 通过` `[] 失败` | 如为基于 dossier 新建的会话，第一次提问不应再弹 mode consent。 |

### D. Dossier 创建与绑定

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| D：Dossier 创建与绑定 | 1. 在前端创建一个名为“劳动争议测试案卷”的 dossier。<br>2. 将上一条 tracked conversation 绑定到该 dossier。<br>3. 打开案卷详情。<br>4. 如有需要，检查 `GET /dossiers` 和 conversation 详情。 | 1. `GET /dossiers` 能看到新案卷。<br>2. `conversation.dossier_id` 正确写入。<br>3. 案卷详情中能看到绑定会话。 | `[] 通过` `[] 失败` | 若绑定不同案情会话，前端应先提示混杂风险。 |

### E. 案卷摘要

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| E：案卷摘要 | 1. 打开 dossier 详情页。<br>2. 先填写一段手动 `summary`。<br>3. 点击“生成 AI 摘要”或“刷新智能摘要”。<br>4. 如果方便，可模拟 LLM 不可用场景再次测试。 | 1. `auto_summary` 更新。<br>2. 若 LLM 成功，生成结构化智能摘要。<br>3. 若 LLM 失败，fallback 摘要仍然用户可读，不暴露原始 JSON、`mode=`、`consent=` 等技术字段。<br>4. 不覆盖用户手动 `summary`。 | `[] 通过` `[] 失败` | 可记录 `auto_summary_updated_at` 和 `auto_summary_source_count` 是否同步更新。 |

### F. 案卷图谱状态与预览

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| F：案卷图谱状态与预览 | 1. 打开 dossier 详情中的图谱状态卡片。<br>2. 刷新图谱状态。<br>3. 查看图谱预览。<br>4. 同时观察后端日志。 | 1. 正常显示会话数、节点数、关系数。<br>2. 图谱节点和关系预览可见。<br>3. 不再出现 Neo4j `id()` deprecation warning。<br>4. `display_label / raw_label` 行为正常。 | `[] 通过` `[] 失败` | 若图谱为空，应显示空状态而不是前端报错。 |

### G. 主体确认 participant resolution

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| G：participant resolution | 1. 在案卷中查看主体候选。<br>2. 将“公司 / 用人单位 / 对方公司”确认为同一主体，例如 `A公司`。<br>3. 刷新案卷详情与图谱预览。<br>4. 再次查看摘要或追问回答表现。 | 1. `confirmed participant` 出现在主体列表。<br>2. `mention mappings` 正常生成。<br>3. 图谱预览优先显示 `A公司`。<br>4. 原始 `raw_label` 仍可见。<br>5. advisor / summary 使用确认后的案卷内称呼。<br>6. 不要求真实姓名。 | `[] 通过` `[] 失败` | 可再确认“我 / 本人 / 劳动者 / 员工”是否可按同样方式确认。 |

### H. 基于案卷新建会话追问

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| H：基于案卷新建会话追问 | 1. 从 dossier 详情中点击“基于此案卷新建会话”。<br>2. 在新会话中输入：`我下一步应该准备哪些证据？`<br>3. 观察前端交互、回答内容、后端日志和结构化持久化情况。 | 1. 不再弹 `mode consent`。<br>2. 不误写结构化案情或图谱。<br>3. advisor 能使用 `dossier context / dossier_graph_summary / confirmed participants` 作为案情背景。<br>4. 法律依据仍来自 RAG `laws / cases`。 | `[] 通过` `[] 失败` | 该场景重点验证“案卷追问”和“事实补充”已被区分。 |

### I. 误绑定 / 混杂风险

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| I：误绑定 / 混杂风险 | 1. 找一个明显不同案情的 conversation。<br>2. 尝试把它绑定到当前 dossier。<br>3. 观察前端确认提示。<br>4. 点击取消。 | 1. 前端出现“不同案情可能导致摘要和图谱混杂”的风险提示。<br>2. 用户确认前不静默绑定。<br>3. 用户取消时不绑定。 | `[] 通过` `[] 失败` | 若继续确认绑定，应记录后续图谱和摘要是否混杂，作为风险示例。 |

### J. 多案卷隔离

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| J：多案卷隔离 | 1. 创建第二个 dossier。<br>2. 在第二个 dossier 中也使用相同主体名称“公司”。<br>3. 分别查看两个 dossier 的主体列表、图谱和摘要。 | 1. 不自动跨案卷合并。<br>2. participant registry 仅在各自 dossier 内生效。<br>3. 图谱聚合只看当前 dossier 的 conversations。 | `[] 通过` `[] 失败` | 可观察同名主体在两个 dossier 中是否具有独立 mapping。 |

### K. 多用户隔离

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| K：多用户隔离 | 1. 使用两个用户或两个 token。<br>2. 用户 A 尝试访问用户 B 的 dossier、conversation、participants、graph API。<br>3. 用户 A 尝试把用户 B 的 conversation 绑定进自己的 dossier。 | 1. 用户 A 不能访问用户 B 的 dossiers。<br>2. 用户 A 不能绑定用户 B 的 conversation。<br>3. 用户 A 不能读取或修改用户 B 的 participants。<br>4. graph API 不泄漏其他用户动态图谱。 | `[] 通过` `[] 失败` | 建议同时记录 HTTP 状态码，例如 `403 / 404`。 |

### L. 删除与归档一致性

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| L：删除与归档一致性 | 1. 清空一个 conversation。<br>2. 删除一个 conversation。<br>3. 归档一个 dossier。<br>4. 必要时检查 MySQL 和 Neo4j。 | 1. 清空或删除 conversation 后，`participant mentions` 不残留。<br>2. `messages` 不残留，或按既有语义被清理。<br>3. 若图谱清理失败，可见 warning，但不阻断 SQL 清理结果返回。<br>4. `archive dossier` 不硬删 `participants / mentions / conversations`。 | `[] 通过` `[] 失败` | 可顺带关注是否还有异常 `SAWarning`。 |

### M. 回归命令

| 场景名 | 操作步骤 | 预期结果 | 通过/失败记录 | 备注 |
|---|---|---|---|---|
| M：回归命令 | 1. 运行 `.venv\\Scripts\\python.exe -m pytest tests\\ -q`。<br>2. 运行 `npm run build`。<br>3. 如时间允许，运行 full benchmark：<br>`$env:PYTHONPATH='.'; $env:PYTHONIOENCODING='utf-8'; uv run python e2e_tests/quality_benchmark.py`。 | 当前基线：<br>1. `pytest 401 passed`。<br>2. `frontend build passed with existing chunk warning`。<br>3. benchmark 历史基线：`65/80 = 81%`。 | `[] 通过` `[] 失败` | full benchmark 约 14 分钟，若未执行，应在备注中注明原因。 |

## 5. 结论记录

- 本轮手测结论：`_______`
- 阻断问题：`_______`
- 可延期问题：`_______`
- 是否允许进入答辩前冻结：`_______`

## 6. 非提交项提醒

完成手测后，继续保持以下规则：

- 不提交 `.env`
- 不提交 `e2e_tests/logs/`
- 不提交临时 probe / latency 文件
- 若运行了 benchmark，先人工确认报告是否需要留存，再决定是否整理
