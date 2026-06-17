from pathlib import Path
import sys

AI_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(AI_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(AI_PYTHON_DIR))

# 直接运行 app/main.py 时，先进入统一启动入口，避免配置加载晚于路由初始化。
if __name__ == "__main__":
    from run import main as run_ai_service

    run_ai_service()
    raise SystemExit

from fastapi import FastAPI

from app.api.rag import router as rag_router


app = FastAPI(
    title="Multimodal RAG Agent Learning Evidence Platform - AI Service",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-python-rag"}


app.include_router(rag_router)
