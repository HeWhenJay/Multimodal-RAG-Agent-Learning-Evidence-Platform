from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from rag.document_parsers import DocumentParserRouter
from rag.mineru_loader import MineruDocumentLoader
from rag.retrieval import create_rag_store
from schemas.rag import (
    EvidenceListResponse,
    IndexResponse,
    IndexTextRequest,
    OverviewResponse,
    QueryRequest,
    QueryResponse,
)


router = APIRouter(prefix="/internal/rag", tags=["RAG"])
loader = MineruDocumentLoader()
parser_router = DocumentParserRouter(loader)
store = create_rag_store()


@router.post("/documents/index-text", response_model=IndexResponse)
def index_text(request: IndexTextRequest) -> IndexResponse:
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="content is empty")
    parsed = parser_router.parse_text(
        document_id=request.documentId,
        title=request.title,
        document_type=request.documentType,
        source_path=request.sourcePath,
        content=request.content,
        parser=request.parser,
    )
    return store.index_blocks(
        document_id=request.documentId,
        title=request.title,
        document_type=request.documentType,
        source=request.source,
        user_id=request.userId,
        visibility_scope=request.visibilityScope,
        language=request.language,
        parser=parsed.parser,
        blocks=parsed.blocks,
        parse_quality=parsed.parse_quality,
        status=parsed.status,
        source_path=request.sourcePath,
    )


@router.post("/documents/index-file", response_model=IndexResponse)
async def index_file(
    file: UploadFile = File(...),
    document_id: str = Form(...),
    title: str = Form(...),
    document_type: str = Form("document"),
    source: str = Form("upload"),
    user_id: str = Form("demo-user"),
    visibility_scope: str = Form("private"),
    source_path: str | None = Form(None),
    high_precision: bool = Form(False),
) -> IndexResponse:
    content = await file.read()
    parsed = parser_router.parse_bytes(
        content=content,
        filename=file.filename or title,
        document_id=document_id,
        source_title=title,
        document_type=document_type,
        content_type=file.content_type,
        source_path=source_path,
        high_precision=high_precision,
    )
    return store.index_blocks(
        document_id=document_id,
        title=title,
        document_type=document_type,
        source=source,
        user_id=user_id,
        visibility_scope=visibility_scope,
        language="zh-CN",
        parser=parsed.parser,
        blocks=parsed.blocks,
        parse_quality=parsed.parse_quality,
        status=parsed.status,
        source_path=source_path,
    )


@router.get("/documents/{document_id}/evidences", response_model=EvidenceListResponse)
def list_document_evidences(document_id: str, limit: int = 20) -> EvidenceListResponse:
    safe_limit = min(max(limit, 1), 100)
    return EvidenceListResponse(documentId=document_id, evidences=store.list_evidences(document_id, safe_limit))


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
