"""
5 套多轮对话剧本，模拟真实用户从开场到结案的完整流程。

每个剧本是一个 list[dict]，每个 dict 包含：
  user_input      用户发送的消息
  expect_keywords 期望回答中包含的关键词（列表，命中 1 个即算过）
  expect_route    期望的 route_intent（None = 不检查）
  check_sources   是否期望有 RAG 引用来源
  description     这一轮在测什么
"""
from __future__ import annotations

SCENARIO_1_LABOR = [
    {
        "user_input": "你好",
        "expect_keywords": ["你好", "您好", "法律", "咨询", "帮助", "助手"],
        "expect_route": "GREETING",
        "check_sources": False,
        "description": "开场问候，期望 GREETING 路由",
    },
    {
        "user_input": "我被公司开除了，他们说我没过试用期",
        "expect_keywords": ["试用期", "劳动", "合同", "补偿", "证明", "情况", "月薪", "入职"],
        "expect_route": None,
        "check_sources": False,
        "description": "首次陈述案情，期望系统追问补充信息",
    },
    {
        "user_input": "我2023年3月1日入职深圳一家科技公司，月薪12000元，没签劳动合同",
        "expect_keywords": ["12000", "劳动合同", "未签", "试用期", "深圳"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "补充核心案情：入职时间、薪资、未签合同",
    },
    {
        "user_input": "公司4月14日口头通知我不用来了，说试用期不合格，没有任何书面文件",
        "expect_keywords": ["口头", "书面", "通知", "证据", "录音", "试用期"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "补充解除方式：口头通知，无书面文件",
    },
    {
        "user_input": "我能要多少赔偿？有没有类似案例？",
        "expect_keywords": ["赔偿", "二倍工资", "经济补偿", "12000", "仲裁"],
        "expect_route": None,
        "check_sources": True,
        "description": "核心诉求：期望有具体金额计算 + RAG 案例命中",
    },
    {
        "user_input": "帮我整理一下我可以主张的所有权益",
        "expect_keywords": ["已知事实", "算账", "确权", "高风险", "下一步", "落地"],
        "expect_route": None,
        "check_sources": False,
        "description": "期望输出完整四层分层回答结构",
    },
]

SCENARIO_2_SELF_DEFENSE = [
    {
        "user_input": "打架中反击把对方打伤，算不算正当防卫？有没有案例？",
        "expect_keywords": ["正当防卫", "不法侵害", "必要限度", "认定"],
        "expect_route": None,
        "check_sources": True,
        "description": "单轮直接命中正当防卫知识点",
    },
    {
        "user_input": "对方先用酒瓶砸我头，我捡起椅子打了回去",
        "expect_keywords": ["先动手", "防卫", "侵害", "证据", "监控", "证人"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "补充关键事实：对方先动手",
    },
    {
        "user_input": "对方住院了，伤情鉴定是轻伤二级",
        "expect_keywords": ["轻伤", "二级", "防卫", "责任", "伤情"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "补充伤情结果",
    },
    {
        "user_input": "我会被判刑吗？有没有类似判轻的案例？",
        "expect_keywords": ["判刑", "正当防卫", "案例", "情节", "轻"],
        "expect_route": None,
        "check_sources": True,
        "description": "期望有案例引用，分析判决风险",
    },
    {
        "user_input": "正当防卫和防卫过当的界限是什么？",
        "expect_keywords": ["防卫过当", "必要限度", "超过", "法条", "刑法"],
        "expect_route": None,
        "check_sources": True,
        "description": "法律知识点：防卫界限，期望法条引用",
    },
]

SCENARIO_3_PRIVACY = [
    {
        "user_input": "非法买卖公民个人信息会怎么判？有没有典型案例？",
        "expect_keywords": ["个人信息", "刑事", "侵犯", "案例", "判决"],
        "expect_route": None,
        "check_sources": True,
        "description": "直接命中个人信息案例库",
    },
    {
        "user_input": "如果是公司内部员工泄露的呢？",
        "expect_keywords": ["内部员工", "泄露", "职务", "责任", "违规"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "追问内部员工泄露场景",
    },
    {
        "user_input": "泄露了大概5000条用户手机号和身份证号",
        "expect_keywords": ["5000", "手机号", "身份证", "数量", "情节"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "补充泄露规模和数据类型",
    },
    {
        "user_input": "除了刑事责任，受害者能要求民事赔偿吗？有没有案例？",
        "expect_keywords": ["民事", "赔偿", "精神损害", "案例", "受害者"],
        "expect_route": None,
        "check_sources": True,
        "description": "期望同时覆盖刑民两条线，有案例引用",
    },
]

SCENARIO_4_DUI = [
    {
        "user_input": "醉驾但情节较轻会怎么判？有没有危险驾驶的典型案例？",
        "expect_keywords": ["醉驾", "危险驾驶", "案例", "判决", "拘役"],
        "expect_route": None,
        "check_sources": True,
        "description": "直接命中醉驾案例，README 标准问题",
    },
    {
        "user_input": "血液酒精含量是85mg/100ml，刚过醉驾标准",
        "expect_keywords": ["80mg", "酒精", "标准", "醉酒", "情节"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "补充关键事实：酒精含量",
    },
    {
        "user_input": "没有造成事故，也没有前科",
        "expect_keywords": ["未造成", "前科", "情节", "从轻", "缓刑"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "补充有利情节",
    },
    {
        "user_input": "能争取缓刑吗？有没有类似判缓刑的案例？",
        "expect_keywords": ["缓刑", "案例", "争取", "判决", "条件"],
        "expect_route": None,
        "check_sources": True,
        "description": "期望缓刑可能性分析 + 类案引用",
    },
]

SCENARIO_5_ENVIRONMENT = [
    {
        "user_input": "你好，我想咨询环境污染的问题",
        "expect_keywords": ["你好", "环境", "污染", "法律", "咨询", "情况"],
        "expect_route": None,
        "check_sources": False,
        "description": "开场，可能是 GREETING 或追问具体情况",
    },
    {
        "user_input": "我们村旁边的化工厂偷偷排污水到河里，已经好几年了",
        "expect_keywords": ["化工厂", "排污", "违法", "环境", "证据", "监管"],
        "expect_route": "CASE_UPDATE",
        "check_sources": False,
        "description": "陈述核心事实：长期非法排污",
    },
    {
        "user_input": "村民集体去告可以吗？有没有环境公益诉讼的案例？",
        "expect_keywords": ["公益诉讼", "集体", "案例", "环境", "起诉", "原告"],
        "expect_route": None,
        "check_sources": True,
        "description": "期望公益诉讼机制解释 + 案例引用",
    },
    {
        "user_input": "企业非法倾倒危险废物，需要承担哪些民事责任？",
        "expect_keywords": ["危险废物", "民事责任", "赔偿", "修复", "环境"],
        "expect_route": None,
        "check_sources": True,
        "description": "README 标准问题，期望直接命中",
    },
    {
        "user_input": "法院会不会判他们修复生态环境？",
        "expect_keywords": ["修复", "生态", "判决", "法院", "责任"],
        "expect_route": None,
        "check_sources": True,
        "description": "期望有具体判决结果引用",
    },
]

# README 8 个标准测试问题（单轮独立会话）
README_STANDARD_QUESTIONS = [
    {
        "question": "公司不签劳动合同，但长期考勤和发工资，算不算存在劳动关系？有没有指导案例？",
        "expect_keywords": ["劳动关系", "考勤", "工资", "案例", "认定"],
        "description": "劳动关系认定",
    },
    {
        "question": "主播和网络平台之间是否构成劳动关系？有没有指导案例？",
        "expect_keywords": ["主播", "平台", "劳动关系", "案例"],
        "description": "平台用工劳动关系",
    },
    {
        "question": "员工离职后被公司主张竞业限制违约金，法院一般怎么判？有没有案例？",
        "expect_keywords": ["竞业限制", "违约金", "案例", "判决"],
        "description": "竞业限制案例",
    },
    {
        "question": "非法买卖公民个人信息会怎么判？有没有典型案例？",
        "expect_keywords": ["个人信息", "刑事", "判决", "案例"],
        "description": "个人信息刑事案例",
    },
    {
        "question": "打架中反击致人受伤，算不算正当防卫？有没有案例？",
        "expect_keywords": ["正当防卫", "案例", "认定", "伤害"],
        "description": "正当防卫案例",
    },
    {
        "question": "醉驾情节较轻会怎么判？有没有危险驾驶案例？",
        "expect_keywords": ["醉驾", "危险驾驶", "案例", "判决"],
        "description": "危险驾驶案例",
    },
    {
        "question": "企业非法倾倒危险废物，要承担哪些责任？有没有环境公益诉讼案例？",
        "expect_keywords": ["危险废物", "责任", "公益诉讼", "案例"],
        "description": "环境公益诉讼案例",
    },
    {
        "question": "网络发言侵害名誉权，法院一般怎么判？有没有案例？",
        "expect_keywords": ["名誉权", "侵害", "案例", "判决", "网络"],
        "description": "名誉权网络侵权案例",
    },
]

ALL_SCENARIOS = [
    ("劳动争议完整咨询链", SCENARIO_1_LABOR),
    ("正当防卫案例咨询", SCENARIO_2_SELF_DEFENSE),
    ("个人信息泄露咨询", SCENARIO_3_PRIVACY),
    ("醉驾危险驾驶咨询", SCENARIO_4_DUI),
    ("环境公益诉讼咨询", SCENARIO_5_ENVIRONMENT),
]
