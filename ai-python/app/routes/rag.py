from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from rag.mineru_loader import MineruDocumentLoader
from rag.retrieval import create_rag_store
from schemas.rag import (
    IndexResponse,
    IndexTextRequest,
    OverviewResponse,
    QueryRequest,
    QueryResponse,
)


router = APIRouter(prefix="/internal/rag", tags=["RAG"])
loader = MineruDocumentLoader()
store = create_rag_store()


@router.post("/documents/index-text", response_model=IndexResponse)
def index_text(request: IndexTextRequest) -> IndexResponse:
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="content is empty")
    return store.index_text(request)


@router.post("/documents/index-file", response_model=IndexResponse)
async def index_file(
    file: UploadFile = File(...),
    document_id: str = Form(...),
    title: str = Form(...),
    document_type: str = Form("document"),
    source: str = Form("upload"),
    user_id: str = Form("demo-user"),
    visibility_scope: str = Form("private"),
) -> IndexResponse:
    content = await file.read()
    parsed = loader.load_bytes(
        content=content,
        filename=file.filename or title,
        content_type=file.content_type,
    )
    if not parsed.text.strip():
        raise HTTPException(status_code=400, detail="document text is empty")

    request = IndexTextRequest(
        documentId=document_id,
        title=title,
        documentType=document_type,
        source=source,
        userId=user_id,
        visibilityScope=visibility_scope,
        content=parsed.text,
        parser=parsed.parser,
    )
    return store.index_text(request)


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question is empty")
    response = store.query(request)
    if not response.evidences:
        response.answer = "当前知识库没有检索到足够相关的证据，请先上传或索引学习资料。"
    return response


@router.get("/overview", response_model=OverviewResponse)
def overview() -> OverviewResponse:
    return store.overview()
