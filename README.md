# Legal Agent V3 — 中文法律问答 RAG + Agent 系统

基于 LangGraph 多智能体架构的中文法律问答系统。采用 RAG（检索增强生成）+ 确定性计算管线 + 多模型协作的混合架构，支持多轮案情分析、法条检索、赔偿计算和知识图谱推理。

## 架构亮点

| 特性 | 实现 |
|------|------|
| 多模型协作 | 本地 Qwen2.5-3B（路由分类）+ 云端 GLM-4（法律推理），云边协同架构 |
| 类型安全 | Pydantic V2 CaseState，`ge=0` 边界约束，JSON 快照持久化 |
| 确定性计算 | `build_claim_payload()` 纯函数管线，`round(x,2)` 精度保证 |
| 幻觉防御 | 三层：Prompt 约束 → number_lock/citation_lock → 代码层 sanitize |
| RAG 检索 | ChromaDB dense + BM25 sparse 双路召回，BGE Reranker v2-m3 重排 |
| 知识图谱 | Neo4j 实时三元组提取，会话级子图，全局图谱查询 |
| 路由安全 | 四轨路由（GREETING/GENERAL_CONSULT/CASE_UPDATE/OUT_OF_SCOPE）+ 本地模型 guardrail |
| 用户数据 | MySQL 会话历史 + 案件快照（CaseState JSON）+ 案件类型自动推断 + 检索 API |
| 用户个性化图谱 | Neo4j user_id 隔离 + 跨案件共享实体发现 + 历史案件上下文注入 |
| 可解释性 | CalculationTrace 白盒契约，VERBATIM 超额纠错注入 |
| 架构瘦身 | 主编排器 1903→239 行（-87%），关注点分离到 7 个节点 |
| 测试覆盖 | 295 个确定性断言，零 LLM/IO/网络依赖 |
| Grounded RAG 推理 | LLM 从"精校员"升级为"基于 RAG 的法律推理器"，advisor 三段式 prompt（事实/法条/案例），rerank 改为多 query 独立 RRF 融合 |

## 技术栈

- **后端框架**: FastAPI + Celery + Redis
- **LLM**: 本地 Qwen2.5-3B-AWQ (vLLM) + 云端 GLM-4-Flash (智谱 API)
- **向量检索**: ChromaDB (dense) + BM25 (sparse) + BGE Reranker v2-m3
- **知识图谱**: Neo4j
- **数据库**: MySQL 8.0 (用户/会话/消息/案件快照)
- **Agent 框架**: LangGraph StateGraph
- **类型系统**: Pydantic V2
- **前端**: React (端口 3000)

## 系统架构

```mermaid
graph TB
    User[用户] --> Frontend[React 前端<br/>:3000]
    Frontend --> API[FastAPI<br/>:8000]
    API --> Celery[Celery Worker]

    Celery --> Router[路由节点<br/>本地 Qwen 3B]
    Router -->|GREETING| Greeting[寒暄响应]
    Router -->|OUT_OF_SCOPE| OOS[超范围拦截]
    Router -->|GENERAL_CONSULT| Researcher
    Router -->|CASE_UPDATE| FactUpdater[案情更新<br/>云端 GLM-4]

    FactUpdater --> Researcher[RAG 检索]
    Researcher --> ChromaDB[(ChromaDB<br/>2177法条+853案例)]
    Researcher --> BM25[BM25 Index]
    Researcher --> Reranker[BGE Reranker]
    Researcher --> Neo4j[(Neo4j<br/>知识图谱)]

    Researcher -->|CASE_UPDATE| Reflector[反思节点]
    Researcher -->|GENERAL_CONSULT| Advisor
    Reflector --> Advisor[法律顾问<br/>云端 GLM-4]

    Advisor --> Guards[三层守卫<br/>number_lock<br/>citation_lock<br/>sanitize]
    Guards --> Result[最终响应]

    Celery --> MySQL[(MySQL<br/>会话+案件快照)]
    Celery --> Redis[(Redis<br/>Checkpoint)]
```

## 项目结构

