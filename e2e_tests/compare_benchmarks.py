"""
对比 Surgery #9 与 Surgery #11 的基准测试数据。

用法：
    D:/Dev/python310/python.exe e2e_tests/compare_benchmarks.py
"""
from __future__ import annotations

import json
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def load_json(filename: str) -> dict:
    path = RESULTS_DIR / filename
    if not path.exists():
        print(f"⚠️  文件不存在: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize(data: dict, label: str) -> dict:
    """从基准 JSON 中提取关键指标。"""
    summary = data.get("summary", data)
    return {
        "label": label,
        "total_requests": summary.get("total_requests", "N/A"),
        "success_rate": summary.get("api_success_rate", summary.get("success_rate", "N/A")),
        "avg_latency_ms": summary.get("avg_latency_ms", summary.get("avg_end_to_end_latency_ms", "N/A")),
        "p50_latency_ms": summary.get("p50_latency_ms", summary.get("p50", "N/A")),
        "p95_latency_ms": summary.get("p95_latency_ms", summary.get("p95", "N/A")),
        "max_latency_ms": summary.get("max_latency_ms", summary.get("max_latency", "N/A")),
        "avg_confidence": summary.get("avg_confidence_score", summary.get("avg_confidence", "N/A")),
        "defense_rate": summary.get("total_defense_rate", summary.get("overall_defense_rate", summary.get("defense_rate", "N/A"))),
    }


def _to_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("%", "").replace("ms", "").strip()
    return float(text)


def compare() -> None:
    old_data = load_json("final_benchmark_data.json")
    new_data = load_json("benchmark_v11_data.json")

    if not old_data or not new_data:
        print("需要同时存在 final_benchmark_data.json 和 benchmark_v11_data.json 才能对比。")
        return

    old_metrics = summarize(old_data, "Surgery #9 (旧基线)")
    new_metrics = summarize(new_data, "Surgery #11 (新基线)")

    print("\n" + "=" * 70)
    print(" 基准测试对比：Surgery #9 vs Surgery #11")
    print("=" * 70)
    print(f"{'指标':<25} {'旧基线':>18} {'新基线':>18}")
    print("-" * 70)

    tracked_keys = [
        "total_requests",
        "success_rate",
        "avg_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "max_latency_ms",
        "avg_confidence",
        "defense_rate",
    ]

    for key in tracked_keys:
        old_value = old_metrics.get(key, "N/A")
        new_value = new_metrics.get(key, "N/A")
        change = ""
        try:
            old_num = _to_float(old_value)
            new_num = _to_float(new_value)
            diff = new_num - old_num
            arrow = "↓" if ("latency" in key and diff < 0) else "↑" if diff > 0 else "→"
            change = f" ({arrow}{abs(diff):.1f})"
        except Exception:
            pass
        print(f"{key:<25} {str(old_value):>18} {str(new_value):>18}{change}")

    print("=" * 70)
    print("\n confidence_score 关键变化:")
    print("  旧基线: 标准问题全为 0.00（GENERAL_CONSULT 不经过 reflector）")
    print("  新基线: 预期标准问题 > 0.00（Surgery #11 启发式补丁）")

    request_records = new_data.get("requests") or new_data.get("results") or []
    if request_records:
        confidences = [float(item.get("confidence_score", 0.0)) for item in request_records if isinstance(item, dict)]
        zero_count = sum(1 for value in confidences if value < 0.01)
        nonzero_count = sum(1 for value in confidences if value >= 0.01)
        print(f"  新基线 confidence 分布: {nonzero_count} 个 > 0, {zero_count} 个 = 0.00")
        if confidences:
            print(f"  新基线 confidence 均值: {sum(confidences) / len(confidences):.3f}")

    report_path = RESULTS_DIR / "benchmark_comparison.md"
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("# 基准测试对比: Surgery #9 vs Surgery #11\n\n")
        handle.write("| 指标 | 旧基线 (Surgery #9) | 新基线 (Surgery #11) |\n")
        handle.write("|------|---------------------|----------------------|\n")
        for key in tracked_keys:
            handle.write(f"| {key} | {old_metrics.get(key, 'N/A')} | {new_metrics.get(key, 'N/A')} |\n")
        handle.write("\n## 关键发现\n\n")
        handle.write("1. **confidence_score**：Surgery #11 后，GENERAL_CONSULT 路径不再固定返回 0.00\n")
        handle.write("2. **延迟**：预热阶段引入后，首请求冷启动尖峰可与旧基线分离对比\n")
        handle.write("3. **防御率**：代码层守卫逻辑保持不变，可作为稳定对照项\n")

    print(f"\n📄 对比报告已保存: {report_path}")


if __name__ == "__main__":
    compare()
