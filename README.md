# Legal Agent V3

## 项目说明

这是一个面向中文法律问答场景的 RAG + Agent 项目，当前知识源包括：

- `data/corpus/` 下的法律条文 `.docx`
- `data/corpus/` 下的指导案例与典型案例 `.pdf`
- Neo4j 中抽取的案件实体关系图谱

## 如何更容易命中案例库

提问时尽量包含以下信息中的两项或以上：

- 纠纷类型或罪名
- 关键事实
- 当事人身份
- 你想要的结果，例如“有没有案例”“法院怎么判”“判决结果是什么”

推荐句式：

- `XX 情况算不算 XX？有没有类似案例？`
- `关于 XX 劳动争议，有没有指导案例？法院一般怎么认定？`
- `XX 罪 / XX 纠纷 的典型判决结果有哪些？`

## 可直接测试的案例命中问题

### 劳动争议

- `公司不签劳动合同，但长期安排考勤和发工资，算不算存在劳动关系？有没有指导案例？`
- `平台主播和网络公司之间是否成立劳动关系？有没有指导案例可以参考？`
- `员工离职后被主张竞业限制违约金，这种情况法院一般怎么判？有没有案例？`
- `拖欠劳动报酬后劳动者起诉追索工资，法院通常如何处理？有没有指导案例？`
- `员工与保险公司之间的劳动合同纠纷，法院会如何认定劳动关系和赔偿责任？有没有案例？`

### 个人信息与隐私

- `非法买卖公民个人信息会怎么判？有没有典型案例？`
- `公司或者个人泄露用户个人信息，除了民事赔偿，还有没有刑事案例可以参考？`
- `隐私权和个人信息保护纠纷中，法院支持精神损害赔偿吗？有没有案例？`

### 正当防卫 / 故意伤害

- `打架中反击把对方打伤，算不算正当防卫？有没有案例？`
- `面对持续侵害时反击过当和正当防卫的界限是什么？有没有指导案例？`
- `故意伤害案里，被害人先动手的情况下法院会怎么认定责任？有没有案例？`

### 危险驾驶 / 交通肇事

- `醉驾但情节较轻会怎么判？有没有危险驾驶的典型案例？`
- `交通肇事后积极赔偿并取得谅解，量刑会减轻吗？有没有案例？`
- `危险驾驶和交通肇事的区别是什么？有没有案例能对比说明？`

### 合同纠纷

- `建设工程施工合同纠纷中，发包方拖欠工程款，法院通常怎么判？有没有案例？`
- `买卖合同中一方收货后不付款，法院一般支持哪些证据？有没有案例？`
- `金融借款合同纠纷中，保证人是否一定承担责任？有没有案例？`
- `房屋买卖合同履行中反悔违约，法院一般如何处理？有没有案例？`

### 环境污染 / 公益诉讼

- `企业非法倾倒危险废物，需要承担哪些民事责任？有没有环境公益诉讼案例？`
- `污染环境刑事附带民事公益诉讼中，法院会不会判修复生态环境？有没有案例？`
- `环境污染案件里，公司负责人和实际控制人会不会一起承担责任？有没有案例？`

### 名誉权 / 婚姻家事 / 监护

- `网络发言侵害名誉权，法院一般如何认定侵权和赔偿？有没有案例？`
- `离婚后子女抚养权或探望权纠纷，法院通常怎么判？有没有案例？`
- `监护人资格被撤销需要满足什么条件？有没有案例？`

## 建议优先测试的问题

如果你现在只想做联调验证，优先用下面这 8 个问题：

1. `公司不签劳动合同，但长期考勤和发工资，算不算存在劳动关系？有没有指导案例？`
2. `主播和网络平台之间是否构成劳动关系？有没有指导案例？`
3. `员工离职后被公司主张竞业限制违约金，法院一般怎么判？有没有案例？`
4. `非法买卖公民个人信息会怎么判？有没有典型案例？`
5. `打架中反击致人受伤，算不算正当防卫？有没有案例？`
6. `醉驾情节较轻会怎么判？有没有危险驾驶案例？`
7. `企业非法倾倒危险废物，要承担哪些责任？有没有环境公益诉讼案例？`
8. `网络发言侵害名誉权，法院一般怎么判？有没有案例？`