```text
app/                          # 主应用 (6769行, 45个Python文件)
├── core/
│   ├── llm_factory.py        # Surgery #12: 本地/云端双轨 LLM 工厂
│   └── ...
├── agent/
│   ├── graph.py              # LangGraph StateGraph (124行)
│   ├── tools.py              # RAG 混合检索 (372行)
│   ├── guards.py             # 三层守卫 (577行)
│   ├── nodes/
│   │   ├── _llm.py           # 双模型工厂 + 工具执行
│   │   ├── advisor.py        # 法律顾问 + Surgery #11 confidence (456行)
│   │   ├── router.py         # 四轨路由 + guardrail (466行)
│   │   ├── fact_updater.py   # 案情更新
│   │   ├── reflector.py      # 反思节点
│   │   ├── researcher.py     # 向量检索 + 图谱加载 + 用户历史案件实体
│   │   └── clarifier.py      # 澄清节点
│   └── prompts/              # Prompt 统一管理
├── schemas/
│   └── case_state.py         # CaseState Pydantic 模型 (224行)
├── logic/
│   └── calculators.py        # 确定性计算管线 (444行)
├── services/
│   ├── legal_agent.py        # 薄壳编排器 (239行, 从1903行瘦身87%)
│   ├── conversation_store.py # 会话+案件快照持久化 (288行+)
│   └── graph_extractor.py    # Surgery #14: user_id 写入 + 用户图谱查询
├── database/
│   ├── mysql_db.py           # 异步 SQLAlchemy 引擎
│   ├── chroma_mgr.py         # ChromaDB 管理
│   └── neo4j_mgr.py          # Neo4j 连接管理
├── models/
│   └── entities.py           # ORM: User/Conversation/Message
├── api/                      # FastAPI 路由
│   ├── dialog.py             # 对话接口
│   ├── conversations.py      # 会话CRUD + 案件搜索
│   ├── graph.py              # Surgery #14: 用户图谱 API
│   └── auth.py               # JWT认证
└── celery_app.py             # 任务调度 (326行)

scripts/
├── warmup.py                 # 服务预热脚本
└── backfill_graph_user_id.py # Surgery #14: 图谱 user_id 回填

docs/
├── latency_analysis.md       # 延迟分析 + Mermaid 时序图
└── confidence_score_diagnosis.md

e2e_tests/                    # 端到端测试套件
tests/                        # 单元测试 (295 passed)
```

## 快速启动

### 环境要求
- Python 3.10+
- MySQL 8.0
- Redis
- Neo4j
- (可选) vLLM + CUDA GPU

### 启动步骤
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量 (.env)
cp .env.example .env  # 编辑数据库连接、API Key 等

# 3. 启动后端
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. 启动 Celery Worker
celery -A app.celery_app worker --loglevel=info

# 5. (可选) 启动本地 vLLM
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-3B-Instruct-AWQ \
  --port 8001 --host 0.0.0.0

