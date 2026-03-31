# main.py
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dialog import router as dialog_router
from app.api.feedback import router as feedback_router
from app.api.history import router as history_router
from app.api.summary import router as summary_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="智能法律咨询系统 V3", version="3.0.0")

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


@app.get("/health")
async def health_check():
    return {"status": "online", "message": "API Gateway is running"}


@app.get("/knowledge/status")
async def kb_status():
    return {"status": "online", "loaded": True, "doc_count": 0}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
