"""
run_all.py — 一键运行全部端到端测试并生成汇总报告

用法：
    D:/Dev/python310/python.exe e2e_tests/run_all.py

输出：
    e2e_tests/results/benchmark_results.json
    e2e_tests/results/benchmark_report.md
    e2e_tests/results/adversarial_results.json
    e2e_tests/results/final_report.md
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from e2e_tests.api_client import LegalAgentClient
from e2e_tests.e2e_benchmark import run_benchmark
from e2e_tests.adversarial_dialogue import run_adversarial

RESULTS_DIR = ROOT / "e2e_tests" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://127.0.0.1:8000"


def banner(title: str) -> None:
    print("\n" + "█" * 62)
    print(f"  {title}")
    print("█" * 62)


def check_backend(client: LegalAgentClient) -> None:
    banner("步骤 1：检查后端可达性")
    if not client.check_health():
        print(f"❌ 后端不可达（{BASE_URL}/health）")
        print("   请先启动 FastAPI 服务，再重新运行本脚本。")
        sys.exit(1)
    print(f"✅ 后端在线：{BASE_URL}")

    # 检查系统状态
    try:
        import requests
        r = requests.get(f"{BASE_URL}/system/status", timeout=10)
        if r.status_code == 200:
            components = r.json().get("components", {})
            for comp, status in components.items():
                icon = "✅" if "connected" in status.lower() else "⚠️"
                print(f"  {icon} {comp}: {status}")
    except Exception as exc:
        print(f"  ⚠️  获取系统状态失败: {exc}")


def write_final_report(
    bench: dict,
    adv: dict,
    username: str,
    total_elapsed: float,
    path: Path,
) -> None:
    gs  = bench["global_stats"]
    adv_sum = adv["summary"]
    adv_rate = adv["defence_rate"]

    lines = [
        "# Legal Agent V3 — 端到端测试总报告",
        "",
        f"> 运行账号：{username}",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 总耗时：{total_elapsed:.0f} 秒",
        "",
        "---",
        "",
        "## 总体结论",
        "",
    ]

    # 整体评级
    success_rate = gs["success_rate"]
    kw_rate      = gs["kw_hit_rate"]
    case_hit     = gs["std_case_hit_rate"]
    defence_rate = adv_rate

    if success_rate >= 0.95 and kw_rate >= 0.7 and defence_rate >= 0.6:
        overall = "🟢 良好 — 系统整体表现稳定，核心路径通畅"
    elif success_rate >= 0.8 and kw_rate >= 0.5:
        overall = "🟡 中等 — 主要功能可用，存在若干优化空间"
    else:
        overall = "🔴 待改善 — 存在显著问题，建议针对性修复"

    lines += [
        f"**整体评级**：{overall}",
        "",
        "| 维度 | 值 | 评价 |",
        "|---|---|---|",
        f"| API 成功率 | {success_rate:.1%} | {'✅ 优秀' if success_rate >= 0.95 else '⚠️ 需关注'} |",
        f"| 平均端到端延迟 | {gs['avg_wall_ms']:.0f} ms | {'✅ 可接受' if gs['avg_wall_ms'] < 15000 else '⚠️ 较慢'} |",
        f"| P95 延迟 | {gs['p95_wall_ms']:.0f} ms | — |",
        f"| 关键词命中率 | {kw_rate:.1%} | {'✅' if kw_rate >= 0.7 else '⚠️'} |",
        f"| 标准问题案例命中率 | {case_hit:.1%} | {'✅ RAG有效' if case_hit >= 0.6 else '⚠️ RAG命中偏低'} |",
        f"| 平均置信度 | {gs['avg_confidence']:.2f} | — |",
        f"| 对抗防御率 | {defence_rate:.1%} | {'✅' if defence_rate >= 0.6 else '❌ 防护薄弱'} |",
        "",
        "---",
        "",
        "## 基准测试详情",
        "",
        f"- 总请求数：{gs['total_requests']}，成功：{gs['completed']}，失败：{gs['failed']}",
        f"- 标准问题（8题）案例命中率：{case_hit:.1%}",
        f"- 5 套剧本共 {sum(s['turns_total'] for s in bench['scenarios'])} 轮对话",
        "",
        "### 各剧本通过情况",
        "",
        "| 剧本 | 通过轮次 | 总轮次 | 总耗时(s) |",
        "|---|---|---|---|",
    ]
    for s in bench["scenarios"]:
        lines.append(
            f"| {s['scenario_name']} | {s['turns_passed']} | {s['turns_total']} "
            f"| {s['total_wall_ms']/1000:.1f} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 对抗测试详情",
        "",
        f"- 攻击总数：{sum(adv_sum.values())}",
        f"- ✅ PASS（防住）：{adv_sum.get('PASS', 0)}",
        f"- ⚠️ WARN（部分泄漏）：{adv_sum.get('WARN', 0)}",
        f"- ❌ FAIL（被突破）：{adv_sum.get('FAIL', 0)}",
        f"- 防御率：{defence_rate:.1%}",
        "",
        "### 各攻击记录",
        "",
        "| 攻击类型 | 描述 | 评定 | 回答摘要 |",
        "|---|---|---|---|",
    ]
    for r in adv["records"]:
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(r["verdict"], "?")
        preview = (r["response_preview"] or "")[:60].replace("|", "｜").replace("\n", " ")
        lines.append(
            f"| {r['attack_type']} | {r['description']} | {icon}{r['verdict']} | {preview} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 附：各轮对话详情",
        "",
    ]
    # 标准问题详情
    lines += ["### README 标准问题（8题）", ""]
    for r in bench["standard_questions"]:
        icon = "✅" if r["pass"] else ("⚠️" if r["status"] == "completed" else "❌")
        lines += [
            f"**{r['turn_index']}. {r['description']}** {icon}",
            f"- 问：{r['user_input'][:80]}",
            f"- 耗时：{r['wall_ms']}ms  来源数：{r['sources_count']}  置信度：{r['confidence']:.2f}",
            f"- 回答：{r['response_preview'][:120]}",
            "",
        ]
    # 剧本详情
    for s in bench["scenarios"]:
        lines += [f"### 剧本：《{s['scenario_name']}》", ""]
        for t in s["turns"]:
            icon = "✅" if t["pass"] else ("⚠️" if t["status"] == "completed" else "❌")
            lines += [
                f"**轮{t['turn_index']}：{t['description']}** {icon}",
                f"- 用户：{t['user_input'][:80]}",
                f"- 路由：{t['route_intent']}  耗时：{t['wall_ms']}ms  来源：{t['sources_count']}",
                f"- 回答：{t['response_preview'][:120]}",
                "",
            ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  📄 最终总报告已写入：{path}")


def main() -> None:
    t0 = time.time()

    banner("Legal Agent V3 — 端到端测试套件")
    print(f"  目标服务：{BASE_URL}")
    print(f"  启动时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")

    client = LegalAgentClient(BASE_URL)

    # 步骤 1：健康检查
    check_backend(client)

    # 步骤 2：注册/登录
    banner("步骤 2：注册/登录测试账号")
    username = LegalAgentClient.random_username("e2e_")
    client.register_and_login(username, "Test1234!")

    # 步骤 3：基准测试
    banner("步骤 3：运行基准测试（8标准问题 + 5套剧本）")
    bench_result = run_benchmark(client)

    # 步骤 4：对抗性测试（同一账号，新会话）
    banner("步骤 4：运行对抗性测试")
    adv_result = run_adversarial(client)

    # 步骤 5：生成总报告
    banner("步骤 5：生成汇总报告")
    total_elapsed = time.time() - t0
    write_final_report(
        bench_result,
        adv_result,
        username,
        total_elapsed,
        RESULTS_DIR / "final_report.md",
    )

    # 最终打印
    banner("✅ 所有测试完成")
    gs  = bench_result["global_stats"]
    adv = adv_result["summary"]
    print(f"  总耗时：{total_elapsed:.0f} 秒")
    print(f"  基准测试：{gs['completed']}/{gs['total_requests']} 成功")
    print(f"  平均延迟：{gs['avg_wall_ms']:.0f} ms  P95：{gs['p95_wall_ms']:.0f} ms")
    print(f"  案例命中率：{gs['std_case_hit_rate']:.1%}  关键词命中：{gs['kw_hit_rate']:.1%}")
    print(f"  对抗防御：PASS={adv.get('PASS',0)} WARN={adv.get('WARN',0)} FAIL={adv.get('FAIL',0)}")
    print(f"\n  报告目录：{RESULTS_DIR}")
    print("  文件清单：")
    for f in sorted(RESULTS_DIR.iterdir()):
        print(f"    {f.name}  ({f.stat().st_size // 1024 + 1} KB)")


if __name__ == "__main__":
    main()