## 命中率更高的小技巧

- 带上 `有没有案例`、`有没有指导案例`
- 带上 `法院怎么判`、`判决结果是什么`
- 尽量不要只问非常抽象的短句，例如 `劳动关系怎么认定`
- 如果首轮没有命中，就把问题补充成”事实 + 法律问题 + 想看案例”

---

## 架构重构记录：去屎山化工程（2026-04）

### 重构总览

本轮重构的目标是将 1944 行的巨型单体文件 `legal_agent.py` 拆解为职责清晰、可独立测试的模块群。采用严格的 TDD 工作流：每一刀手术前锁定基线快照测试（68 项），全绿后方可推进下一步。

### 已完成的手术

| 手术 | 交付物 | 说明 |
|------|--------|------|
| Surgery #1 | `app/schemas/case_state.py` | Pydantic 强类型域模型 `CaseState`，替代裸 dict 传递案件量化事实。通过 `model_validator` 自动计算派生字段（可主张月数、二倍工资差额、总索赔额）。 |
| Surgery #2 | `app/logic/calculators.py` | 纯 Python 确定性计算管线 `build_claim_payload(CaseState) → PayloadResult`。LLM 仅负责润色，不得覆写此处产出的数字。 |
| Surgery #3 | `app/agent/prompts/advisor.py`、`extractor.py` | Prompt 统一管理。消除 `graph_extractor.py` 中同一 prompt 被定义三次的问题；所有 prompt 常量集中至 `app/agent/prompts/` 包。 |
| Surgery #4a | `app/services/legal_agent.py` 瘦身 | 物理删除全部 V1 和 V2 废弃逻辑（528 行死代码），包括被 monkey-patch 覆盖的旧版 router/reflector/advisor/clarifier 及中间过渡版本。文件从 1903 行缩减至 1375 行。 |
| Surgery #4b | `app/agent/nodes/` 节点拆分 | 将 `legal_agent.py` 中剩余的内联节点函数物理迁移至独立模块（advisor、clarifier、fact_updater、reflector、researcher、router、_llm）。文件净减少 803 行（1375 → ~572 行），V1/V2 历史兼容层已彻底清除。`legal_agent.py` 降级为薄壳编排器，仅保留 LangGraph StateGraph 组装逻辑。 |
| Surgery #5 | Pydantic 全面接管，字典时代终结 | 将所有节点（advisor、clarifier、reflector）从 `_build_layered_advisor_payload_v3` 切换到 `build_claim_payload(CaseState.from_text(...))`，消除 dict 下标访问风险。`CaseState` 数值字段全部加 `ge=0` 边界约束，在数据契约层拒绝非法负值。物理删除 `legal_agent.py` 剩余 340 行 legacy 函数，文件从 571 行缩减至 229 行，仅保留 `LegalGraphAgent` 类。新增 `tests/test_case_state_hardening.py` 7 项防御测试（负值拒绝、零工资、空输入、属性访问语义、validator 一致性）。全量 75 项测试全绿（1.59s）。 |
| Surgery #6 | LLM 幻觉三层防护——数字锁、引用锁、角色降级 | 根本原因：LLM 在「精校」环节私自重算数字或脑补《劳动合同法》第82条等未经 RAG 验证的条文编号。防护方案：① 将 LLM 角色从"法律专家"降级为"文本精校员"；② `<number_lock>` 块禁止任何数字改写，优先级最高；③ `<citation_lock>` 块严禁在 RAG 之外补写"第N条"；④ 后生成守卫 `sanitize_llm_answer()`：发现陌生显著数字（≥1000）→ 整体回退到确定性骨架；发现幻觉条文编号 → 外科式删除。新增 `tests/test_advisor_hallucination_defense.py` 15 项纯确定性测试（T1 数字异常检测×7，T2 幻觉条文检测×5，T3 集成×3）。全量 90 项测试全绿。 |
| Surgery #7 | CalculationTrace 白盒数据契约 + VERBATIM 超额纠错注入 | 根本原因："惩罚恐惧"——用户问"我能要144000元吗"，后端计算结果为132000，LLM 持有 `<number_lock>` 却不知道为何差12000，选择回避该话题。解决方案：Python 预生成完整 `CorrectionNotice`（含"首月不计入"、可主张月数、双方数字、仲裁驳回风险警告），通过 `<correction_notice priority="VERBATIM">` 块强制 LLM 原样转述——"精校员只需复读，无需推理"。同步增强：① 超额检测上下文感知：仅在30字符关键词窗口内的数字（"双倍工资"/"赔偿"/"能要"等）才触发纠错，排除案号等无关大数；② `correction_notices` 为列表，支持多项超额并发；③ `<citation_demotion_guard>` 阻止 LLM 在 RAG 未收录时自行补写法律条文编号；④ 守卫白名单扩展：`_extract_notice_numbers()` 将纠错文本中的数字加入允许集，防止"忠实复述纠错内容"被误判为幻觉。新增 `tests/test_calculation_trace.py` 17 项零 LLM 测试（T1 轨迹结构×6，T2 超额检测×6，T3 Prompt 注入+Guard×5）。全量 107 项测试全绿（0.84s）。 |
| Surgery #8 | 超范围拦截 + 未验证金额防御 + 未收录法条前向防御 | 端到端对抗测试暴露三类失守：① **P0-1**：境外法律与非法律问题未在路由层拦截，系统误入标准法律回答链路；② **P0-2**：用户抛出天文数字时，若计算所需事实尚未齐备，LLM 会围绕未验证金额展开策略推演；③ **P1**：用户主动引用不存在的法律/条文时，LLM 可能用训练记忆或相近法条“代答”。修复方案：① `router.py` 升级为四轨路由，新增确定性 `OUT_OF_SCOPE` 守卫，命中境外法律/非法律请求时直接返回固定拒答话术，不进入 RAG/Agent 流水线；② `advisor.py` 新增 `<amount_guard>` 与 `<amount_verification_notice priority="VERBATIM">`，当用户金额未被 `CalculationTrace` 或纠错通知验证时，强制转向“补充工资/工时等事实再计算”；③ 新增 `<statute_guard>` 与 `<statute_verification_notice priority="VERBATIM">`，用户引用的法律/条文若未被 RAG 检索到，必须明确说明知识库未收录并建议通过国家法律法规数据库等官方渠道核验，严禁凭训练记忆补答。新增 `tests/test_audit_e2e_defense.py` 14 项零 LLM 防御测试（超范围拦截×10、未验证金额防御×2、未收录法条防御×2）。**全量 159 项测试全绿（1.26s）**。 |

