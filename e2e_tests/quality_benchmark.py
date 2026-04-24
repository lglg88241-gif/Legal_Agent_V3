"""
Legal Agent V3 — 全面回答质量基准测试
覆盖维度：
  D1 金额计算准确性
  D2 法条真实性
  D3 多轮案情连贯性
  D4 回答完整性
  D5 回答可操作性
  D6 幻觉抑制
  D7 案例引用质量
  D8 系统健康状态（Reranker / sources）
用法: D:/Dev/python310/python.exe e2e_tests/quality_benchmark.py
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# ── 配置 ────────────────────────────────────────────────────────
BASE_URL = "http://127.0.0.1:8000"
POLL_INTERVAL = 1.5
POLL_TIMEOUT = 120
RESULTS_DIR = Path("e2e_tests/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# 真实存在的法条（用于 D2 幻觉检测）
REAL_STATUTES = [
    "劳动合同法第八十二条",
    "劳动合同法第四十七条",
    "劳动合同法第四十八条",
    "劳动合同法第八十七条",
    "劳动合同法第二十三条",
    "劳动合同法第二十四条",
    "刑法第二十条",
    "刑法第二百三十四条",
    "民法典第一千一百六十五条",
    "道路交通安全法第九十一条",
]

# 已知虚假/常见幻觉法条
FAKE_STATUTES = [
    "劳动法第三百条",
    "劳动合同法第一百条",
    "刑法第五百条",
    "民法典第三千条",
]


# ── 基础设施 ─────────────────────────────────────────────────────
def login() -> str:
    for username, password in (("admin", "admin123"), ("lg", "123456")):
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()["access_token"]
    raise AssertionError(f"登录失败: {r.status_code}")


def new_conv(headers: dict) -> int:
    r = requests.post(f"{BASE_URL}/conversations", headers=headers, timeout=10)
    assert r.status_code in (200, 201), f"创建会话失败: {r.status_code}"
    body = r.json()
    return body.get("id") or body.get("conversation", {}).get("id") or 1


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _normalize_dialog_result(
    payload: dict | None,
    *,
    fallback_latency_ms: int,
    default_status: str = "completed",
) -> dict:
    data = payload.copy() if isinstance(payload, dict) else {}

    response_text = data.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        message = data.get("message")
        response_text = message if isinstance(message, str) else ""

    sources = data.get("sources")
    if not isinstance(sources, list):
        sources = []

    try:
        confidence = float(data.get("confidence_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    try:
        latency_ms = int(data.get("latency_ms", 0) or 0)
    except (TypeError, ValueError):
        latency_ms = 0
    if latency_ms <= 0:
        latency_ms = fallback_latency_ms

    data["status"] = str(data.get("status") or default_status)
    data["response"] = response_text
    data["sources"] = sources
    data["confidence_score"] = confidence
    data["latency_ms"] = latency_ms
    data["route_intent"] = str(data.get("route_intent") or data.get("route") or "")
    return data


def ask(headers: dict, cid: int, text: str, force: bool = False) -> dict:
    started = time.perf_counter()
    try:
        r = requests.post(
            f"{BASE_URL}/dialog",
            headers=headers,
            json={
                "user_input": text,
                "conversation_id": cid,
                "force_current_case": force,
                "is_precise_mode": False,
                "proactive_graph_mode": True,
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        return _normalize_dialog_result(
            {"status": "failed", "error": f"post_failed: {exc}"},
            fallback_latency_ms=_elapsed_ms(started),
            default_status="failed",
        )

    if r.status_code != 200:
        return _normalize_dialog_result(
            {"status": "failed", "error": f"HTTP {r.status_code}"},
            fallback_latency_ms=_elapsed_ms(started),
            default_status="failed",
        )

    try:
        first = r.json()
    except ValueError as exc:
        return _normalize_dialog_result(
            {"status": "failed", "error": f"invalid_json: {exc}"},
            fallback_latency_ms=_elapsed_ms(started),
            default_status="failed",
        )

    if first.get("status") == "needs_confirmation" and not force:
        return ask(headers, cid, text, force=True)

    task_id = first.get("task_id")
    if not task_id:
        return _normalize_dialog_result(
            first,
            fallback_latency_ms=_elapsed_ms(started),
            default_status=str(first.get("status") or "completed"),
        )

    for _ in range(int(POLL_TIMEOUT / POLL_INTERVAL)):
        time.sleep(POLL_INTERVAL)
        try:
            sr = requests.get(
                f"{BASE_URL}/dialog/status/{task_id}", headers=headers, timeout=10
            )
        except requests.RequestException:
            continue
        if sr.status_code != 200:
            continue
        data = sr.json()
        if data.get("status") == "completed":
            return _normalize_dialog_result(
                data.get("data", {}),
                fallback_latency_ms=_elapsed_ms(started),
            )
        if data.get("status") == "failed":
            return _normalize_dialog_result(
                {
                    "status": "failed",
                    "error": data.get("error", "task_failed"),
                    "message": data.get("message", ""),
                },
                fallback_latency_ms=_elapsed_ms(started),
                default_status="failed",
            )

    return _normalize_dialog_result(
        {"status": "failed", "error": "timeout"},
        fallback_latency_ms=_elapsed_ms(started),
        default_status="failed",
    )


def _join_turn_responses(*turns: dict) -> str:
    return "\n".join(
        (turn.get("response") or "").strip()
        for turn in turns
        if isinstance(turn, dict) and (turn.get("response") or "").strip()
    )


def _sum_turn_latencies(*turns: dict) -> int:
    total = 0
    for turn in turns:
        try:
            total += int(turn.get("latency_ms", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            continue
    return total


def contains(text: str, *keywords: str) -> bool:
    return all(kw.lower() in text.lower() for kw in keywords)


def contains_any(text: str, *keywords: str) -> bool:
    return any(kw.lower() in text.lower() for kw in keywords)


def contains_real_statute(text: str) -> bool:
    return any(s in text for s in REAL_STATUTES)


def contains_fake_statute(text: str) -> bool:
    return any(s in text for s in FAKE_STATUTES)


# ── 测试结果数据结构 ──────────────────────────────────────────────
@dataclass
class CheckResult:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class TestCase:
    name: str
    dimension: str
    checks: list[CheckResult] = field(default_factory=list)
    latency_ms: int = 0
    sources_count: int = 0
    confidence: float = 0.0
    response_preview: str = ""
    route: str = ""

    @property
    def score(self) -> tuple[int, int]:
        p = sum(1 for c in self.checks if c.passed)
        return p, len(self.checks)


def print_test(tc: TestCase) -> None:
    p, t = tc.score
    print(f"\n{'─'*55}")
    print(f"[{tc.dimension}] {tc.name}")
    print(f"{'─'*55}")
    for c in tc.checks:
        icon = "[PASS]" if c.passed else "[FAIL]"
        detail = f" — {c.detail}" if c.detail else ""
        print(f"  {icon} {c.label}{detail}")
    print(
        f"  得分: {p}/{t} | 延迟: {tc.latency_ms}ms | "
        f"sources: {tc.sources_count} | confidence: {tc.confidence:.2f}"
    )
    if tc.response_preview:
        print(f"  回答预览: {tc.response_preview[:200]}")


# ════════════════════════════════════════════════════════════════
# 测试集
# ════════════════════════════════════════════════════════════════
def test_d1_amount_calculation(headers: dict) -> list[TestCase]:
    """D1: 金额计算准确性"""
    cases = []

    cid = new_conv(headers)
    ask(headers, cid, "我2023年1月入职，月薪10000元，公司一直没签劳动合同")
    r = ask(headers, cid, "我能要双倍工资差额吗？能要多少？")
    text = r.get("response", "")
    tc = TestCase(
        "未签合同双倍工资计算",
        "D1-金额计算",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
        route=r.get("route_intent", ""),
    )
    tc.checks = [
        CheckResult("提到双倍工资", contains(text, "双倍")),
        CheckResult("提到11个月上限", contains_any(text, "11个月", "十一个月", "最多11")),
        CheckResult("计算结果包含10000", contains(text, "10000") or contains(text, "1万")),
        CheckResult("提到起算时间", contains_any(text, "满一个月", "第二个月", "起算")),
        CheckResult("未出现明显错误金额（不含999999）", "999999" not in text),
    ]
    cases.append(tc)

    cid = new_conv(headers)
    ask(headers, cid, "我入职满3年，月薪15000，公司突然口头通知我不用来了，没有任何书面文件")
    r = ask(headers, cid, "我能要赔偿金吗？能要多少？")
    text = r.get("response", "")
    tc2 = TestCase(
        "违法辞退赔偿金计算",
        "D1-金额计算",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
        route=r.get("route_intent", ""),
    )
    tc2.checks = [
        CheckResult("提到赔偿金", contains(text, "赔偿金")),
        CheckResult("提到2N或双倍", contains_any(text, "2倍", "两倍", "2N", "双倍赔偿")),
        CheckResult("月薪15000连贯", contains(text, "15000") or contains(text, "1.5万")),
        CheckResult("提到工作年限3年", contains_any(text, "3年", "三年", "36个月")),
        CheckResult("结果合理（不超过100万）", not any(str(n) in text for n in range(1000000, 9999999, 100000))),
    ]
    cases.append(tc2)

    cid = new_conv(headers)
    r = ask(
        headers,
        cid,
        "我在工厂上班时被机器压断了一根手指，被认定为工伤，月薪8000元，工作了2年，鉴定结果是十级伤残，能获得什么赔偿？",
    )
    text = r.get("response", "")
    tc3 = TestCase(
        "工伤十级伤残赔偿",
        "D1-金额计算",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
        route=r.get("route_intent", ""),
    )
    tc3.checks = [
        CheckResult("提到一次性伤残补助金", contains_any(text, "伤残补助金", "一次性补助")),
        CheckResult("提到十级对应7个月", contains_any(text, "7个月", "七个月")),
        CheckResult("提到工伤保险", contains_any(text, "工伤保险", "工伤基金")),
        CheckResult("月薪8000连贯", contains(text, "8000") or contains(text, "8,000")),
        CheckResult("提到医疗费用", contains_any(text, "医疗", "治疗费")),
    ]
    cases.append(tc3)
    return cases


def test_d2_statute_accuracy(headers: dict) -> list[TestCase]:
    """D2: 法条真实性"""
    cases = []

    cid = new_conv(headers)
    r = ask(headers, cid, "公司拖欠工资3个月，请告诉我相关法律条文，要具体到条款号")
    text = r.get("response", "")
    tc = TestCase(
        "劳动争议法条引用",
        "D2-法条真实性",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    real = [s for s in REAL_STATUTES if s in text]
    fake = [s for s in FAKE_STATUTES if s in text]
    tc.checks = [
        CheckResult("引用了真实存在的法条", contains_real_statute(text), f"命中: {real}" if real else "未命中任何已知真实法条"),
        CheckResult("未引用已知虚假法条", not contains_fake_statute(text), f"幻觉: {fake}" if fake else ""),
        CheckResult("提到劳动合同法", contains(text, "劳动合同法")),
        CheckResult("提到具体条款号", contains_any(text, "第", "条")),
        CheckResult("提到诉讼时效", contains_any(text, "仲裁时效", "时效", "一年")),
    ]
    cases.append(tc)

    cid = new_conv(headers)
    r = ask(headers, cid, "我朋友因为激情犯罪伤害了人，被判故意伤害罪，请问刑法对故意伤害罪是怎么规定的？")
    text = r.get("response", "")
    tc2 = TestCase(
        "刑事故意伤害法条",
        "D2-法条真实性",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    fake2 = [s for s in FAKE_STATUTES if s in text]
    tc2.checks = [
        CheckResult("提到刑法第234条或故意伤害相关条款", contains_any(text, "第二百三十四条", "234条", "故意伤害罪")),
        CheckResult("提到量刑档次", contains_any(text, "三年", "十年", "无期")),
        CheckResult("区分轻伤/重伤", contains_any(text, "轻伤", "重伤")),
        CheckResult("未引用已知虚假法条", not contains_fake_statute(text), f"幻觉: {fake2}" if fake2 else ""),
        CheckResult("提到从宽情节", contains_any(text, "自首", "认罪", "从轻", "和解")),
    ]
    cases.append(tc2)
    return cases


def test_d3_multi_turn_continuity(headers: dict) -> list[TestCase]:
    """D3: 多轮案情连贯性"""
    cases = []

    cid = new_conv(headers)
    r1 = ask(headers, cid, "你好，我想咨询劳动纠纷")
    r2 = ask(headers, cid, "我叫张三，在北京一家科技公司上班，月薪20000元")
    r3 = ask(headers, cid, "公司2024年6月1日突然口头通知我离职，说是优化裁员，没有提前30天通知")
    r4 = ask(headers, cid, "我在公司工作了整整4年，一直没有签劳动合同")
    r5 = ask(headers, cid, "综合我的情况，帮我算一下我能主张哪些权益，总共能要多少钱？")
    text = _join_turn_responses(r1, r2, r3, r4, r5)
    final_text = r5.get("response", "")
    tc = TestCase(
        "五轮劳动争议全程连贯",
        "D3-多轮连贯",
        latency_ms=_sum_turn_latencies(r1, r2, r3, r4, r5),
        sources_count=len(r5.get("sources", [])),
        confidence=r5.get("confidence_score", 0),
        response_preview=final_text[:200],
        route=r5.get("route_intent", ""),
    )
    tc.checks = [
        CheckResult("月薪20000连贯", contains_any(text, "20000", "2万", "两万")),
        CheckResult("工作年限4年连贯", contains_any(text, "4年", "四年", "48个月")),
        CheckResult("未签合同事实连贯", contains(text, "未签") or contains(text, "没有签")),
        CheckResult("提到双倍工资", contains(text, "双倍")),
        CheckResult("提到违法辞退赔偿", contains_any(text, "赔偿金", "违法")),
        CheckResult("提到代通知金", contains_any(text, "代通知金", "提前通知", "一个月工资")),
    ]
    cases.append(tc)

    cid = new_conv(headers)
    r1 = ask(headers, cid, "我借给朋友50000元，有借条，约定2023年12月31日还款")
    r2 = ask(headers, cid, "到期了他一直不还，我催了很多次，他说最多还我30000")
    r3 = ask(headers, cid, "我应该怎么维权？我能要回全部50000吗，还有利息吗？")
    text = _join_turn_responses(r1, r2, r3)
    final_text = r3.get("response", "")
    tc2 = TestCase(
        "借贷纠纷数字连贯",
        "D3-多轮连贯",
        latency_ms=_sum_turn_latencies(r1, r2, r3),
        sources_count=len(r3.get("sources", [])),
        confidence=r3.get("confidence_score", 0),
        response_preview=final_text[:200],
        route=r3.get("route_intent", ""),
    )
    tc2.checks = [
        CheckResult("50000元本金连贯", contains_any(text, "50000", "5万", "五万")),
        CheckResult("提到借条/书面证据", contains_any(text, "借条", "书面", "证据")),
        CheckResult("提到诉讼途径", contains_any(text, "起诉", "法院", "诉讼")),
        CheckResult("提到利息", contains(text, "利息")),
        CheckResult("未把30000当作本金", "30000元本金" not in text),
    ]
    cases.append(tc2)
    return cases


def test_d4_completeness(headers: dict) -> list[TestCase]:
    """D4: 回答完整性"""
    cases = []

    cid = new_conv(headers)
    ask(headers, cid, "我在公司工作5年，月薪12000，没签劳动合同，公司违法辞退我，也没缴社保，病假期间工资被扣了")
    r = ask(headers, cid, "我所有能主张的权益是什么？帮我列个清单")
    text = r.get("response", "")
    tc = TestCase(
        "劳动争议权益清单完整性",
        "D4-回答完整性",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    tc.checks = [
        CheckResult("提到双倍工资差额", contains(text, "双倍")),
        CheckResult("提到违法辞退赔偿金", contains_any(text, "赔偿金", "违法辞退")),
        CheckResult("提到经济补偿金", contains(text, "经济补偿")),
        CheckResult("提到社保补缴", contains_any(text, "社保", "社会保险")),
        CheckResult("提到病假工资", contains_any(text, "病假", "医疗期")),
        CheckResult("提到仲裁时效", contains_any(text, "时效", "一年", "仲裁")),
    ]
    cases.append(tc)

    cid = new_conv(headers)
    r = ask(headers, cid, "我结婚8年，有一套婚前房产（我婚前全款购买），婚后共同购买了一辆车和一套房，还有30万存款，现在想离婚，财产应该怎么分？")
    text = r.get("response", "")
    tc2 = TestCase(
        "离婚财产分割完整性",
        "D4-回答完整性",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    tc2.checks = [
        CheckResult("区分婚前/婚后财产", contains_any(text, "婚前", "婚后")),
        CheckResult("婚前房产归个人", contains_any(text, "个人财产", "婚前财产", "不参与分割")),
        CheckResult("共同财产对半分", contains_any(text, "共同财产", "平均", "各半", "对半")),
        CheckResult("提到存款分割", contains_any(text, "存款", "30万")),
        CheckResult("提到车辆分割", contains(text, "车")),
        CheckResult("提到协议或诉讼途径", contains_any(text, "协议离婚", "诉讼离婚", "法院")),
    ]
    cases.append(tc2)
    return cases


def test_d5_actionability(headers: dict) -> list[TestCase]:
    """D5: 回答可操作性"""
    cases = []

    cid = new_conv(headers)
    r = ask(headers, cid, "我被公司违法辞退，准备申请劳动仲裁，但我不知道需要准备哪些证据，应该怎么收集？")
    text = r.get("response", "")
    tc = TestCase(
        "仲裁证据收集指导",
        "D5-可操作性",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    tc.checks = [
        CheckResult("提到劳动合同", contains(text, "劳动合同")),
        CheckResult("提到工资流水/银行记录", contains_any(text, "工资流水", "银行", "转账记录")),
        CheckResult("提到考勤记录", contains_any(text, "考勤", "打卡")),
        CheckResult("提到辞退通知/书面文件", contains_any(text, "辞退通知", "书面", "解除通知")),
        CheckResult("提到聊天记录", contains_any(text, "聊天记录", "微信", "钉钉", "邮件")),
        CheckResult("给出收集优先级或顺序", contains_any(text, "首先", "优先", "重要", "关键")),
    ]
    cases.append(tc)

    cid = new_conv(headers)
    r = ask(headers, cid, "我要申请劳动仲裁，具体流程是什么？需要去哪里？要准备什么材料？")
    text = r.get("response", "")
    tc2 = TestCase(
        "劳动仲裁流程指导",
        "D5-可操作性",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    tc2.checks = [
        CheckResult("提到劳动仲裁委员会", contains_any(text, "仲裁委", "劳动仲裁委员会")),
        CheckResult("提到申请书", contains_any(text, "申请书", "仲裁申请")),
        CheckResult("提到60天时限", contains_any(text, "60天", "六十天", "45天", "四十五天")),
        CheckResult("提到免费/费用", contains_any(text, "免费", "不收费", "费用")),
        CheckResult("提到管辖地", contains_any(text, "用人单位所在地", "劳动合同履行地", "管辖")),
    ]
    cases.append(tc2)
    return cases


def test_d6_hallucination(headers: dict) -> list[TestCase]:
    """D6: 幻觉抑制"""
    cases = []

    cid = new_conv(headers)
    r = ask(headers, cid, "根据2025年最新修订的劳动合同法第三百二十五条，连续工作满20年的员工可以要求无限期劳动合同，是这样吗？")
    text = r.get("response", "")
    tc = TestCase(
        "虚假法条诱导不编造",
        "D6-幻觉抑制",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    tc.checks = [
        CheckResult("未确认虚假条款存在", not contains(text, "第三百二十五条")),
        CheckResult("未说'是的'/'正确'/'没错'等肯定", not contains_any(text, "是的，", "正确，", "没错，", "确实如此")),
        CheckResult("提到了真实的无固定期限条款", contains_any(text, "无固定期限", "连续工作满十年", "第十四条")),
        CheckResult("有纠正或提示", contains_any(text, "实际上", "正确的是", "根据现行", "应当是")),
    ]
    cases.append(tc)

    cid = new_conv(headers)
    r = ask(headers, cid, "我被公司开除了，我能要多少赔偿？")
    text = r.get("response", "")
    tc2 = TestCase(
        "信息不足时不乱算金额",
        "D6-幻觉抑制",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    tc2.checks = [
        CheckResult("未直接给出具体金额", not contains_any(text, "元赔偿", "元补偿", "共计", "总计")),
        CheckResult("询问或提示缺少信息", contains_any(text, "月薪", "工作年限", "工作时间", "需要了解", "请提供")),
        CheckResult("解释了计算所需信息", contains_any(text, "工资标准", "工作年限", "入职时间")),
        CheckResult("未出现幻觉金额（999999等）", "999999" not in text),
    ]
    cases.append(tc2)

    cid = new_conv(headers)
    r = ask(headers, cid, "我在美国被公司解雇了，美国劳动法怎么规定赔偿的？")
    text = r.get("response", "")
    tc3 = TestCase(
        "境外法律拦截",
        "D6-幻觉抑制",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
        route=r.get("route_intent", ""),
    )
    tc3.checks = [
        CheckResult("路由为OUT_OF_SCOPE", r.get("route_intent") == "OUT_OF_SCOPE"),
        CheckResult("未给出美国法律内容", not contains_any(text, "WARN Act", "at-will", "美国劳动法规定")),
        CheckResult("提示专注中国法律", contains_any(text, "中国法律", "中国大陆", "境内")),
    ]
    cases.append(tc3)
    return cases


def test_d7_case_citation(headers: dict) -> list[TestCase]:
    """D7: 案例引用质量"""
    cases = []

    cid = new_conv(headers)
    r = ask(headers, cid, "有没有关于试用期违法辞退的典型案例？我想了解法院一般怎么判的")
    text = r.get("response", "")
    tc = TestCase(
        "试用期违法辞退案例引用",
        "D7-案例引用",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(r.get("sources", [])),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:300],
    )
    tc.checks = [
        CheckResult("有来源标注或案例编号", contains_any(text, "案例", "判决", "仲裁", "案件")),
        CheckResult("包含案件事实描述", contains_any(text, "原告", "申请人", "劳动者", "被告")),
        CheckResult("包含裁判结果", contains_any(text, "支持", "裁决", "判决", "赔偿", "获得")),
        CheckResult("有参考意义说明", contains_any(text, "参考", "说明", "表明", "体现")),
        CheckResult("有RAG来源", len(r.get("sources", [])) > 0, f"sources={len(r.get('sources', []))}"),
    ]
    cases.append(tc)
    return cases


def test_d8_system_health(headers: dict) -> list[TestCase]:
    """D8: 系统健康状态"""
    cases = []

    cid = new_conv(headers)
    r = ask(headers, cid, "劳动合同试用期最长可以约定多久？")
    text = r.get("response", "")
    sources = r.get("sources", [])
    tc = TestCase(
        "RAG检索质量",
        "D8-系统健康",
        latency_ms=r.get("latency_ms", 0),
        sources_count=len(sources),
        confidence=r.get("confidence_score", 0),
        response_preview=text[:200],
    )
    tc.checks = [
        CheckResult("有检索结果(sources≥1)", len(sources) >= 1, f"sources={len(sources)}"),
        CheckResult("有3个以上检索结果(Reranker正常标志)", len(sources) >= 3, f"sources={len(sources)}，少于3可能Reranker异常"),
        CheckResult("回答提到6个月", contains_any(text, "六个月", "6个月")),
        CheckResult("延迟合理(<30s)", r.get("latency_ms", 99999) < 30000, f"latency={r.get('latency_ms')}ms"),
        CheckResult("有confidence分数", r.get("confidence_score", 0) > 0, f"confidence={r.get('confidence_score', 0):.2f}"),
    ]
    cases.append(tc)
    return cases


# ════════════════════════════════════════════════════════════════
# 主程序
# ════════════════════════════════════════════════════════════════
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Legal Agent V3 质量 benchmark")
    parser.add_argument(
        "--only",
        default="",
        help="只跑指定维度，逗号分隔，例如 D3,D8",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    only_codes = {
        item.strip().upper()
        for item in args.only.split(",")
        if item.strip()
    }

    print("=" * 60)
    print("Legal Agent V3 — 全面回答质量基准测试")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    token = login()
    print("[PASS] 登录成功")
    headers = {"Authorization": f"Bearer {token}"}
    all_cases: list[TestCase] = []
    test_groups = [
        ("D1", "D1 金额计算准确性", test_d1_amount_calculation),
        ("D2", "D2 法条真实性", test_d2_statute_accuracy),
        ("D3", "D3 多轮案情连贯性", test_d3_multi_turn_continuity),
        ("D4", "D4 回答完整性", test_d4_completeness),
        ("D5", "D5 回答可操作性", test_d5_actionability),
        ("D6", "D6 幻觉抑制", test_d6_hallucination),
        ("D7", "D7 案例引用质量", test_d7_case_citation),
        ("D8", "D8 系统健康状态", test_d8_system_health),
    ]
    if only_codes:
        print(f"[INFO] 仅运行维度: {', '.join(sorted(only_codes))}")
    selected_groups = [
        item for item in test_groups if not only_codes or item[0] in only_codes
    ]
    for _, group_name, test_fn in selected_groups:
        print(f"\n{'═'*55}")
        print(f"  {group_name}")
        print(f"{'═'*55}")
        try:
            cases = test_fn(headers)
            for tc in cases:
                print_test(tc)
                all_cases.append(tc)
        except Exception as e:
            print(f"  [FAIL] 测试组异常: {e}")

    print(f"\n{'═'*60}")
    print("总体评分汇总")
    print(f"{'═'*60}")
    dim_scores: dict[str, tuple[int, int]] = {}
    for tc in all_cases:
        p, t = tc.score
        dim = tc.dimension
        prev_p, prev_t = dim_scores.get(dim, (0, 0))
        dim_scores[dim] = (prev_p + p, prev_t + t)

    total_p = total_t = 0
    for dim, (p, t) in sorted(dim_scores.items()):
        pct = 100 * p // t if t else 0
        bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
        print(f"  {dim:<20} {bar} {p}/{t} ({pct}%)")
        total_p += p
        total_t += t
    overall = 100 * total_p // total_t if total_t else 0
    print(f"{'─'*60}")
    print(f"  {'总分':<20} {total_p}/{total_t} ({overall}%)")
    print(f"{'═'*60}")

    report = {
        "generated_at": datetime.now().isoformat(),
        "overall_score": f"{total_p}/{total_t} ({overall}%)",
        "dimension_scores": {
            dim: {"passed": p, "total": t, "pct": 100 * p // t if t else 0}
            for dim, (p, t) in dim_scores.items()
        },
        "test_cases": [
            {
                "name": tc.name,
                "dimension": tc.dimension,
                "score": f"{tc.score[0]}/{tc.score[1]}",
                "latency_ms": tc.latency_ms,
                "sources_count": tc.sources_count,
                "confidence": tc.confidence,
                "route": tc.route,
                "checks": [
                    {"label": c.label, "passed": c.passed, "detail": c.detail}
                    for c in tc.checks
                ],
                "response_preview": tc.response_preview,
            }
            for tc in all_cases
        ],
    }
    if only_codes:
        suffix = "_".join(code.lower() for code in sorted(only_codes))
        out_path = RESULTS_DIR / f"quality_benchmark_report_{suffix}.json"
    else:
        out_path = RESULTS_DIR / "quality_benchmark_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n 详细报告已保存: {out_path}")

    failed = [
        (tc.name, c.label, c.detail)
        for tc in all_cases
        for c in tc.checks
        if not c.passed
    ]
    if failed:
        print(f"\n⚠️  未通过检查项 ({len(failed)} 项):")
        for name, label, detail in failed:
            d = f" — {detail}" if detail else ""
            print(f"  [{name}] {label}{d}")


if __name__ == "__main__":
    main()
