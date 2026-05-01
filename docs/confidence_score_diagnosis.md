# confidence_score 异常诊断

## 计算逻辑位置

- 初始默认值：
  [app/services/legal_agent.py](C:/Users/123/Legal_Agent_V3/app/services/legal_agent.py:188)
  [app/services/legal_agent.py](C:/Users/123/Legal_Agent_V3/app/services/legal_agent.py:202)

- 最终返回值：
  [app/services/legal_agent.py](C:/Users/123/Legal_Agent_V3/app/services/legal_agent.py:228)

- 唯一实际赋值节点：
  [app/agent/nodes/reflector.py](C:/Users/123/Legal_Agent_V3/app/agent/nodes/reflector.py:47)
  [app/agent/nodes/reflector.py](C:/Users/123/Legal_Agent_V3/app/agent/nodes/reflector.py:58)
  [app/agent/nodes/reflector.py](C:/Users/123/Legal_Agent_V3/app/agent/nodes/reflector.py:67)

- GENERAL_CONSULT 是否经过 reflector 的路由判定：
  [app/agent/graph.py](C:/Users/123/Legal_Agent_V3/app/agent/graph.py:53)

## 计算公式

当前没有动态公式，只有 reflector 返回的两档固定值：

- `NEED_USER_INFO` → `confidence_score = 0.62`
- `SUFFICIENT` → `confidence_score = 0.84`

也就是说，`confidence_score` 目前本质上不是连续评分，而是 reflector 节点的固定标签值。

## 0.00 根因分析

### 根因 1：GENERAL_CONSULT 路径根本不经过 reflector

在 [app/agent/graph.py](C:/Users/123/Legal_Agent_V3/app/agent/graph.py:53)：

```python
def route_after_vector_researcher(state: AgentState) -> str:
    route_intent = str(state.get("route_intent", "GENERAL_CONSULT")).upper()
    if route_intent == "GENERAL_CONSULT":
        return "legal_advisor"
    return "reflector"
```

因此：
- `GENERAL_CONSULT`：`vector_researcher -> legal_advisor`
- `CASE_UPDATE`：`vector_researcher -> reflector -> legal_advisor/clarifier`

只有 `CASE_UPDATE` 才会经过 reflector，只有经过 reflector 才会写入 `confidence_score`。

### 根因 2：初始状态把 confidence_score 设成了 0.0

在 [app/services/legal_agent.py](C:/Users/123/Legal_Agent_V3/app/services/legal_agent.py:202)：

```python
"confidence_score": 0.0,
```

如果后续节点不覆盖这个字段，最终返回就会保留 `0.0`。

### 根因 3：GREETING / OUT_OF_SCOPE 也不会覆盖 confidence_score

`greeting_node` 和 `out_of_scope_node` 只返回 `response/sources/is_final`，没有写 `confidence_score`。
所以这些路由天然也都是 0.00。

## 数据证据

数据来源：[final_benchmark_data.json](C:/Users/123/Legal_Agent_V3/e2e_tests/results/final_benchmark_data.json)

统计结果：
- 对话类请求总数：34
- `confidence_score == 0.00`：22
- `confidence_score > 0`：12

路由分布：
- `0.00` 的请求全部来自：
  - `GENERAL_CONSULT`
  - `GREETING`
  - `OUT_OF_SCOPE`
- `> 0` 的请求全部来自：
  - `CASE_UPDATE`

也就是说，**当前的 confidence_score 不是“回答质量评分”，而是“是否经过 reflector 节点”的副产物。**

## 诊断结论

标准问题为什么全是 `0.00`：
- README 8 个标准问题全部是单轮 `GENERAL_CONSULT`
- 这条路径不会进入 reflector
- 所以不会写入 `confidence_score`
- 最终返回沿用初始默认值 `0.0`

多轮剧本里为什么会出现 `0.62 / 0.84`：
- 案情补充轮次进入 `CASE_UPDATE`
- `reflector_node` 被执行
- reflector 固定写入 `0.62` 或 `0.84`

## 修复建议

### 方案 1：让 GENERAL_CONSULT 也经过 reflector
优点：
- 语义最一致，所有正式回答都能产出 confidence
- 可复用现有 reflector 的评分接口

代价：
- 增加一次节点跳转
- 如果 reflector 未来恢复 LLM 化，会进一步增加延迟

### 方案 2：在 legal_advisor_node 中补一个轻量 confidence
建议做法：
- 若 `confidence_score` 仍为 0.0，则按启发式赋值，例如：
  - `sources_count == 0` → 0.35
  - `sources_count in [1,2]` → 0.55
  - `sources_count >= 3` → 0.72
- 这样不改路由结构，也不会增加延迟

### 方案 3：把 confidence 分成两类
- `route_confidence`：路由/案情完整度（reflector 产出）
- `answer_confidence`：回答证据充分度（advisor 按 sources / payload 产出）

这是最完整的设计，但超出当前毕业设计的必要范围。

## 建议结论

如果目标是**最小改动修复论文展示效果**，优先采用方案 2：
- 不改变 LangGraph 路由
- 不增加新 LLM 调用
- 只在 `legal_advisor_node` 输出前补一个基于 `sources_count` 和 `route_intent` 的启发式 confidence

如果目标是**语义最干净的架构一致性**，采用方案 1，让所有正式回答统一经过 reflector。