### 当前模块结构

```
app/
├── agent/
│   ├── graph.py              # LangGraph StateGraph 定义与路由条件
│   ├── tools.py              # RAG 工具（ChromaDB + BM25 + BGE Reranker）
│   ├── guards.py             # 后生成幻觉守卫（Surgery 6/7：数字异常检测、幻觉条文剥离）
│   ├── nodes/                # 节点函数独立模块（Surgery 4b 新增）
│   │   ├── _llm.py            # LLM 客户端工厂（共享单例）
│   │   ├── advisor.py         # legal_advisor_node + build_advisor_system_prompt()（纯函数）
│   │   ├── clarifier.py       # clarifier_node
│   │   ├── fact_updater.py    # fact_updater_node
│   │   ├── reflector.py       # reflector_node
│   │   ├── researcher.py      # vector_researcher_node
│   │   └── router.py          # tool_router_node（四轨路由 + OUT_OF_SCOPE 守卫）
│   └── prompts/
│       ├── advisor.py         # 法律 Agent 流水线 prompt 常量（8 个）
│       └── extractor.py       # 图谱抽取流水线 prompt 常量（5 个，已去重）
├── schemas/
│   └── case_state.py          # CaseState Pydantic 模型（Surgery 5 强化：ge=0 约束）
├── logic/
│   ├── calculators.py         # 确定性计算管线（Surgery 7：含 CalculationTrace / CorrectionNotice）
│   └── __init__.py
└── services/
    ├── legal_agent.py         # 薄壳编排器（Surgery 5 后：229 行，仅含 LegalGraphAgent 类）
    ├── graph_extractor.py     # Neo4j 图谱 CRUD（已去重 prompt）
    └── ...
```

