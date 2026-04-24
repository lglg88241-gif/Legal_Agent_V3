"""
e2e_benchmark.py — 性能基准测试

运行方式：
    D:/Dev/python310/python.exe e2e_tests/e2e_benchmark.py

输出：
    e2e_tests/results/benchmark_results.json
    e2e_tests/results/benchmark_report.md
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from e2e_tests.api_client import LegalAgentClient
from e2e_tests.dialogue_scenarios import ALL_SCENARIOS, README_STANDARD_QUESTIONS

RESULTS_DIR = ROOT / "e2e_tests" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

INTER_TURN_DELAY = 2   # 轮次间等待（秒），避免打爆 Celery


# ─── 关键词命中检查 ───────────────────────────────────────────────────

def check_keywords(response: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    return any(kw in response for kw in keywords)


# ─── 单轮结果记录 ────────────────────────────────────────────────────

def make_turn_record(
    turn_index: int,
    user_input: str,
    result: dict,
    expect_keywords: list[str],
    expect_route: str | None,
    check_sources: bool,
    description: str,
) -> dict:
    response = result.get("response", "")
    sources  = result.get("sources", []) or []
    status   = result.get("status", "failed")

    kw_hit   = check_keywords(response, expect_keywords) if status == "completed" else False
    route_ok = (
        expect_route is None
        or result.get("route_intent", "") == expect_route
    )
    src_ok   = (not check_sources) or (len(sources) > 0)

    return {
        "turn_index":    turn_index,
        "description":   description,
        "user_input":    user_input,
        "status":        status,
        "response_preview": response[:200] if response else "",
        "response_len":  len(response),
        "route_intent":  result.get("route_intent", ""),
        "confidence":    result.get("confidence_score", 0.0),
        "sources_count": len(sources),
        "latency_ms":    result.get("latency_ms", 0),
        "wall_ms":       result.get("wall_ms", 0),
        "kw_hit":        kw_hit,
        "route_ok":      route_ok,
        "sources_ok":    src_ok,
        "pass":          status == "completed" and kw_hit and src_ok,
    }


# ─── 运行8个标准问题 ─────────────────────────────────────────────────

def run_standard_questions(client: LegalAgentClient) -> list[dict]:
    print("\n" + "═" * 60)
    print("▶ 阶段 1：README 8 个标准问题（各独立会话）")
    print("═" * 60)
    records = []

    for i, q in enumerate(README_STANDARD_QUESTIONS, 1):
        print(f"\n  [{i}/8] {q['description']}")
        print(f"  问：{q['question'][:60]}…")
        conv_id = client.create_conversation(title=f"标准问题{i}-{q['description']}")
        result  = client.send_message(conv_id, q["question"])
        rec = make_turn_record(
            turn_index=i,
            user_input=q["question"],
            result=result,
            expect_keywords=q["expect_keywords"],
            expect_route=None,
            check_sources=True,
            description=q["description"],
        )
        records.append(rec)
        status_icon = "✅" if rec["pass"] else ("⚠️" if rec["status"] == "completed" else "❌")
        print(f"  {status_icon} 状态={rec['status']} wall={rec['wall_ms']}ms "
              f"sources={rec['sources_count']} kw={'✓' if rec['kw_hit'] else '✗'} "
              f"confidence={rec['confidence']:.2f}")
        if rec["response_preview"]:
            print(f"  回答预览：{rec['response_preview'][:100]}…")
        time.sleep(INTER_TURN_DELAY)

    passed = sum(1 for r in records if r["pass"])
    hit    = sum(1 for r in records if r["sources_count"] > 0)
    avg_w  = sum(r["wall_ms"] for r in records) / len(records) if records else 0
    print(f"\n  📊 标准问题汇总：通过 {passed}/8，案例命中 {hit}/8，平均耗时 {avg_w:.0f}ms")
    return records


# ─── 运行多轮对话剧本 ────────────────────────────────────────────────

def run_scenario(
    client: LegalAgentClient,
    scenario_name: str,
    turns: list[dict],
) -> dict:
    print(f"\n{'─'*60}")
    print(f"▶ 剧本：《{scenario_name}》（{len(turns)} 轮）")
    print("─" * 60)
    conv_id = client.create_conversation(title=f"剧本-{scenario_name}")
    turn_records = []

    for idx, turn in enumerate(turns, 1):
        user_input = turn["user_input"]
        print(f"\n  轮{idx}/{len(turns)} | {turn['description']}")
        print(f"  用户：{user_input[:60]}")
        result = client.send_message(conv_id, user_input)
        rec = make_turn_record(
            turn_index=idx,
            user_input=user_input,
            result=result,
            expect_keywords=turn.get("expect_keywords", []),
            expect_route=turn.get("expect_route"),
            check_sources=turn.get("check_sources", False),
            description=turn.get("description", ""),
        )
        turn_records.append(rec)
        icon = "✅" if rec["pass"] else ("⚠️" if rec["status"] == "completed" else "❌")
        print(f"  {icon} route={rec['route_intent']} wall={rec['wall_ms']}ms "
              f"src={rec['sources_count']} kw={'✓' if rec['kw_hit'] else '✗'}")
        if rec["response_preview"]:
            print(f"  回答：{rec['response_preview'][:120]}…")
        if idx < len(turns):
            time.sleep(INTER_TURN_DELAY)

    passed = sum(1 for r in turn_records if r["pass"])
    total_ms = sum(r["wall_ms"] for r in turn_records)
    print(f"\n  剧本结束：通过 {passed}/{len(turns)} 轮，总耗时 {total_ms/1000:.1f}s")
    return {
        "scenario_name": scenario_name,
        "conversation_id": conv_id,
        "turns": turn_records,
        "turns_passed": passed,
        "turns_total": len(turns),
        "total_wall_ms": total_ms,
    }


# ─── 全局统计 ────────────────────────────────────────────────────────

def compute_global_stats(std_records: list[dict], scenario_results: list[dict]) -> dict:
    all_turns = std_records + [t for s in scenario_results for t in s["turns"]]
    completed = [r for r in all_turns if r["status"] == "completed"]
    walls     = sorted([r["wall_ms"] for r in completed])
    n = len(walls)
    p95 = walls[int(n * 0.95)] if n > 0 else 0

    return {
        "total_requests":    len(all_turns),
        "completed":         len(completed),
        "failed":            len(all_turns) - len(completed),
        "success_rate":      len(completed) / len(all_turns) if all_turns else 0,
        "avg_wall_ms":       sum(walls) / n if n else 0,
        "max_wall_ms":       max(walls) if walls else 0,
        "p95_wall_ms":       p95,
        "avg_confidence":    sum(r["confidence"] for r in completed) / len(completed) if completed else 0,
        "kw_hit_rate":       sum(1 for r in completed if r["kw_hit"]) / len(completed) if completed else 0,
        "sources_hit_rate":  sum(1 for r in all_turns if r["sources_count"] > 0) / len(all_turns) if all_turns else 0,
        "std_case_hit_rate": sum(1 for r in std_records if r["sources_count"] > 0) / len(std_records) if std_records else 0,
    }


# ─── Markdown 报告 ───────────────────────────────────────────────────

def write_markdown_report(
    std_records: list[dict],
    scenario_results: list[dict],
    global_stats: dict,
    path: Path,
) -> None:
    lines = [
        "# Legal Agent V3 — 端到端基准测试报告",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 全局统计",
        "",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 总请求数 | {global_stats['total_requests']} |",
        f"| 成功率 | {global_stats['success_rate']:.1%} |",
        f"| 平均端到端延迟 | {global_stats['avg_wall_ms']:.0f} ms |",
        f"| P95 延迟 | {global_stats['p95_wall_ms']:.0f} ms |",
        f"| 最大延迟 | {global_stats['max_wall_ms']:.0f} ms |",
        f"| 平均置信度 | {global_stats['avg_confidence']:.2f} |",
        f"| 关键词命中率 | {global_stats['kw_hit_rate']:.1%} |",
        f"| 标准问题案例命中率 | {global_stats['std_case_hit_rate']:.1%} |",
        "",
        "## 一、README 8 个标准问题",
        "",
        "| # | 描述 | 状态 | 耗时(ms) | 来源数 | 置信度 | 关键词 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in std_records:
        icon = "✅" if r["pass"] else ("⚠️" if r["status"] == "completed" else "❌")
        lines.append(
            f"| {r['turn_index']} | {r['description']} | {icon}{r['status']} "
            f"| {r['wall_ms']} | {r['sources_count']} | {r['confidence']:.2f} "
            f"| {'✓' if r['kw_hit'] else '✗'} |"
        )
    lines += [""]

    for s in scenario_results:
        lines += [
            f"## 二、剧本：《{s['scenario_name']}》",
            "",
            f"通过 {s['turns_passed']}/{s['turns_total']} 轮，总耗时 {s['total_wall_ms']/1000:.1f}s",
            "",
            "| 轮次 | 描述 | 状态 | 耗时(ms) | 路由 | 来源 | KW | 回答预览 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for t in s["turns"]:
            icon = "✅" if t["pass"] else ("⚠️" if t["status"] == "completed" else "❌")
            preview = (t["response_preview"] or "")[:60].replace("|", "｜").replace("\n", " ")
            lines.append(
                f"| {t['turn_index']} | {t['description']} | {icon} "
                f"| {t['wall_ms']} | {t['route_intent']} | {t['sources_count']} "
                f"| {'✓' if t['kw_hit'] else '✗'} | {preview} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  📄 Markdown 报告已写入：{path}")


# ─── 主入口 ──────────────────────────────────────────────────────────

def run_benchmark(client: LegalAgentClient) -> dict:
    std_records      = run_standard_questions(client)
    scenario_results = []
    for name, turns in ALL_SCENARIOS:
        sr = run_scenario(client, name, turns)
        scenario_results.append(sr)

    global_stats = compute_global_stats(std_records, scenario_results)

    result_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "global_stats":      global_stats,
        "standard_questions": std_records,
        "scenarios":         scenario_results,
    }

    json_path = RESULTS_DIR / "benchmark_results.json"
    json_path.write_text(
        json.dumps(result_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  💾 JSON 结果已写入：{json_path}")

    write_markdown_report(
        std_records, scenario_results, global_stats,
        RESULTS_DIR / "benchmark_report.md",
    )

    print("\n" + "═" * 60)
    print("📊 基准测试完成 — 全局汇总")
    print("═" * 60)
    for k, v in global_stats.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.2f}" if v < 1.1 else f"  {k}: {v:.0f}")
        else:
            print(f"  {k}: {v}")

    return result_payload


if __name__ == "__main__":
    client = LegalAgentClient()
    if not client.check_health():
        print("❌ 后端不可达（http://127.0.0.1:8000/health），请先启动服务。")
        sys.exit(1)

    username = LegalAgentClient.random_username("bench_")
    client.register_and_login(username, "Test1234!")
    run_benchmark(client)
