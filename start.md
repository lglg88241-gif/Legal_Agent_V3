
启动 FastAPI Web 终端
敲入点火命令：
PowerShell
wsl
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

cd .\Legal_Agent_V3\

.venv\Scripts\activate

python main.py

python -m celery -A app.celery_app.celery_app worker --loglevel=info -P threads

cd .\frontend\

npm run dev