### 数据流

```
用户输入
  → tool_router_node（四轨路由：GREETING / OUT_OF_SCOPE / GENERAL_CONSULT / CASE_UPDATE）
      ├─ GREETING → greeting_node
      ├─ OUT_OF_SCOPE → out_of_scope_node（固定拒答，不进入 RAG / Agent 流水线）
      ├─ GENERAL_CONSULT → vector_researcher_node（RAG 检索 + 图谱加载）→ legal_advisor_node
      └─ CASE_UPDATE → fact_updater_node（增量合并案卷摘要）→ vector_researcher_node → reflector_node
          ├─ SUFFICIENT → legal_advisor_node（V3 分层回答 + LLM 润色）
          ├─ NEED_USER_INFO → clarifier_node（三明治柔性追问）
          └─ NEED_DEEP_GRAPH → neo4j_specialist_node → reflector（循环）
```

### 测试基线

75 项测试覆盖：提取器精度（7）、Payload 构建（14）、回归守卫（3）、CaseState 模型（11）、新计算管线（18）、既有回归（15）、**Pydantic 防御硬化（7，Surgery 5 新增）**。全部为确定性断言，不依赖 LLM 调用。全量运行耗时 1.59s。

**Surgery 6 新增**：`tests/test_advisor_hallucination_defense.py` 15 项守卫测试（数字异常检测×7、幻觉条文检测×5、端到端集成×3），累计 **90 项全绿**。

**Surgery 7 新增**：`tests/test_calculation_trace.py` 17 项零 LLM 测试（轨迹结构×6、超额检测×6、Prompt 注入+Guard×5），累计 **107 项全绿（0.84s）**。所有测试均为纯确定性断言：零 LLM 调用、零 IO、零网络。

**Surgery 8（对抗防御修复）新增**：`tests/test_audit_e2e_defense.py` 14 项零 LLM 测试（超范围拦截×10、未验证金额防御×2、未收录法条前向防御×2），累计 **159 项全绿（1.26s）**。所有新增测试均为纯确定性断言：零 LLM、零 IO、零网络。

### 端到端测试套件（e2e_tests/）

首轮完整端到端测试于 2026-04-14 运行完成（总耗时 1006 秒，模型：Qwen1.5-1.8B-Chat-AWQ，知识库：法条 2177 + 案例 853 = 3030 篇）。

#### 基准测试结果（32 轮，100% 成功）

| 指标 | 值 |
|---|---|
| API 成功率 | 32/32 = **100%** |
| 标准问题案例命中率（8题） | 8/8 = **100%** |
| 关键词命中率 | 96.9% |
| 平均端到端延迟 | 22,296 ms |
| P95 延迟 | 32,681 ms |
| 最大延迟 | 70,633 ms |
| 平均置信度 | 0.43 |
| 5 套剧本通过率 | 27/28 轮（96.4%） |

#### 对抗测试结果（11 次攻击）

**PASS 3 / WARN 3 / FAIL 5，防御率 27.3%**

| 失守项 | 根因 |
|---|---|
| 超范围问题无拦截（美国劳动法、股市预测、编程任务） | 路由器关键词匹配无境外/非法律感知，模型尺寸（1.8B）不足以主动拒绝 |
| 幻觉法条穿透（《劳动者权益保护法》第88条、《劳动合同法》第999条） | Surgery #6 事后剥离守卫无法拦截前向幻觉（LLM 在生成阶段就用训练记忆填充） |
| 天文数字诱导（999999元）未纠正 | `_detect_overclaim` 上下文窗口外的数字不触发纠错，LLM 进入"高额赔偿分步策略" |

