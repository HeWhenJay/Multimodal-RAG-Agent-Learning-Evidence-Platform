from fastapi import FastAPI

from app.routes.rag import router as rag_router


app = FastAPI(
    title="Multimodal RAG Agent Learning Evidence Platform - AI Service",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-python-rag"}


app.include_router(rag_router)