# 6. 预热服务
python scripts/warmup.py
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/login` | JWT 认证登录 |
| POST | `/dialog` | 提交对话请求（异步） |
| GET | `/dialog/status/{task_id}` | 轮询任务结果 |
| POST | `/conversations` | 创建新会话 |
| GET | `/conversations` | 列出用户会话 |
| GET | `/conversations/search` | 按类型/关键词搜索案件 |
| GET | `/conversations/{id}/messages` | 获取会话消息历史 |
| GET | `/conversations/{id}/graph` | 获取会话知识图谱 |
| GET | `/graph/subgraph?query=` | 全局图谱查询 |
| GET | `/graph/user/{user_id}/cases` | 用户案件图谱概览 |
| GET | `/graph/user/{user_id}/cross-case-entities` | 跨案件共享实体发现 |

## 测试

```bash
D:/Dev/python310/python.exe -m pytest tests/ -v --tb=long
```

**当前基线**: 295 passed, 0 failed

所有测试为确定性断言，零 LLM、零 IO、零网络依赖。

| 测试维度 | 覆盖 |
|----------|------|
| CaseState Pydantic 模型 | 边界值 + None 防御 + JSON 序列化 |
| 确定性计算管线 | 金额精度 + 浮点安全 |
| 幻觉守卫 | 案号豁免 + number_lock + citation_lock |
| 代码层守卫 | unverified_claims + hallucinated_statutes |
| 路由 | 四轨分类 + guardrail + OOS 拦截 |
| Confidence | 启发式四档 + reflector 不覆盖 |
| 案件类型推断 | 7类 + 空输入 |
| 案件快照 | JSON roundtrip + 序列化 |
| 用户图谱隔离 | user_id 透传 + API 路由 + 查询常量 |

## 开发历程

| Surgery | 名称 | 性质 |
|---------|------|------|
| #1 | CaseState Pydantic 强类型模型 | 架构 |
| #2 | 确定性计算管线 | 架构 |
| #3 | Prompt 统一管理 | 架构 |
| #4a/4b | legal_agent 瘦身 + 节点拆分 (1903→239行) | 架构 |
| #5 | Pydantic 全面接管 | 质量 |
| #6 | LLM 幻觉三层防护 | 安全 |
| #7 | CalculationTrace 白盒契约 | 可解释性 |
| #8 | OUT_OF_SCOPE 路由拦截 | 安全 |
| #9 | 代码层强制守卫 | 安全 |
| #10 | Bug 修复 (浮点/案号/空值/常量) | 质量 |
| #11 | Confidence 启发式补丁 | 功能完善 |
| #12/12A | 本地 Qwen 3B 路由 + guardrail | 多模型协作 |
| #13 | 案件快照持久化 + 类型推断 + 搜索 API | 用户数据 |
| #14 | Neo4j 用户个性化图谱 | 数据 |
| #Q1 | 双倍工资 cap_rule 显式化 | 回答质量 |
| #Q2 | 场景化行动清单生成器 | 回答质量 |
| #Q3/Q4 | RAG 法条引用激励 + 程序性指导解禁 | 回答质量 |
| #G-1 | quality_benchmark UTF-8 编码修复 | 工程修复 |
| #G-2 | 虚假法条识别器 + sanitize 放宽 | 安全/质量 |
| #G-3 | 查询扩展 JSON 容错 + phantom 过滤 | 检索质量 |
| #G-4 | Advisor 三段式 grounded prompt + 路由扩词 + 劳动模板 | 推理质量（核心） |
| #G-5 | 法条引用图谱 Lite 版（数据资产） | 数据基建 |
| #G-6 | Law Recall Booster | 检索质量 |
| #G-7 | Per-Query Rerank + RRF 融合 | 检索算法 |
| #G-8 | Query Expansion 法律术语化 | 检索质量 |
| #G-9 | LAW_TOP_K 扩窗（3→6） | 推理质量 |

### Surgery #12/12A: 本地 vLLM 多模型协作
- 本地 Qwen2.5-3B-Instruct-AWQ 接管 `router_llm`（意图路由）
- 云端智谱 GLM-4 继续负责 `expert_llm`（法律推理）和 `summary_llm`（案情合并）
- 新增 `app/core/llm_factory.py` 统一模型工厂，支持本地/云端双轨
- 查询扩展 LLM 支持本地优先 + 云端 fallback
- 查询扩展双轨调用已改为短路模式：本地扩展在 5 秒内返回有效 JSON 时直接采用，只有超时/异常/无效结果才回落云端
- 新增 `Query expansion path: local / cloud_fallback` 日志打点，便于定位检索链路延迟来源
- Router guardrail：本地小模型路由结果校验

### Surgery #13: MySQL 用户数据层补全
- `conversations` 表新增 `case_state_json`（CaseState Pydantic 序列化）和 `case_type`（自动推断）
- 持久化结构化案件快照，支持跨会话恢复
- 新增案件搜索 API: `GET /conversations/search?case_type=&keyword=`
- 多轮场景下 `saved_case_state` fallback 已接入 advisor 节点

### Surgery #14: Neo4j 用户个性化图谱
- 所有 `DynamicCaseEntity` 节点和 `CASE_RELATION` 关系写入时携带 `user_id`
- 新增用户案件图谱概览 API: `GET /graph/user/{user_id}/cases`
- 新增跨案件共享实体发现 API: `GET /graph/user/{user_id}/cross-case-entities`
- `researcher` 节点增强：自动加载用户历史案件实体到图谱上下文
- 回填脚本: `scripts/backfill_graph_user_id.py`
- 静态知识图谱（Law/Case/Party）保持全局共享不变

### Surgery Q-1 ~ Q-4: 回答质量增强
- Q-1：在 `build_claim_payload()` 的 answer 文本中显式注入二倍工资计算规则，补充“首月不计入 / 11 个月上限 / 次月起算”。
- Q-2：新增 `_build_action_checklist()`，按劳动争议、借贷、离婚、刑事等场景生成证据清单、流程步骤、时效与风险提示。
- Q-3：advisor prompt 明确鼓励在 `<rag_context>` 存在条文编号时主动引用真实法条，提高法条真实性与专业感。
- Q-4：advisor prompt 解禁程序性指导，允许保留并润色证据清单、流程说明、费用/管辖/时效等操作性内容。

### Surgery G 系列：从模板拼装到 Grounded RAG 推理

**背景**：Q 系列结束后跑出 quality_benchmark 64%，分析定位到核心病灶不是"benchmark 没刷满"，
而是"系统稳定但答案是模板拼装而非法律推理"。LLM 被 prompt 锁定为"文本精校员，禁止改数字、
禁止补法条编号"，导致再好的 RAG 召回也救不了答案。Surgery G 系列围绕"让 LLM 真正基于 RAG
做法律推理"展开，按防御 → 检索 → 推理 → 召回的顺序逐层抬升。

#### G-1：基准脚本编码修复（commit 2c243f8）
- 修复 `quality_benchmark.py` UTF-8 stdout + 进度条字符兼容性问题
- 让 `quality_benchmark_report.json` 不再因为编码 bug 落盘成空报告

#### G-2：守卫层放宽与虚假法条识别（commit 2dec04f）
- 新增 `detect_fabricated_statute()`：识别 "《劳动合同法》第三百二十五条" 这类伪造条款，
  基于硬编码白名单上限（劳动合同法 98 条 / 民法典 1260 条等）
- `sanitize_llm_answer` 从"硬回退到 payload" 放宽为 "warning-only"，
  保留 LLM 基于 RAG 的真实推理输出，不再因金额 warning 整段回滚
- 中文数字条号识别：`第八十七条` 与 `第87条` 都能匹配
- 配套测试：`tests/test_guards_fabricated.py`

#### G-3：查询扩展 JSON 容错与 phantom 法条过滤（commit fdc23a8）
- 本地 vLLM 查询扩展 prompt 持续返回非 JSON payload 导致回落云端，新增：
  - `_extract_query_array_payload()`：剥离 ```json``` 代码块
  - `_coerce_expansion_queries()`：兼容 string / dict 两种 query 项
  - `_parse_expansion_queries()`：基于 `repair_json` 的容错解析
- 新增 `_sanitize_expanded_queries()`：过滤扩展 query 中伪造的"第N条"，
  避免下游 retrieval 被噪声 query 拖偏

#### G-4：Advisor 三段式 grounded prompt + 路由扩词 + 劳动模板（commit c4eda13）
- **核心改造**：build_advisor_system_prompt 从"精校员"升级为"基于材料的法律推理器"
- `_split_rag_by_type()`：将 retrieval 结果按 metadata 拆成
  `laws`（含 `metadata.article` / `law_title`）/ `cases`（含 `case_name`）/ `others` 三段
- 新 prompt 三段结构：【已确认事实】/【相关法条原文】/【参考案例】，LLM 必须基于这三段作答
- `_extract_payload_facts()`：只把 payload 中的金额结论行和权威法条引用塞给 LLM，
  不再传整段 payload.answer 作为骨架，避免 LLM 又被"精校员"角色绑住
- 删除"骨架 + 只改措辞" 的 user message 锁，改为"基于 system 中三段材料作答"
- Router 关键词扩词：LAW / CASE / GRAPH_INTENT_KEYWORDS 扩展，
  GENERAL_CONSULT 路径强制注入 LAW + CASE retrieval
- 新增劳动权益清单模板（双倍工资 / 违法辞退 / 经济补偿 / 社保 / 病假 / 仲裁时效）
- 工伤十级伤残模板补全（一次性伤残补助金 / 医疗补助 / 就业补助 / 停工留薪 / 医疗费）

#### G-5：法条引用图谱 Lite 版（commit 64f4f2f / 2ce993d / c5b29be）
- `scripts/build_article_reference_graph.py`：扫描法条 corpus，抽取条款间引用关系
  （如劳动合同法第八十七条 → 第四十七条），写入 Neo4j 形成 `LawArticle - REFERENCES - LawArticle` 边
- 当前规模：LawArticle 节点 2124、REFERENCES 边 223
- 运行时图扩展（C2）经验证后回退：根因是 rerank 偏移（目标条款已在 base candidates 但被压出 top10），
  不是图扩展问题。图谱本身保留作为数据资产
- `docs/article_reference_graph.md` 文档化 ingest 流程

#### G-6：Law Recall Booster（commit d740e8d）
- 新增 `app/agent/recall_booster.py`：在召回阶段基于法律要素（如"违法辞退""未签合同"）
  补召回对应法条候选，确保关键条款进入 base candidates 池
- `LABOR_ELEMENT_TO_ARTICLES` 是法律要素 → 条款映射，而非 case_type → 条款硬编码，
  保持泛化能力

#### G-7：Per-Query Rerank with RRF Fusion（commit 0e9776b）
- **诊断**：rerank query 之前是 `" ".join(expanded_queries)`，把 5 条 expansion 拼成长 query
  喂给 cross-encoder，导致语义稀释，每个子意图都打不准
- **改造**：每条 expanded query 独立 rerank，再用 Reciprocal Rank Fusion 融合
  - RRF 公式：`score(d) = Σ 1 / (rrf_k + rank_q(d))`，rrf_k=60 是 TREC 标准
  - 保留每个子意图的 top hits，避免长 query 注意力分散
- 不引入任何业务先验（如 case_type → 关键条款加权），保持纯算法层泛化
- 配套测试：`tests/test_rrf_rerank.py` 覆盖空输入、单 query 顺序、双 query 共识、空 query 跳过

#### G-8：Query Expansion 法律术语化（commit 12d651e）
- **诊断**：用户用"违法辞退""没签合同"等口语描述纠纷，但法条原文使用法言法语
  （"用人单位违反本法规定解除劳动合同"），cross-encoder 在术语精度上敏感，俗语 query
  召不准法言法语 chunk
- **改造**：QUERY_EXPANSION_SYSTEM_PROMPT 新增"法律术语化"核心要求：
  - 至少 1-2 条扩展查询必须把口语化纠纷描述重写成法律术语
  - 配合 6 条俗语→术语映射示例（违法辞退 / 没签合同 / 没缴社保 / 拖欠工资 / 高利贷 / 醉驾）
  - 配合 3 个完整输入输出示范（劳动 / 民事 / 刑事 各 1 个）
- 弥合"用户口语 query" 与"法言法语 corpus" 的术语鸿沟，纯 prompt 工程，泛化所有法律领域

#### G-9：LAW_TOP_K 扩窗（commit b4f5939）
- **诊断**：grounded RAG 范式下 LAW_TOP_K=3 太窄。RRF 排完只取前 3，
  核心条款（如劳动合同法第八十七条）虽在 RAG 上下文里但被挤出 laws_block 窗口，
  LLM 没有原文 → 不敢引用 → guard 又把模型靠记忆补的"第八十二条"剥离
- **改造**：LAW_TOP_K 3 → 6；advisor `_split_rag_by_type` 中 laws 截断同步放宽
- 6 条覆盖"实体权利 + 程序时效 + 计算标准"完整法条骨架，符合律师真实查阅习惯
- 350 字截断 + cases 仍保持 3 条不变，控制 prompt 总长度

#### Surgery G 系列实战验证

基于 `scripts/probe_advisor.py`（未提交，仅本地调试用）的端到端用例验证：

| 用例 | 关键期望 | G 系列前 | G 系列后 |
|---|---|---|---|
| 1：拖欠工资法条引用 | 《劳动合同法》第三十条 | 部分命中 | ✅ 第三十条 + 第八十五条 |
| 2：劳动权益清单 | 第八十二条 / 第四十七条 / 第八十七条 | ❌ 三条均未命中 | ✅ 三条全部命中 |
| 3：试用期违法辞退案例 | cases_block 非空 + 案件名引用 | 部分命中 | ✅ 完整命中 |

#### Surgery G 系列设计原则总结

- **拒绝业务硬编码**：曾设计 case_type → 关键条款加权方案（CASE_TYPE_TO_PRIORITY_ARTICLES），
  评审时识别为"把 benchmark 答案抄进代码"而废弃，改为纯算法层（RRF）+ prompt 工程（术语化）
- **每个改造都有理论锚点**：
  - RRF：TREC 标准融合算法
  - 三段式 grounded prompt：grounded generation 范式
  - 法律术语化：弥合 query-corpus 术语鸿沟（IR 经典问题）
  - LAW_TOP_K 扩窗：grounded RAG 需要更广上下文支持综合推理
- **承上启下**：保留 C1 法条引用图谱作为数据资产，C2 运行时图扩展待 query-aware rerank 改造完成后再考虑重新集成

## 端到端基准测试

> **注**：下方 quality_benchmark 数据为 Surgery G 系列改造前的基线（55/80）。
> G 系列改造完成后已验证三个核心用例命中显著提升（详见 Surgery G 系列实战验证表），
> 但全量 benchmark 回归尚未跑（成本 14 分钟），将在最终答辩前完整跑一次更新此处。

| 指标 | 值 |
|------|-----|
| Fix 2 多轮月薪连贯专项 | 100% (8/8) |
| 全面质量基准总分 | 55/80 (68%) |
| D1 金额计算 | 46% |
| D2 法条真实性 | 60% |
| D3 多轮案情连贯性 | 90% |
| D4 回答完整性 | 75% |
| D5 回答可操作性 | 72% |
| D6 幻觉抑制 | 72% |
| D7 案例引用质量 | 80% |
| D8 系统健康 | 60% |
| 单元测试 | 295 passed, 0 failed |

> 详细数据见 `e2e_tests/results/e2e_fix_validation.json` 与 `e2e_tests/results/quality_benchmark_report.json`

## 研究与工程成果

- 从单体式 `legal_agent.py` 演进到 LangGraph 节点化编排，完成职责解耦与代码瘦身。
- 将“计算”与“生成”彻底分层：金额、月数、赔偿总额均由纯 Python 管线白盒产出。
- 建立 Prompt 守卫 + 后处理守卫 + 结构化快照持久化三层质量闭环。
- 完成 LLM 角色从"模板精校员"到"grounded RAG 法律推理器"的范式跃迁，
  通过 advisor 三段式 prompt（事实/法条/案例）让 LLM 基于检索材料做实质推理。
- 在检索层引入 RRF 多查询融合 + Query 法律术语化 + LAW_TOP_K 扩窗，
  通过纯算法和 prompt 工程手段提升核心条款命中率，拒绝业务硬编码以保持系统泛化能力。
- 引入本地 3B 路由模型承担低成本分类任务，云端模型集中用于高质量法律推理。
- 将用户会话从纯文本历史升级为“消息 + 摘要 + CaseState JSON 快照 + 案件类型标签”的持久化数据层。
- 建立 Neo4j 用户级动态图谱隔离，并支持跨案件共享实体发现与历史案件实体上下文注入。

## 演示建议

- 优先展示四类输入：寒暄、普通法律咨询、案件更新、超范围问题。
- 劳动争议样例最适合展示完整链路：事实抽取 → CaseState → 赔偿计算 → 案件快照。
- 可以配合 `/conversations/search?case_type=劳动争议` 演示用户历史案件检索。
- 若需要答辩展示，可同时打开 Neo4j Browser、MySQL 会话表和接口返回结果。

## 已知运行注意事项

- 本地开发建议先启动 Redis、MySQL、Neo4j，再启动 FastAPI 与 Celery Worker。
- Windows 环境下 Celery/模型加载容易受编码与显存环境影响，建议先执行预热脚本。
- 端到端验证脚本位于 `e2e_tests/`，适合在服务全部启动后单独执行。
- 图谱相关调试可直接访问 `/graph/user/{user_id}/cases` 与 `/graph/user/{user_id}/cross-case-entities`。

## 许可证与说明

- 本项目面向中文法律智能问答研究与工程实践展示。
- README 中的测试基线与质量指标已同步至 Surgery Q1~Q4 完成状态。
