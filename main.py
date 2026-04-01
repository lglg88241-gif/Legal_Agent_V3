import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from neo4j import GraphDatabase
from openai import AsyncOpenAI
from redis import Redis

from app.api.dialog import router as dialog_router
from app.api.feedback import router as feedback_router
from app.api.graph import router as graph_router
from app.api.history import router as history_router
from app.api.summary import router as summary_router
from app.agent.tools import AGENT_TOOLS
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def build_initial_system_status() -> dict:
    return {
        "status": "loading",
        "ready": False,
        "components": {
            "chroma": "loading",
            "neo4j": "loading",
            "llm": "loading",
            "celery": "loading",
        },
        "knowledge": {
            "law_doc_count": 0,
            "case_doc_count": 0,
            "total_doc_count": 0,
        },
        "model": settings.LLM_MODEL_NAME,
        "tools": [tool.name for tool in AGENT_TOOLS],
    }


def refresh_system_status_flags(system_status: dict) -> None:
    components = system_status["components"]
    ready = all(value == "connected" for value in components.values())
    system_status["ready"] = ready
    system_status["status"] = "online" if ready else "loading"


def sync_warmup_chroma() -> dict:
    from app.database.chroma_mgr import chroma_mgr

    law_doc_count = chroma_mgr.law_vectorstore._collection.count()
    case_doc_count = chroma_mgr.case_vectorstore._collection.count()
    return {
        "law_doc_count": law_doc_count,
        "case_doc_count": case_doc_count,
        "total_doc_count": law_doc_count + case_doc_count,
    }


def sync_warmup_neo4j() -> None:
    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    try:
        driver.verify_connectivity()
    finally:
        driver.close()


def sync_warmup_celery() -> None:
    client = Redis.from_url(settings.REDIS_URL)
    try:
        client.ping()
    finally:
        client.close()


async def warmup_local_llm() -> None:
    client = AsyncOpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL)
    try:
        await client.chat.completions.create(
            model=settings.LLM_MODEL_NAME,
            messages=[
                {"role": "system", "content": "Warm up the model."},
                {"role": "user", "content": "ping"},
            ],
            temperature=0,
            max_tokens=1,
        )
    finally:
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            maybe_awaitable = close_fn()
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable


async def run_system_warmup(app: FastAPI) -> None:
    system_status = app.state.system_status

    async def warmup_chroma_component() -> None:
        try:
            counts = await asyncio.to_thread(sync_warmup_chroma)
            system_status["components"]["chroma"] = "connected"
            system_status["knowledge"] = counts
        except Exception as exc:
            logger.warning("Chroma warm-up failed: %s", exc)
        finally:
            refresh_system_status_flags(system_status)

    async def warmup_neo4j_component() -> None:
        try:
            await asyncio.to_thread(sync_warmup_neo4j)
            system_status["components"]["neo4j"] = "connected"
        except Exception as exc:
            logger.warning("Neo4j warm-up failed: %s", exc)
        finally:
            refresh_system_status_flags(system_status)

    async def warmup_llm_component() -> None:
        try:
            await warmup_local_llm()
            system_status["components"]["llm"] = "connected"
        except Exception as exc:
            logger.warning("Local LLM warm-up failed: %s", exc)
        finally:
            refresh_system_status_flags(system_status)

    async def warmup_celery_component() -> None:
        try:
            await asyncio.to_thread(sync_warmup_celery)
            system_status["components"]["celery"] = "connected"
        except Exception as exc:
            logger.warning("Celery broker warm-up failed: %s", exc)
        finally:
            refresh_system_status_flags(system_status)

    await asyncio.gather(
        warmup_chroma_component(),
        warmup_neo4j_component(),
        warmup_llm_component(),
        warmup_celery_component(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.system_status = build_initial_system_status()
    warmup_task = asyncio.create_task(run_system_warmup(app))
    try:
        yield
    finally:
        if not warmup_task.done():
            warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warmup_task


app = FastAPI(title="智能法律咨询系统 V3", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dialog_router)
app.include_router(history_router)
app.include_router(summary_router)
app.include_router(feedback_router)
app.include_router(graph_router)


@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "message": "API Gateway is running",
        "model": settings.LLM_MODEL_NAME,
        "tools": [tool.name for tool in AGENT_TOOLS],
    }


@app.get("/knowledge/status")
async def kb_status():
    knowledge = app.state.system_status["knowledge"]
    chroma_ready = app.state.system_status["components"]["chroma"] == "connected"
    return {
        "status": "online" if chroma_ready else "loading",
        "loaded": chroma_ready,
        "doc_count": knowledge["law_doc_count"],
        "case_doc_count": knowledge["case_doc_count"],
        "total_doc_count": knowledge["total_doc_count"],
    }


@app.get("/system/status")
async def system_status():
    return app.state.system_status


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
