# 重跑端到端基准测试指南

## 前提

- 后端服务已启动：`http://127.0.0.1:8000`
- Redis 已运行
- Celery worker 已启动
- 已完成 Surgery #11 代码更新

## 步骤

### 1. 预热服务

```bash
D:/Dev/python310/python.exe scripts/warmup.py --base-url http://127.0.0.1:8000
```

等待输出“所有 lazy-init 组件已进入热态”后再继续。

### 2. 运行基准测试

```bash
D:/Dev/python310/python.exe e2e_tests/final_benchmark.py
```

结果将输出到：

- `e2e_tests/results/benchmark_v11_data.json`
- `e2e_tests/results/benchmark_v11_report.md`

### 3. 对比新旧数据

```bash
D:/Dev/python310/python.exe e2e_tests/compare_benchmarks.py
```

将生成：

- `e2e_tests/results/benchmark_comparison.md`

### 4. 预期变化

- `confidence_score`：标准问题从 `0.00` 提升到 `0.35 ~ 0.78`
- `max_latency`：冷启动尖峰应与旧基线分离，预期显著下降
- `P95 latency`：在预热后，预期优于旧基线
- 防御率：代码层守卫逻辑不变，预期与旧基线接近

## 说明

- 如果 `compare_benchmarks.py` 提示缺少旧基线，请确认 `e2e_tests/results/final_benchmark_data.json` 存在。
- 如果需要保留多轮实验结果，建议先备份 `e2e_tests/results/` 下的旧 JSON/Markdown，再执行新一轮测试。
