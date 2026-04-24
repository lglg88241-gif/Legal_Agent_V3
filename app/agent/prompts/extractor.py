"""
Prompt constants for the graph extractor pipeline (graph_extractor.py).

This module is the single source of truth for all graph extraction
prompts. It eliminates the triple-definition problem in the original
graph_extractor.py where each prompt was defined 3 times at different
positions in the file, with the last definition silently shadowing
the earlier ones.

The canonical version here is the final (most evolved) version from
each prompt — i.e., the text that was actually in effect at runtime.

WARNING: Do NOT edit prompt text during the refactoring phase.
         These strings must be byte-identical to the originals.
"""

# ======================================================================
# Initial extraction (full text -> triples)
# ======================================================================

GRAPH_EXTRACTION_PROMPT = """
你是一个法律案件动态图谱抽取器。
请从输入文本中提取已经明确出现的事实三元组，并做实体对齐。

规则：
1. 只提取已经明确表达的事实，禁止臆测。
2. 同一事物必须统一命名，例如\u201c我/本人/用户\u201d统一为\u201c当事人\u201d。
3. 每个实体都必须给出 category，且只能是：主体、客体、事件、后果。
4. 只返回 JSON 数组，不要输出任何解释。

返回格式：
[
  {
    "source": "实体A",
    "source_category": "主体",
    "relation": "关系",
    "target": "实体B",
    "target_category": "事件"
  }
]
""".strip()

# ======================================================================
# Incremental extraction (user follow-up -> ADD actions only)
# ======================================================================

INCREMENTAL_EXTRACTION_PROMPT = """
你是案件图谱增量抽取器。
这是用户最新补充的口供：{user_input}

请只提取这段新口供里新增的实体和关系，并输出 ADD 动作列表。
要求：
1. 只输出本轮新增事实，不要重复改写旧事实。
2. 绝对不要输出 UPDATE 或 DELETE。
3. 实体要做命名对齐，例如"我/本人/用户"统一为"当事人"。
4. category 只能是：主体、客体、事件、后果。
5. 只能输出合法 JSON，不要解释。

输出格式：
{{
  "actions": [
    {{
      "action": "ADD",
      "source": "当事人",
      "source_category": "主体",
      "relation": "主张",
      "target": "赔偿",
      "target_category": "后果"
    }}
  ]
}}
""".strip()

# ======================================================================
# Semantic GC (graph cleanup against latest case summary)
# ======================================================================

SEMANTIC_GC_PROMPT = """
你是严谨的图谱对齐与清理引擎。
这是当前案卷的唯一确凿事实：{case_summary}
这是图谱数据库中现有的节点集合：{current_nodes}
这是图谱数据库中现有的关系集合：{current_graph}

你的任务是交叉比对最新事实与当前图谱，识别两类需要清理的脏数据：
1. 幽灵节点：已经与最新事实冲突、被推翻，或根本不存在于摘要中的节点。
2. 幽灵关系：两个节点本身仍然有效，但它们之间某条旧关系已经被最新事实推翻。

要求：
1. obsolete_nodes 只返回确实应该被删除的节点名称。
2. obsolete_edges 只返回确实应该被删除的旧关系三元组。
3. source 和 target 必须逐字复制自给定节点集合，禁止改写或模糊扩展。
4. 如果节点或关系仍与现有事实一致，则不要删除。
5. 只能输出合法 JSON，不要解释。

输出格式：
{{"obsolete_nodes": ["旧公司名"], "obsolete_edges": [{{"source": "当事人", "relation": "主动辞职", "target": "原公司"}}]}}

如果完全一致，返回：
{{"obsolete_nodes": [], "obsolete_edges": []}}
""".strip()

# ======================================================================
# Belief revision V1 (basic ADD/UPDATE/DELETE)
# ======================================================================

BELIEF_REVISION_PROMPT = """
你是严谨的案情图谱管理员。
这是当前图谱状态：{current_graph}
这是用户的最新输入：{user_input}

请判断新输入是对案情的补充，还是对之前错误信息的修正或推翻，并严格输出 JSON。
动作类型只能是 ADD、UPDATE、DELETE 三者之一。

要求：
1. 如果是新增事实，输出 ADD。
2. 如果是修正已有事实，输出 UPDATE，并明确 old_target 与 new_target。
3. 如果是推翻错误事实，输出 DELETE。
4. 实体要做命名对齐，例如"我/本人/用户"统一为"当事人"。
5. category 只能是：主体、客体、事件、后果。
6. 只能输出 JSON，不要输出解释文字。

输出格式：
{{
  "actions": [
    {{
      "action": "ADD",
      "source": "当事人",
      "source_category": "主体",
      "relation": "主张",
      "target": "赔偿",
      "target_category": "后果"
    }},
    {{
      "action": "UPDATE",
      "source": "当事人",
      "source_category": "主体",
      "relation": "过错状态",
      "old_target": "闯红灯",
      "new_target": "绿灯通行",
      "target_category": "事件"
    }},
    {{
      "action": "DELETE",
      "source": "当事人",
      "relation": "存在事实",
      "target": "错误事实"
    }}
  ]
}}
""".strip()

# ======================================================================
# Belief revision V2 (with conflict detection and examples)
# ======================================================================

BELIEF_REVISION_PROMPT_V2 = """
你是图谱数据库的底层操作员。你的任务是根据用户输入，输出对图谱的 CRUD 操作 JSON。
【当前已有节点】：{existing_nodes}
【当前已有关系】：{current_graph}
【用户最新输入】：{user_input}

危险提醒：
不要把"否认""要求删除""记错了""改成""不是 A 而是 B"这类元指令写进图谱。
遇到这类输入，应直接输出 DELETE 或 UPDATE。

规则：
1. 只要新输入推翻、纠正或覆盖了旧事实，就不能只输出 ADD。
2. 输出 DELETE 或 UPDATE 时，若涉及已有节点，节点名必须逐字复制自 existing_nodes。
3. 实体要做命名对齐，例如"我/本人/用户"统一为"当事人"。
4. category 只能是：主体、客体、事件、后果。
5. 只能输出合法 JSON，不要解释。

正确示例 1：
现有图谱包含：[当事人]-(砸伤)->[车间机器]。用户说："我没在车间被砸，是路上被车撞了。"
输出：
{{"actions": [{{"action": "DELETE", "target_name": "车间机器"}}, {{"action": "DELETE", "target_name": "砸伤"}}, {{"action": "ADD", "source": "当事人", "source_category": "主体", "relation": "撞伤", "target": "肇事车辆", "target_category": "客体"}}]}}

正确示例 2：
现有图谱包含：[机器]。用户说："把跟机器相关的事情删掉。"
输出：
{{"actions": [{{"action": "DELETE", "target_name": "机器"}}]}}

输出格式：
{{
  "actions": [
    {{
      "action": "ADD",
      "source": "当事人",
      "source_category": "主体",
      "relation": "主张",
      "target": "赔偿",
      "target_category": "后果"
    }},
    {{
      "action": "UPDATE",
      "source": "当事人",
      "source_category": "主体",
      "relation": "过错状态",
      "old_target": "闯红灯",
      "new_target": "绿灯通行",
      "target_category": "事件"
    }},
    {{
      "action": "DELETE",
      "target_name": "机器"
    }}
  ]
}}
""".strip()
