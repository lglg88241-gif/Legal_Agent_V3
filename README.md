# Legal Agent V3

Legal Agent V3 是一个面向法律咨询场景的智能问答工作台。系统使用 FastAPI 提供后端 API，Celery + Redis 执行异步智能体任务，Vue 3 + Element Plus 提供前端工作台，MySQL 保存用户、会话和案卷数据，ChromaDB 保存法律法规与案例向量库，Neo4j 保存法条关系和案情图谱。

系统支持普通法律问答、RAG 法律依据检索、案情结构化、案卷管理、主体确认、图谱展示、金额计算和法条幻觉守卫。它的定位是法律信息检索与案情整理辅助工具，不替代律师意见或司法裁判。

## 目录

- [系统架构](#系统架构)
- [环境要求](#环境要求)
- [配置文件](#配置文件)
- [首次部署](#首次部署)
- [启动服务](#启动服务)
- [数据入库](#数据入库)
- [访问与健康检查](#访问与健康检查)
- [测试与回归](#测试与回归)
- [常见问题](#常见问题)

## 系统架构

```mermaid
flowchart LR
    UI[Vue 前端工作台] --> API[FastAPI API]
    API --> DB[(MySQL)]
    API --> REDIS[(Redis)]
    API --> CELERY[Celery Worker]
    CELERY --> AGENT[多 Agent 编排]
    AGENT --> RAG[RAG 检索]
    RAG --> CHROMA[(ChromaDB)]
    RAG --> NEO[(Neo4j)]
    AGENT --> LLM[大模型 API / 本地 vLLM]
    AGENT --> GUARD[安全守卫]
    GUARD --> API
```

主要模块：

- `main.py`：FastAPI 应用入口，提供健康检查、系统状态和业务路由。
- `app/api/`：登录注册、对话、会话、案卷、图谱和反馈 API。
- `app/celery_app.py`：Celery 任务入口，执行对话智能体、案情更新和图谱刷新。
- `app/services/`：法律问答、案卷、图谱、主体确认、守卫和持久化逻辑。
- `ingest_laws.py`：法律法规 DOCX 入库到 Chroma。
- `ingest_cases.py`：案例 PDF 入库到 Chroma，并写入部分 Neo4j 案例关系。
- `frontend/`：Vue 3 前端项目。

## 环境要求

推荐在 Windows PowerShell 中运行，本项目当前路径示例为：

```powershell
cd C:\Users\123\Legal_Agent_V3
```

基础依赖：

- Python 3.10 或 3.11
- Node.js `20.19.0` 或 `22.12.0` 以上，Vite 7 对 Node 版本有要求
- MySQL 8.x
- Redis 6.x 或 7.x
- Neo4j 5.x
- 可访问的大模型 API，或可选本地 vLLM 服务

默认端口：

| 服务 | 默认地址 |
|---|---|
| FastAPI | `http://127.0.0.1:8000` |
| Vue/Vite | `http://127.0.0.1:3000` |
| Redis | `127.0.0.1:6379` |
| MySQL | `127.0.0.1:3306` |
| Neo4j Bolt | `bolt://localhost:7687` |
| 本地 vLLM，可选 | `http://127.0.0.1:8001` |

## 配置文件

项目运行读取根目录 `.env`。先复制模板：

```powershell
Copy-Item .env.example .env
```

建议至少检查这些配置：

```env
# LLM
LLM_MODE=real
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
ALT_GPT_API_KEY=
ALT_GPT_BASE_URL=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=
ZHIPUAI_API_KEY=your-zhipu-key-here

# Optional local vLLM
API_KEY=EMPTY
BASE_URL=http://127.0.0.1:8001/v1
VLLM_BASE_URL=http://127.0.0.1:8001
LLM_MODEL_NAME=Qwen/Qwen2.5-3B-Instruct
LOCAL_LLM_ENABLED=false

# Databases
MYSQL_URL=mysql+aiomysql://root:your-password@127.0.0.1:3306/legal_db
DATABASE_URL=mysql+aiomysql://root:your-password@127.0.0.1:3306/legal_db
REDIS_URL=redis://127.0.0.1:6379/0
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-neo4j-password

# Chroma / models
CHROMA_PERSIST_DIR=./chroma_data
CHROMA_COLLECTION_NAME=legal_knowledge_base
CHROMA_CASES_COLLECTION=legal_cases_collection
EMBEDDING_MODEL_PATH=BAAI/bge-small-zh-v1.5
RERANKER_MODEL_PATH=BAAI/bge-reranker-v2-m3

# Auth
JWT_SECRET_KEY=change-this-secret-in-production
JWT_EXPIRE_MINUTES=1440
```

说明：

- `.env` 只用于本地运行，不要提交到仓库。
- 如果只想跑前后端流程，可先使用云端模型 API，不必启动本地 vLLM。
- `LLM_MODE=real` 会检查主要模型通道配置；如果仍使用默认 `mock`，部分问答能力不是完整真实链路。
- 首次加载 HuggingFace embedding/reranker 时会下载模型，网络慢时可以把模型预下载到本地目录，再把 `EMBEDDING_MODEL_PATH`、`RERANKER_MODEL_PATH` 指向本地路径。

## 首次部署

### 1. 创建 Python 环境

使用已有 `.venv` 时可以直接激活；如果是新机器，先创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果你习惯使用 `uv`：

```powershell
uv pip install -r requirements.txt
```

### 2. 安装前端依赖

```powershell
cd frontend
npm install
cd ..
```

### 3. 准备 MySQL

登录 MySQL 后创建数据库：

```sql
CREATE DATABASE legal_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

然后确认 `.env` 中的 `MYSQL_URL` / `DATABASE_URL` 指向这个库。业务表会在注册、对话、案卷等接口首次访问时自动创建。

### 4. 准备 Redis

启动 Redis 后建议关闭 RDB 持久化保护，避免内存不足导致 `MISCONF` 写入失败影响 Celery：

```powershell
redis-cli CONFIG SET save ""
redis-cli CONFIG SET stop-writes-on-bgsave-error no
```

如需永久生效，在 `redis.conf` 中加入：

```text
save ""
stop-writes-on-bgsave-error no
```

### 5. 准备 Neo4j

启动 Neo4j，确认用户名和密码与 `.env` 一致。可以用 Neo4j Browser 或命令行验证连接：

```cypher
RETURN 1;
```

### 6. 准备知识库数据

项目默认从 `data/corpus` 读取法律法规 `.docx` 和案例 `.pdf`。如果 `chroma_data` 已存在且可用，可以跳过首次入库；如果是新环境或需要重建知识库，执行：

```powershell
python ingest_laws.py
python ingest_cases.py
```

如果需要构建法条引用关系图谱，再执行：

```powershell
python scripts\build_article_reference_graph.py
```

注意：入库会加载 embedding 模型，首次运行较慢，也可能需要较多内存。

## 启动服务

开发环境建议打开 3 个 PowerShell 终端。

### 终端 1：启动 FastAPI

```powershell
cd C:\Users\123\Legal_Agent_V3
.\.venv\Scripts\Activate.ps1
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

也可以直接运行：

```powershell
python main.py
```

### 终端 2：启动 Celery Worker

```powershell
cd C:\Users\123\Legal_Agent_V3
.\.venv\Scripts\Activate.ps1
python -m celery -A app.celery_app.celery_app worker --loglevel=info -P threads --concurrency 2
```

内存紧张时把并发降到 1：

```powershell
python -m celery -A app.celery_app.celery_app worker --loglevel=info -P threads --concurrency 1
```

开发时如果希望 Python 文件修改后自动重启 Celery：

```powershell
.\.venv\Scripts\python.exe .\scripts\dev_celery_worker.py
```

### 终端 3：启动前端

```powershell
cd C:\Users\123\Legal_Agent_V3\frontend
npm run dev
```

前端默认访问 `http://127.0.0.1:8000`。如果后端地址不同，启动前设置：

```powershell
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm run dev
```

### 可选：启动本地 vLLM

只有需要本地模型加速时才启动。示例：

```powershell
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-3B-Instruct --port 8001 --host 0.0.0.0
```

同时在 `.env` 中确认：

```env
LOCAL_LLM_ENABLED=true
VLLM_BASE_URL=http://127.0.0.1:8001
BASE_URL=http://127.0.0.1:8001/v1
```

## 数据入库

常见数据初始化顺序：

```powershell
python ingest_laws.py
python ingest_cases.py
python scripts\build_article_reference_graph.py
```

各脚本作用：

| 脚本 | 输入 | 输出 |
|---|---|---|
| `ingest_laws.py` | `data/corpus/*.docx` | 法律法规向量集合 `legal_knowledge_base` |
| `ingest_cases.py` | `data/corpus/*.pdf` | 案例向量集合 `legal_cases_collection`，并写入部分案例图谱数据 |
| `scripts/build_article_reference_graph.py` | Chroma 法条集合 | Neo4j 法条引用关系 |

重建 Chroma 前请确认没有服务正在访问 `chroma_data`。如果目录损坏，可停止 FastAPI/Celery 后删除 `chroma_data`，再重新执行入库脚本。

## 访问与健康检查

启动后访问：

- 前端：`http://127.0.0.1:3000`
- 后端接口文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`
- 知识库状态：`http://127.0.0.1:8000/knowledge/status`
- 系统状态：`http://127.0.0.1:8000/system/status`

PowerShell 快速检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/system/status
```

`/system/status` 中各组件含义：

| 字段 | 正常值 | 说明 |
|---|---|---|
| `components.chroma` | `connected` | Chroma 向量库可读 |
| `components.neo4j` | `connected` | Neo4j 可连接 |
| `components.llm` | `connected` | LLM 配置检查通过 |
| `components.celery` | `connected` | Redis broker 可连接 |

第一次使用前端时先注册账号。注册接口会自动创建用户和默认会话。

## 生产部署建议

当前命令主要面向本地开发和毕业设计演示。若部署到服务器，建议：

- 使用 Nginx 或网关代理前端静态文件和 FastAPI。
- FastAPI 使用 `uvicorn` 或 `gunicorn + uvicorn worker` 常驻运行。
- Celery Worker 使用进程管理器托管，例如 systemd、Supervisor 或 Docker Compose。
- Redis、MySQL、Neo4j 使用独立持久化卷，并配置备份策略。
- `.env` 中必须更换 `JWT_SECRET_KEY`，并限制数据库、Neo4j、Redis 的外网访问。
- 前端执行 `npm run build` 后部署 `frontend/dist`。
- 不要把 `.env`、`chroma_data`、数据库备份和运行日志提交到 Git。

前端生产构建：

```powershell
cd frontend
npm run build
npm run preview
```

## 测试与回归

后端测试：

```powershell
python -m pytest tests -q
python -m compileall -q app tests
```

前端构建检查：

```powershell
cd frontend
npm run build
cd ..
```

端到端 benchmark：

```powershell
python e2e_tests\run_benchmarks.py --profile quick --check-readiness --suite-timeout 360
python e2e_tests\run_benchmarks.py --profile full --check-readiness --suite-timeout 420
```

常用专项回归：

```powershell
python e2e_tests\run_benchmarks.py --only legal_qa --check-readiness --suite-timeout 360
python e2e_tests\run_benchmarks.py --only workspace_participant --check-readiness --suite-timeout 420
python e2e_tests\run_benchmarks.py --only workspace_archive --check-readiness --suite-timeout 360
python e2e_tests\run_benchmarks.py --only workspace_delete --check-readiness --suite-timeout 360
```

## 常见问题

### 1. 前端能打开，但发送消息失败

检查后端和 Celery 是否都已启动：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/system/status
```

如果 `celery` 不是 `connected`，通常是 Redis 未启动或 `REDIS_URL` 配置错误。

### 2. 系统状态里 Chroma 一直是 loading

常见原因：

- 没有执行 `python ingest_laws.py` / `python ingest_cases.py`。
- `CHROMA_PERSIST_DIR` 指向了错误目录。
- embedding 模型下载失败。

可以先确认 `chroma_data` 是否存在，再查看 FastAPI 日志中的 Chroma warm-up 错误。

### 3. Neo4j 一直是 loading

检查：

- Neo4j 服务是否启动。
- `.env` 中 `NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD` 是否正确。
- Neo4j 初始密码是否已经修改完成。

### 4. MySQL 连接失败

检查：

- `legal_db` 数据库是否已创建。
- `MYSQL_URL` / `DATABASE_URL` 的用户名、密码、端口是否正确。
- MySQL 用户是否允许从本机连接。

### 5. Redis 报 MISCONF

执行：

```powershell
redis-cli CONFIG SET save ""
redis-cli CONFIG SET stop-writes-on-bgsave-error no
```

如果仍然失败，检查磁盘空间和 Redis 数据目录权限。

### 6. Celery 内存占用过高

把并发从 2 降到 1：

```powershell
python -m celery -A app.celery_app.celery_app worker --loglevel=info -P threads --concurrency 1
```

同时避免在 FastAPI 和 Celery 中同时触发大量模型加载。

### 7. 大模型不可用

检查 `.env`：

- `LLM_MODE=real`
- 至少配置一个可用的大模型通道，例如 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 或 `ZHIPUAI_API_KEY`。
- 如果启用本地 vLLM，确认 `VLLM_BASE_URL` 可访问。

## 相关文档

- [个性化工作台进展](docs/personalized_workspace_progress.md)
- [答辩提纲](docs/defense_outline.md)
- [智能体编排架构](docs/agent_orchestration_architecture.md)
- [延迟分析](docs/latency_analysis.md)
- [案卷图谱工具设计](docs/dossier_graph_tool_design.md)
