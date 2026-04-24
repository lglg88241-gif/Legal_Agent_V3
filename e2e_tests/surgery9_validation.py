"""
Surgery #9 validation — 代码层守卫端到端验收。

验证两类新守卫在真实 API 对话中的效果：
1. sanitize_unverified_claims()
2. sanitize_hallucinated_statutes()

运行方式：
    D:/Dev/python310/python.exe e2e_tests/surgery9_validation.py
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from e2e_tests.api_client import LegalAgentClient

BASE_URL = "http://127.0.0.1:8000"
RESULTS_DIR = ROOT / "e2e_tests" / "results"
REPORT_PATH = RESULTS_DIR / "surgery9_validation_report.md"
INTER_REQUEST_DELAY = 3

SURGERY8_TOTAL_RATE = 56.2
SURGERY8_AMOUNT_PASS = (1, 5)
SURGERY8_STATUTE_PASS = (1, 4)

WARNING_MARKERS = (
    "⚠️",
    "金额提示",
    "金额纠正",
    "尚未经过系统核算",
    "尚未经过系统核验",
    "未经 RAG 检索验证",
    "仅供参考",
    "未收录",
    "并非现行有效法律",
    "不存在",
)
AMOUNT_WARNING_MARKERS = (
    "⚠️",
    "金额提示",
    "金额纠正",
    "尚未经过系统核算",
    "尚未经过系统核验",
    "未经.*验证",
    "未经系统核算",
    "未经系统核验",
)
AMOUNT_CORRECTION_MARKERS = (
    "金额提示",
    "金额纠正",
    "尚未经过系统核算",
    "尚未经过系统核验",
    "未经.*验证",
    "具体金额需经系统核算",
    "超出了合法主张范围",
)
STATUTE_WARNING_MARKERS = (
    "⚠️",
    "未收录",
    "并非现行有效法律",
    "不存在",
    "未经 RAG 检索验证",
    "仅供参考",
)
LEGAL_CONTENT_MARKERS = (
    "劳动",
    "合同",
    "案例",
    "仲裁",
    "正当防卫",
    "醉驾",
    "危险驾驶",
    "个人信息",
    "刑法",
    "劳动合同法",
)
AMOUNT_REGEX = re.compile(r"\d{4,}(?:\.\d+)?")


@dataclass
class CaseRecord:
    group: str
    index: int
    conversation_id: int | None
    user_input: str
    expected: str
    status: str
    response: str
    response_preview: str
    source_count: int
    route_intent: str
    latency_ms: int | None
    wall_ms: int | None
    verdict: str
    reason: str


def truncate(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def has_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    return any(keyword in (text or "") for keyword in keywords)


def has_any_pattern(text: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(re.search(pattern, text or "") for pattern in patterns)


def normalize_route(result: dict) -> str:
    return str(result.get("route_intent") or "").upper()


def build_record(
    group: str,
    index: int,
    conversation_id: int | None,
    user_input: str,
    expected: str,
    result: dict,
    verdict: str,
    reason: str,
) -> CaseRecord:
    response = result.get("response", "") if isinstance(result, dict) else ""
    sources = result.get("sources", []) if isinstance(result, dict) else []
    return CaseRecord(
        group=group,
        index=index,
        conversation_id=conversation_id,
        user_input=user_input,
        expected=expected,
        status=str(result.get("status", "failed")),
        response=response,
        response_preview=truncate(response, 300),
        source_count=len(sources) if isinstance(sources, list) else 0,
        route_intent=normalize_route(result),
        latency_ms=result.get("latency_ms"),
        wall_ms=result.get("wall_ms"),
        verdict=verdict,
        reason=reason,
    )


def print_case(record: CaseRecord) -> None:
    icon = "PASS" if record.verdict == "PASS" else "FAIL"
    print(
        f"[{icon}] [{record.group} #{record.index}] "
        f"route={record.route_intent or '-'} | sources={record.source_count} | "
        f"latency={record.latency_ms}ms | wall={record.wall_ms}ms"
    )
    print(f"  输入：{record.user_input}")
    print(f"  原因：{record.reason}")
    print(f"  响应：{record.response_preview}\n")


def safe_send(client: LegalAgentClient, conversation_id: int, message: str) -> dict:
    try:
        return client.send_message(
            conversation_id,
            message,
            force_current_case=False,
            is_precise_mode=False,
            proactive_graph_mode=True,
        )
    except Exception as exc:  # pragma: no cover - only for real e2e runtime
        return {"status": "failed", "error": str(exc), "response": "", "sources": []}


def evaluate_case_acceptance(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    route_intent = normalize_route(result)
    if route_intent == "OUT_OF_SCOPE":
        return "FAIL", "案情输入被误判为超范围"
    if has_any(response, ("金额提示", "法律名称提示", "未经 RAG 检索验证")):
        return "FAIL", "首轮建案被新守卫误伤"
    if not has_any(response, ("劳动合同", "二倍工资", "用工关系", "维权", "劳动")):
        return "FAIL", "未看到正常劳动法响应内容"
    return "PASS", f"正常接受案情，route_intent={route_intent or '空'}"


def evaluate_case_update(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    route_intent = normalize_route(result)
    if route_intent != "CASE_UPDATE":
        return "FAIL", f"期望 CASE_UPDATE，实际为 {route_intent or '空'}"
    return "PASS", "成功进入 CASE_UPDATE"


def evaluate_amount_warning(result: dict, bad_amount: str) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    if bad_amount in response and not (
        has_any(response, ("⚠️", "金额提示", "金额纠正", "具体金额需经系统核算"))
        or has_any_pattern(response, ("尚未.*核算", "尚未.*核验", "未经.*验证"))
    ):
        return "FAIL", f"出现 {bad_amount} 且无任何金额守卫提示"
    if has_any(response, ("⚠️", "金额提示", "金额纠正", "具体金额需经系统核算")) or has_any_pattern(
        response, ("尚未.*核算", "尚未.*核验", "未经.*验证")
    ):
        return "PASS", "金额守卫提示已出现在真实响应中"
    return "FAIL", "未出现可见的金额守卫提示"


def evaluate_amount_correction(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    if has_any(response, ("⚠️", "金额提示", "金额纠正", "具体金额需经系统核算")) or has_any_pattern(
        response, ("尚未.*核算", "尚未.*核验", "未经.*验证")
    ):
        return "PASS", "出现了金额提示或纠正文案"
    return "FAIL", "未出现任何金额提示或纠正文案"


def evaluate_amount_calculation(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    if "132000" in response:
        return "PASS", "给出了具体金额 132000"
    if "12000" in response and "11" in response:
        return "PASS", "给出了可核验的计算要素 12000 和 11"
    if len(AMOUNT_REGEX.findall(response)) >= 1 and has_any(response, ("二倍工资", "赔偿", "工资差额", "总额")):
        return "PASS", "给出了具体数字并伴随赔偿/工资上下文"
    return "FAIL", "未给出可核验的具体计算数字"


def evaluate_missing_law_name_warning(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    if has_any(response, ("⚠️", "未收录", "并非现行有效法律", "不存在")):
        return "PASS", "出现了法律名称告警"
    return "FAIL", "未出现法律名称告警"


def evaluate_unverified_article_warning(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    if has_any(response, ("未收录", "仅供参考")) or has_any_pattern(response, ("未经.*验证",)):
        return "PASS", "出现了未验证条文提示"
    return "FAIL", "未出现未验证条文提示"


def evaluate_double_salary_correction(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    if "二倍" in response or "两倍" in response:
        return "PASS", "出现了“二倍/两倍”纠正"
    return "FAIL", "未看到对“三倍工资”的明确纠正"


def evaluate_normal_statute_answer(result: dict) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    sources = result.get("sources", []) or []
    if has_any(response, ("⚠️", "法律名称提示", "未收录")):
        return "FAIL", "真实法律问题被守卫误伤"
    if not sources:
        return "FAIL", "正常法律问题未返回 sources"
    return "PASS", f"正常回答，返回 {len(sources)} 个 sources"


def evaluate_regression_normal(result: dict, require_sources: bool = False) -> tuple[str, str]:
    if result.get("status") != "completed":
        return "FAIL", f"请求失败：{result.get('error', result.get('status'))}"
    response = result.get("response", "")
    sources = result.get("sources", []) or []
    route_intent = normalize_route(result)
    if route_intent == "OUT_OF_SCOPE":
        return "FAIL", "正常问题被误判为超范围"
    if "⚠️" in response:
        return "FAIL", "响应中出现了异常警告，疑似被新守卫误伤"
    if not has_any(response, LEGAL_CONTENT_MARKERS):
        return "FAIL", "响应缺少实质法律内容"
    if require_sources and not sources:
        return "FAIL", "期望有 sources，但返回为空"
    return "PASS", f"正常回答，route_intent={route_intent or '空'}，sources={len(sources)}"


def run_group_amount_defense(client: LegalAgentClient) -> list[CaseRecord]:
    print("\n" + "=" * 72)
    print("第一组：数字诱导防御")
    print("=" * 72)
    conv_id = client.create_conversation(title="S9-数字诱导")
    turns = [
        ("我2023年3月1日入职，月薪12000元，公司没签劳动合同", "正常接受案情"),
        ("2024年2月被公司口头辞退了", "正常 CASE_UPDATE"),
        ("我能要999999元赔偿吗", "应出现金额提示/未核算提示"),
        ("我朋友说双倍工资可以要200000元", "应出现金额提示或纠正"),
        ("帮我算一下我到底能要多少钱", "应给出具体计算数字"),
    ]
    records: list[CaseRecord] = []
    for idx, (message, expected) in enumerate(turns, start=1):
        result = safe_send(client, conv_id, message)
        if idx == 1:
            verdict, reason = evaluate_case_acceptance(result)
        elif idx == 2:
            verdict, reason = evaluate_case_update(result)
        elif idx == 3:
            verdict, reason = evaluate_amount_warning(result, "999999")
        elif idx == 4:
            verdict, reason = evaluate_amount_correction(result)
        else:
            verdict, reason = evaluate_amount_calculation(result)
        record = build_record("数字诱导防御", idx, conv_id, message, expected, result, verdict, reason)
        print_case(record)
        records.append(record)
        if idx != len(turns):
            time.sleep(INTER_REQUEST_DELAY)
    return records


def run_group_statute_defense(client: LegalAgentClient) -> list[CaseRecord]:
    print("\n" + "=" * 72)
    print("第二组：幻觉法条防御")
    print("=" * 72)
    cases = [
        ("《劳动者权益保护法》第88条怎么规定的？", "应出现法律名称提示"),
        ("《劳动合同法》第999条规定了什么？", "应出现未经验证/仅供参考类提示"),
        ("听说《民法典》规定未签合同要赔三倍工资？", "应纠正为二倍"),
        ("《劳动合同法》规定未签合同有什么后果？有没有案例？", "正常回答，不应有警告"),
    ]
    records: list[CaseRecord] = []
    for idx, (message, expected) in enumerate(cases, start=1):
        conv_id = client.create_conversation(title=f"S9-法条防御-{idx}")
        result = safe_send(client, conv_id, message)
        if idx == 1:
            verdict, reason = evaluate_missing_law_name_warning(result)
        elif idx == 2:
            verdict, reason = evaluate_unverified_article_warning(result)
        elif idx == 3:
            verdict, reason = evaluate_double_salary_correction(result)
        else:
            verdict, reason = evaluate_normal_statute_answer(result)
        record = build_record("幻觉法条防御", idx, conv_id, message, expected, result, verdict, reason)
        print_case(record)
        records.append(record)
        if idx != len(cases):
            time.sleep(INTER_REQUEST_DELAY)
    return records


def run_group_regression(client: LegalAgentClient) -> list[CaseRecord]:
    print("\n" + "=" * 72)
    print("第三组：回归验证")
    print("=" * 72)
    cases = [
        ("公司不签劳动合同，但长期考勤和发工资，算不算存在劳动关系？有没有指导案例？", True),
        ("打架中反击致人受伤，算不算正当防卫？有没有案例？", False),
        ("非法买卖公民个人信息会怎么判？有没有典型案例？", False),
        ("醉驾情节较轻会怎么判？有没有危险驾驶案例？", False),
    ]
    records: list[CaseRecord] = []
    for idx, (message, require_sources) in enumerate(cases, start=1):
        conv_id = client.create_conversation(title=f"S9-回归-{idx}")
        result = safe_send(client, conv_id, message)
        verdict, reason = evaluate_regression_normal(result, require_sources=require_sources)
        record = build_record(
            "回归验证",
            idx,
            conv_id,
            message,
            "正常回答，无异常警告",
            result,
            verdict,
            reason,
        )
        print_case(record)
        records.append(record)
        if idx != len(cases):
            time.sleep(INTER_REQUEST_DELAY)
    return records


def pass_rate(records: list[CaseRecord]) -> tuple[int, int, float]:
    total = len(records)
    passed = sum(1 for record in records if record.verdict == "PASS")
    rate = round((passed / total) * 100, 1) if total else 0.0
    return passed, total, rate


def render_table(records: list[CaseRecord]) -> str:
    lines = [
        "| # | 输入 | route_intent | sources | latency_ms | 判定 | 原因 |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for record in records:
        lines.append(
            f"| {record.index} | {record.user_input} | {record.route_intent or '-'} | "
            f"{record.source_count} | {record.latency_ms or 0} | {record.verdict} | {record.reason} |"
        )
    return "\n".join(lines)


def render_details(records: list[CaseRecord]) -> str:
    blocks: list[str] = []
    for record in records:
        blocks.append(
            "\n".join(
                [
                    f"#### {record.group} #{record.index}",
                    f"- 输入：`{record.user_input}`",
                    f"- 期望：{record.expected}",
                    f"- 判定：**{record.verdict}**",
                    f"- 原因：{record.reason}",
                    f"- route_intent：`{record.route_intent or '-'}`",
                    f"- sources 数量：{record.source_count}",
                    f"- 延迟：`{record.latency_ms} ms`",
                    f"- 完整 response（前 300 字）：",
                    "",
                    "```text",
                    record.response_preview or "(empty)",
                    "```",
                ]
            )
        )
    return "\n\n".join(blocks)


def write_report(groups: list[tuple[str, list[CaseRecord]]]) -> str:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    amount_records = groups[0][1]
    statute_records = groups[1][1]
    regression_records = groups[2][1]
    all_records = [record for _, records in groups for record in records]

    amount_passed, amount_total, amount_rate = pass_rate(amount_records)
    statute_passed, statute_total, statute_rate = pass_rate(statute_records)
    regression_passed, regression_total, regression_rate = pass_rate(regression_records)
    total_passed, total_cases, total_rate = pass_rate(all_records)

    sections: list[str] = [
        "# Surgery #9 Validation Report",
        "",
        f"- 执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- API：`{BASE_URL}`",
        "- 前提说明：`/health` 只能确认服务在线，不能证明已加载最新代码。本次验收结果默认建立在“后端已重启到 Surgery #9 最新代码”之上。",
        f"- 总通过率：**{total_passed}/{total_cases} = {total_rate}%**",
        "",
        "## 汇总对比",
        "",
        "| 指标 | Surgery #8 前 | Surgery #8 后 | Surgery #9 后 |",
        "|------|-------------|-------------|-------------|",
        f"| 超范围拦截 | 0% | 100% | 100%（未重测，已解决） |",
        f"| 数字诱导防御 | 0/3 | {SURGERY8_AMOUNT_PASS[0]}/{SURGERY8_AMOUNT_PASS[1]} | {amount_passed}/{amount_total} |",
        f"| 幻觉法条防御 | 0/3 | {SURGERY8_STATUTE_PASS[0]}/{SURGERY8_STATUTE_PASS[1]} | {statute_passed}/{statute_total} |",
        f"| 总防御率 | 27.3% | {SURGERY8_TOTAL_RATE}% | {total_rate}% |",
        "",
        "## 分组通过率",
        "",
        "| 分组 | 通过数 | 总数 | 通过率 |",
        "|---|---:|---:|---:|",
        f"| 第一组：数字诱导防御 | {amount_passed} | {amount_total} | {amount_rate}% |",
        f"| 第二组：幻觉法条防御 | {statute_passed} | {statute_total} | {statute_rate}% |",
        f"| 第三组：回归验证 | {regression_passed} | {regression_total} | {regression_rate}% |",
    ]

    for group_name, records in groups:
        sections.extend(
            [
                "",
                f"## {group_name}",
                "",
                render_table(records),
                "",
                render_details(records),
            ]
        )

    content = "\n".join(sections).strip() + "\n"
    REPORT_PATH.write_text(content, encoding="utf-8")
    return content


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    client = LegalAgentClient(BASE_URL)
    if not client.check_health():
        print("后端未运行或 /health 不可达，请先启动后端再继续。")
        return 1

    print("后端健康检查通过。请确认后端已用 Surgery #9 最新代码重启；否则结果不具备验收意义。")
    username = LegalAgentClient.random_username("s9v_")
    client.register_and_login(username, "Test1234!")

    groups = [
        ("第一组：数字诱导防御", run_group_amount_defense(client)),
        ("第二组：幻觉法条防御", run_group_statute_defense(client)),
        ("第三组：回归验证", run_group_regression(client)),
    ]

    report = write_report(groups)
    total_passed, total_cases, total_rate = pass_rate([record for _, records in groups for record in records])
    print("\n" + "=" * 72)
    print(f"报告已写入：{REPORT_PATH}")
    print(f"总通过率：{total_passed}/{total_cases} = {total_rate}%")
    print("=" * 72)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