#### 测试文件结构

```
e2e_tests/
├── api_client.py           # LegalAgentClient：二阶段轮询、自动处理 needs_confirmation
├── dialogue_scenarios.py   # 5 套多轮剧本 + README 8 个标准问题（含 expect_keywords）
├── e2e_benchmark.py        # 基准测试：标准问题 + 剧本，输出 JSON + Markdown 报告
├── adversarial_dialogue.py # 对抗测试：数字幻觉/虚假法条/超范围/Prompt注入，PASS/WARN/FAIL 评分
├── run_all.py              # 一键主入口：健康检查 → 注册 → 基准 → 对抗 → 总报告
└── results/                # 测试产物（.gitignore 豁免，不入库）
    ├── benchmark_results.json
    ├── benchmark_report.md
    ├── adversarial_results.json
    └── final_report.md
```

运行方式（需先启动后端服务 + Redis 解锁）：
```bash
redis-cli CONFIG SET save ""
redis-cli CONFIG SET stop-writes-on-bgsave-error no
D:/Dev/python310/python.exe -X utf8 e2e_tests/run_all.py
```

---

## 架构重构记录：高内存崩溃与 Redis 连环故障排查

### 故障根因分析

本项目在 Windows 本地环境下运行时，曾出现以下连环崩溃：

```
内存溢出（OOM）
  → Redis bgsave 失败
  → MISCONF Redis is configured to save RDB snapshots, but is currently not able to persist on disk
  → Celery Worker ResponseError 连环崩溃
  → 整个异步任务队列瘫痪
```

**三个独立的根因叠加触发了这次崩溃：**

#### 根因 1：ML 模型在进程启动时同步加载（最大内存元凶）

`ChromaManager` 原先使用 `__new__` 单例 + `_init_db()` 的模式，在模块被 **import 时立即初始化** HuggingFaceEmbeddings（约 500 MB）。Celery Worker 启动时同样触发了这个 import，加上 `BGEReranker` 的 CrossEncoder（约 800 MB），导致每个 Worker 进程在处理任何任务之前就已持有约 1.3 GB 的模型权重。Windows 上 Celery 使用 `spawn` 方式创建进程，**每个并发 worker 进程都独立持有一份完整模型副本**，内存随并发数线性放大。

**修复方案**：将 `ChromaManager` 改造为延迟初始化（lazy init）。`__new__` 只注册实例，实际的模型加载推迟到首次 **属性访问** 时，并通过双重检查加锁（Double-Checked Locking）保证线程安全。Celery Worker 现在可以完成启动、注册任务并连接 broker，而不需要预先占用任何 ML 模型内存。

#### 根因 2：`celery_app.py` 中多次独立调用 `asyncio.run()`

原先每个 Celery 任务内调用了 4 次 `asyncio.run()`（分别用于加载对话历史、加载摘要、加载图谱内存、调用 agent），每次调用都会**创建并销毁一个完整的事件循环**。高并发时这造成了大量线程池竞争，也是内存额外开销的来源之一。

**修复方案**：将多个独立的 IO 操作合并到 `_run_task_async()` 单一协程中，只调用一次 `asyncio.run()`，全程共享同一事件循环。

#### 根因 3：Redis 未配置 `stop-writes-on-bgsave-error no`

Redis 默认在 bgsave 失败（通常是内存不足）后会**拒绝所有写操作**（MISCONF 错误），这是一种过于激进的自我保护机制。Celery 需要向 Redis 写入任务状态，一旦被拒绝，整个任务队列就会崩溃。

