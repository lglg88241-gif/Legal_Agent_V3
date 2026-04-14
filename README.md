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

### 当前模块结构

```
app/
├── agent/
│   ├── graph.py              # LangGraph StateGraph 定义与路由条件
│   ├── tools.py              # RAG 工具（ChromaDB + BM25 + BGE Reranker）
│   └── prompts/
│       ├── advisor.py         # 法律 Agent 流水线 prompt 常量（8 个）
│       └── extractor.py       # 图谱抽取流水线 prompt 常量（5 个，已去重）
├── schemas/
│   └── case_state.py          # CaseState Pydantic 模型
├── logic/
│   ├── calculators.py         # 确定性计算管线 + PayloadResult 模型
│   └── __init__.py
└── services/
    ├── legal_agent.py         # V3 生产逻辑（瘦身后，待进一步领域拆分）
    ├── graph_extractor.py     # Neo4j 图谱 CRUD（已去重 prompt）
    └── ...
```

### 数据流

```
用户输入
  → tool_router_node（三轨路由：GREETING / GENERAL_CONSULT / CASE_UPDATE）
  → fact_updater_node（增量合并案卷摘要）
  → vector_researcher_node（RAG 检索 + 图谱加载）
  → reflector_node → CaseState.from_text() + build_claim_payload()
      ├─ SUFFICIENT → legal_advisor_node（V3 分层回答 + LLM 润色）
      ├─ NEED_USER_INFO → clarifier_node（三明治柔性追问）
      └─ NEED_DEEP_GRAPH → neo4j_specialist_node → reflector（循环）
```

### 测试基线

68 项测试覆盖：提取器精度（7）、Payload 构建（14）、回归守卫（3）、CaseState 模型（11）、新计算管线（18）、既有回归（15）。全部为确定性断言，不依赖 LLM 调用。

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
