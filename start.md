
# 启动指南

## 0. 首次运行 / 内存崩溃后的 Redis 防护配置

打开 Redis CLI（或在 WSL 中执行），关闭 RDB 持久化：
```
redis-cli CONFIG SET save ""
redis-cli CONFIG SET stop-writes-on-bgsave-error no
```

这两条命令解决"内存不足 → bgsave 失败 → MISCONF 锁死写入 → Celery 连环崩溃"的根因。
如果需要永久生效，在 redis.conf 中添加：
```
save ""
stop-writes-on-bgsave-error no
```

---

## 1. 启动 FastAPI Web 终端

```powershell
# PowerShell → WSL
wsl
cd /path/to/Legal_Agent_V3
python -m uvicorn main:app --reload
```

或直接在 Windows PowerShell：
```powershell
cd .\Legal_Agent_V3\
.venv\Scripts\activate
python main.py
```

---

## 2. 启动 VLLM 推理服务（可选，仅 LLM_MODE=real 时需要）

```bash
cd ~/my_vllm_server
source venv/bin/activate
python -m vllm.entrypoints.openai.api_server \
  --model ~/.cache/huggingface/hub/models--Qwen--Qwen1.5-1.8B-Chat-AWQ/snapshots/eb196b26ac14d8bcf4144f9a3ab77b10ae77fe8f \
  --served-model-name Qwen/Qwen1.5-1.8B-Chat-AWQ \
  --quantization awq \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.8 \
  --max-num-seqs 128 \
  --port 8001 \
  --host 0.0.0.0
```

---

## 3. 启动 Celery Worker

**重要**：`--concurrency 2` 限制并发，防止多任务同时加载 ML 模型导致内存溢出。

```powershell
cd .\Legal_Agent_V3\
.venv\Scripts\activate
python -m celery -A app.celery_app.celery_app worker --loglevel=info -P threads --concurrency 2
```

> 如果仍然报内存不足，可进一步降低为 `--concurrency 1`。

---

## 4. 启动前端

```powershell
cd .\frontend\
npm run dev
```

---

## 内存占用参考（Windows 本地环境）

| 组件 | 峰值内存占用 |
|------|-------------|
| HuggingFace Embedding 模型 (BGE-small-zh) | ~500 MB |
| BGE Reranker CrossEncoder (bge-reranker-v2-m3) | ~800 MB |
| FastAPI + uvicorn | ~200 MB |
| Celery Worker × 2 并发（不含模型） | ~300 MB |
| Redis | ~50 MB |
| **合计估算** | **~1.85 GB** |

建议系统可用内存 ≥ 8 GB，运行时保留 ≥ 4 GB 空闲内存。

