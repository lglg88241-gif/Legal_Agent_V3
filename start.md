启动 Celery 打工人终端
切换到正在跑 Celery 的那个终端窗口。
敲入点火命令（注意观察它启动时加载 BGE 模型的日志）：

PowerShell
celery -A celery_app worker --loglevel=info --pool=solo

启动 FastAPI Web 终端
敲入点火命令：
PowerShell
python -m uvicorn main_app:app --reload
启动 VLLM 终端
cd ~/my_vllm_server
source venv/bin/activate
python -m vllm.entrypoints.openai.api_server \
  --model ~/.cache/huggingface/hub/models--Qwen--Qwen1.5-1.8B-Chat-AWQ/snapshots/eb196b26ac14d8bcf4144f9a3ab77b10ae77fe8f \
  --served-model-name Qwen/Qwen1.5-1.8B-Chat-AWQ \
  --quantization awq \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.8 \
  --max-num-seqs 128 \
  --port 8000 \
  --host 0.0.0.0


### 📋 使用方式 方式 1：Mock 模式（默认）
```
# .env 文件
LLM_MODE=mock

# 运行测试
python day_test_learning/
day16_system_eval.py
```
预期日志输出 ：

```
⚙️ LLM 服务初始化完成，当前模式: mock
⚙️ 当前 LLM 模式: mock - 拦截并返回 
Mock 向量
⚙️ 当前 LLM 模式: mock - 拦截并返回 
Mock 响应
🎯 Mock: 返回意图识别结果 -> 
vector_researcher
``` 方式 2：Real 模式
```
# .env 文件
LLM_MODE=real

# 运行测试
python day_test_learning/
day16_system_eval.py
```
预期日志输出 ：

```
⚙️ LLM 服务初始化完成，当前模式: real
🧬 正在生成文本向量...
🧬 正在生成文本向量...
🎯 Mock: 返回法律顾问 Markdown 响应
```