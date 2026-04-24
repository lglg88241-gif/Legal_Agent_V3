"""
Legal Agent V3 — 回答质量评估
评估维度：相关性、法律要素覆盖、可操作性、案情理解
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 1.5


@dataclass
class QualityCase:
    """一个质量评估案例"""

    name: str
    description: str
    turns: list[tuple[str, bool]]
    eval_turn: int
    must_contain: list[str]
    must_not_contain: list[str]
    min_length: int = 100
    score_weight: float = 1.0


QUALITY_CASES = [
    QualityCase(
        name="未签劳动合同-二倍工资",
        description="用户未签劳动合同，应提到二倍工资赔偿",
        turns=[
            (
                "我2023年3月入职一家公司，月薪10000元，公司一直没有和我签劳动合同，"
                "2024年1月被口头辞退，我有哪些权利可以主张？",
                False,
            ),
        ],
        eval_turn=0,
        must_contain=["二倍工资", "劳动合同法", "经济补偿"],
        must_not_contain=[],
        min_length=200,
    ),
    QualityCase(
        name="试用期违法-赔偿金",
        description="试用期被违法辞退，应提到赔偿金（2N）而非补偿金（N）",
        turns=[
            (
                "我入职一个月被公司说试用期不合格辞退，但公司根本没有说明具体原因，"
                "这合法吗？我能要求什么赔偿？",
                False,
            ),
        ],
        eval_turn=0,
        must_contain=["试用期", "赔偿金", "违法"],
        must_not_contain=[],
        min_length=150,
    ),
    QualityCase(
        name="工伤认定-流程",
        description="工伤应提到认定流程和工伤保险",
        turns=[
            ("我在工作中受伤了，公司不承认是工伤，我应该怎么办？", False),
        ],
        eval_turn=0,
        must_contain=["工伤认定", "劳动能力鉴定"],
        must_not_contain=[],
        min_length=150,
    ),
    QualityCase(
        name="正当防卫-判断标准",
        description="应提到正当防卫的必要限度和刑法第20条",
        turns=[
            (
                "对方先动手打我，我反击把对方打伤了，我会被判刑吗？"
                "有没有正当防卫的相关案例？",
                False,
            ),
        ],
        eval_turn=0,
        must_contain=["正当防卫", "必要限度", "刑法"],
        must_not_contain=[],
        min_length=200,
    ),
    QualityCase(
        name="多轮案情理解-金额连贯",
        description="多轮对话后，系统应理解并记住案情，回答金额应基于已知事实",
        turns=[
            ("我2023年6月入职，月薪15000元，公司没签劳动合同", False),
            ("2024年3月公司把我辞退了，说是业绩不达标，没有出示任何证据", True),
            ("我具体能要多少赔偿金？帮我算一下", True),
        ],
        eval_turn=2,
        must_contain=["15000", "补偿", "月"],
        must_not_contain=["999999", "100000000"],
        min_length=100,
    ),
    QualityCase(
        name="离婚财产分割-共同财产",
        description="应提到夫妻共同财产原则和具体分割规则",
        turns=[
            (
                "我结婚8年，婚后买了一套房子和一辆车，现在要离婚，"
                "财产怎么分？如果对方不配合怎么办？",
                False,
            ),
        ],
        eval_turn=0,
        must_contain=["共同财产", "分割"],
        must_not_contain=[],
        min_length=200,
    ),
    QualityCase(
        name="合同违约-违约金上限",
        description="应提到违约金不超过实际损失的30%原则",
        turns=[
            (
                "我和一家公司签了服务合同，合同约定违约金20万，"
                "但我实际损失只有2万，对方要求我全额赔偿，合理吗？",
                False,
            ),
        ],
        eval_turn=0,
        must_contain=["违约金", "实际损失"],
        must_not_contain=[],
        min_length=150,
    ),
    QualityCase(
        name="醉驾-缓刑可能性",
        description="应提到危险驾驶罪的量刑标准和缓刑条件",
        turns=[
            (
                "我醉驾被查了，血液酒精含量95mg/100ml，没有造成事故，"
                "也没有前科，能争取缓刑吗？",
                False,
            ),
        ],
        eval_turn=0,
        must_contain=["危险驾驶", "缓刑"],
        must_not_contain=[],
        min_length=150,
    ),
    QualityCase(
        name="可操作性-下一步行动",
        description="实用性测试：回答应给出具体可操作的下一步",
        turns=[
            ("公司欠我三个月工资不发，我应该怎么办？", False),
        ],
        eval_turn=0,
        must_contain=["劳动仲裁", "申请"],
        must_not_contain=[],
        min_length=150,
    ),
    QualityCase(
        name="拒绝幻觉金额",
        description="守卫测试：不应在回答中给出没有依据的具体金额",
        turns=[
            (
                "我被公司违法辞退，我月薪10000工作了2年，"
                "帮我精确计算一下我能拿多少钱",
                False,
            ),
        ],
        eval_turn=0,
        must_contain=["补偿", "月"],
        must_not_contain=["999999", "1000000"],
        min_length=100,
    ),
]


def login() -> Optional[str]:
    for username, password in [("lg", "123456"), ("admin", "admin123")]:
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("access_token")
    return None


def new_conv(headers: dict[str, str]) -> int:
    r = requests.post(
        f"{BASE_URL}/conversations",
        json={"title": "Quality Eval"},
        headers=headers,
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return int(data["conversation"]["id"])


def ask(headers: dict[str, str], conv_id: int, text: str, force: bool = False) -> dict[str, Any]:
    r = requests.post(
        f"{BASE_URL}/dialog",
        headers=headers,
        json={
            "user_input": text,
            "conversation_id": conv_id,
            "force_current_case": force,
            "is_precise_mode": False,
            "proactive_graph_mode": False,
        },
        timeout=30,
    )
    if r.status_code != 200:
        return {"error": r.status_code, "phase": "submit"}

    body = r.json()
    if body.get("status") == "needs_confirmation":
        return ask(headers, conv_id, text, force=True)

    task_id = body.get("task_id")
    if not task_id:
        return {"error": "missing_task_id", "payload": body}

    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        sr = requests.get(
            f"{BASE_URL}/dialog/status/{task_id}",
            headers=headers,
            timeout=15,
        )
        if sr.status_code == 200:
            d = sr.json()
            if d.get("status") == "completed":
                return d.get("data", {})
            if d.get("status") == "failed":
                return {"error": "failed", "payload": d}
    return {"error": "timeout", "task_id": task_id}


def evaluate_response(response: str, case: QualityCase) -> dict[str, Any]:
    """对回答做结构化质量评估"""
    scores: dict[str, bool] = {}
    scores["有效回答"] = len(response) >= case.min_length

    must_hits = [kw for kw in case.must_contain if kw in response]
    must_miss = [kw for kw in case.must_contain if kw not in response]
    scores["法律要素覆盖"] = len(must_hits) == len(case.must_contain)

    bad_hits = [kw for kw in case.must_not_contain if kw in response]
    scores["无幻觉内容"] = len(bad_hits) == 0

    action_words = ["建议", "可以", "申请", "提交", "收集", "保留", "联系", "向", "前往"]
    scores["可操作性"] = any(word in response for word in action_words)

    passed = sum(1 for ok in scores.values() if ok)
    total = len(scores)
    return {
        "scores": scores,
        "must_hit": must_hits,
        "must_miss": must_miss,
        "bad_hits": bad_hits,
        "passed": passed,
        "total": total,
        "score_pct": round(passed / total * 100),
        "overall_pass": scores["法律要素覆盖"] and scores["有效回答"] and scores["无幻觉内容"],
    }


def main() -> None:
    print("Legal Agent V3 - 回答质量评估")
    print(f"评估案例数: {len(QUALITY_CASES)}")
    print("=" * 60)

    token = login()
    if not token:
        print("[ERROR] 登录失败")
        return
    headers = {"Authorization": f"Bearer {token}"}

    all_results: list[dict[str, Any]] = []
    total_pass = 0

    for case in QUALITY_CASES:
        print(f"\n[{case.name}]")
        print(f"  意图: {case.description}")
        conv_id = new_conv(headers)
        turn_outputs: list[dict[str, Any]] = []

        for i, (text, force_flag) in enumerate(case.turns):
            data = ask(headers, conv_id, text, force_flag)
            turn_outputs.append(data)
            route = data.get("route_intent", "?")
            conf = float(data.get("confidence_score", 0) or 0)
            latency = data.get("latency_ms", 0)
            marker = " <-- 评估轮" if i == case.eval_turn else ""
            print(f"  Turn {i + 1}: route={route} conf={conf:.2f} latency={latency}ms{marker}")

        target = turn_outputs[case.eval_turn] if case.eval_turn < len(turn_outputs) else turn_outputs[-1]
        response = target.get("response", "")

        if "error" in target:
            print(f"  [ERROR] {target['error']}")
            result = {
                "case": case.name,
                "overall_pass": False,
                "error": target["error"],
                "score_pct": 0,
                "route": target.get("route_intent"),
                "response_len": len(response),
                "missing_forbidden": [],
            }
        else:
            eval_result = evaluate_response(response, case)
            result = {
                "case": case.name,
                **eval_result,
                "route": target.get("route_intent"),
                "response_len": len(response),
                "response_preview": response[:200],
            }
            icon = "[PASS]" if eval_result["overall_pass"] else "[FAIL]"
            print(f"  {icon} 质量评分: {eval_result['score_pct']}%")
            for dim, ok in eval_result["scores"].items():
                print(f"    {'OK' if ok else '--'} {dim}")
            if eval_result["must_miss"]:
                print(f"    缺失要素: {eval_result['must_miss']}")
            if eval_result["bad_hits"]:
                print(f"    幻觉内容: {eval_result['bad_hits']}")

        all_results.append(result)
        if result.get("overall_pass"):
            total_pass += 1

    print("\n" + "=" * 60)
    print(f"质量评估汇总: {total_pass}/{len(QUALITY_CASES)} PASS")
    print(f"整体通过率: {round(total_pass / len(QUALITY_CASES) * 100)}%")
    avg_score = sum(item.get("score_pct", 0) for item in all_results) / len(all_results)
    print(f"平均质量评分: {avg_score:.1f}%")
    print("\n各案例结果:")
    for item in all_results:
        icon = "[PASS]" if item.get("overall_pass") else "[FAIL]"
        print(f"  {icon} {item['case']} ({item.get('score_pct', 0)}%)")

    os.makedirs("e2e_tests/results", exist_ok=True)
    out = "e2e_tests/results/quality_eval.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total": len(QUALITY_CASES),
                "passed": total_pass,
                "pass_rate": round(total_pass / len(QUALITY_CASES) * 100),
                "avg_score_pct": round(avg_score, 1),
                "results": all_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    main()