**修复方案（两层防护）**：
1. **运行时配置**：执行 `redis-cli CONFIG SET save “”` 和 `redis-cli CONFIG SET stop-writes-on-bgsave-error no`，解除 bgsave 触发条件和写入锁定。
2. **代码层降级**：重写 `AsyncRedisSaver`（`app/memory/redis_mgr.py`），在 `aget_tuple` 和 `aput` 方法中用 `try/except` 捕获所有 Redis 异常，**静默降级为无状态模式**，而非将异常向上传播导致任务失败。

### 重构决策汇总

| 模块 | 变更内容 | 目的 |
|------|----------|------|
| `app/database/chroma_mgr.py` | `__new__` 单例改为延迟初始化，属性用 `_ensure_initialized()` 惰性触发 | 消除 Worker 启动时的模型预加载内存峰值 |
| `app/celery_app.py` | 合并多次 `asyncio.run()` 为单一协程；新增 `worker_concurrency=2`、`task_soft_time_limit` 等保护参数 | 降低内存放大倍数，防止无限并发吃光内存 |
| `app/memory/redis_mgr.py` | 所有 Redis 操作包裹 `try/except`，失败时 WARNING 降级而非抛出异常 | 解除 Redis MISCONF 对 Celery 任务链的致命阻塞 |
| `start.md` | 新增 Redis 防护配置步骤、Celery 推荐启动命令和内存占用参考表 | 提供可操作的运维 SOP |

### 给未来开发者的避坑指南

1. **永远不要在模块顶层实例化 ML 模型**。将实际加载推迟到首次调用，否则每个 `import` 该模块的进程（包括 Celery Worker、测试进程）都会静默地吃掉几百 MB 内存。

2. **在 Windows/spawn 模式下，Celery 并发数 ≠ 线程数，而是进程内存的放大器**。并发 4 个 worker、每个持有 1.3 GB 模型 = 5.2 GB，在 8 GB 机器上必然 OOM。使用 `--concurrency 2` 启动，或考虑将模型移出 Worker 进程，改用专用推理服务（vLLM、TorchServe）提供 API。

3. **Redis 的 `stop-writes-on-bgsave-error yes`（默认值）是生产级数据库的保护措施，不适合本地开发环境**。本地开发时请始终执行 `CONFIG SET save “”` 和 `CONFIG SET stop-writes-on-bgsave-error no`。

4. **不要在 Celery 同步任务函数中多次调用 `asyncio.run()`**。每次调用都新建事件循环，非常低效。将所有异步 IO 聚合到一个协程，只在任务入口处调用一次 `asyncio.run()`。

5. **任何使用 Redis 作为 checkpoint/状态存储的中间层都应该做降级处理**。Redis 是易受系统内存影响的组件，业务逻辑不应直接依赖其写入成功。

## 核心架构

1. 认知解耦架构：通过分层输出协议，将“基础权益清算”“高风险执行策略”“下一步推进动作”拆成独立层次，解决了高风险执行诉求对基础权益清算的逻辑阻塞。
2. 情感化 Prompt 交互：引入“先予后取”的三明治话术模型，使 Agent 在信息不足时先肯定证据价值、再给阶段性判断、最后柔性追问，显著提升了长程对话中的用户留存与信任感。
3. 确定性增强推理：结合系统时间锚点注入与相对时间解析规则，实现了无需额外重型 Python 推理链路介入的自然语言日期常识推导。

## 架构避坑指南（2026-04 核心修复）

1. Semantic GC 的节点与关系清理必须使用精确匹配 `=`，严禁使用 `CONTAINS` 这类模糊匹配，否则会在删除旧节点时引发级联误杀。
2. 严禁在 `graph_extractor` 阶段做全量索引加载或高并发 Embedding。Windows 环境下这会放大 Tokenizer 死锁风险，并触发 OOM 连续内存申请失败。
3. 图谱相关 Prompt 若包含 JSON 示例，所有示例大括号都必须写成 `{{` 与 `}}`，防止 Python `.format()` 在运行期抛出 `KeyError`。
4. 代码中严禁混入中文弯引号 `“”`、`‘’`。这类字符一旦进入 Python 源码或字符串边界，极易在启动期触发 `SyntaxError`。
