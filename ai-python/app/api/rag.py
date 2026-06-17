from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from rag.loaders.document_parsers import DocumentParserRouter
from rag.loaders.mineru_loader import MineruDocumentLoader
from rag.process_logger import RagProcessLogger, logged_rag_method, process_event, use_process_logger
from rag.progress import RagProgressReporter
from rag.retrievers.retrieval import create_rag_store
from app.schemas.rag import (
    EvidenceListResponse,
    JdAnalyzeRequest,
    JdAnalyzeResponse,
    JdLearningPlanResult,
    JdSkillResult,
    IndexResponse,
    IndexTextRequest,
    IndexVideoSourceRequest,
    OverviewResponse,
    QueryRequest,
    QueryResponse,
    ResumeAlignmentResult,
)

import re


router = APIRouter(prefix="/internal/rag", tags=["RAG"])
loader = MineruDocumentLoader()
parser_router = DocumentParserRouter(loader)
store = create_rag_store()


@router.post("/documents/index-text", response_model=IndexResponse)
def index_text(request: IndexTextRequest) -> IndexResponse:
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="content is empty")
    process_logger = RagProcessLogger(document_id=request.documentId, user_id=request.userId)
    with use_process_logger(process_logger):
        process_event(
            stage="api.index-text",
            action="index_text_received",
            message="Python 已接收文本资料索引请求",
            context={"title": request.title, "documentType": request.documentType},
        )
        progress = RagProgressReporter(document_id=request.documentId, user_id=request.userId)
        progress.emit("index.request", "已接收文本资料索引请求", current_step=1, total_steps=8, percent=5)
        parsed = parser_router.parse_text(
            document_id=request.documentId,
            title=request.title,
            document_type=request.documentType,
            source_path=request.sourcePath,
            content=request.content,
            parser=request.parser,
            progress_reporter=progress,
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
            progress_reporter=progress,
        )


@router.post("/documents/index-video-source", response_model=IndexResponse)
def index_video_source(request: IndexVideoSourceRequest) -> IndexResponse:
    if not request.sourcePath.strip():
        raise HTTPException(status_code=400, detail="sourcePath is empty")
    process_logger = RagProcessLogger(document_id=request.documentId, user_id=request.userId)
    with use_process_logger(process_logger):
        process_event(
            stage="api.index-video-source",
            action="index_video_source_received",
            message="Python 已接收视频源索引请求",
            context={"title": request.title, "documentType": request.documentType, "sourcePath": request.sourcePath},
        )
        progress = RagProgressReporter(document_id=request.documentId, user_id=request.userId)
        progress.emit("index.request", "已接收视频源索引请求", current_step=1, total_steps=8, percent=5)
        parsed = parser_router.parse_video_source(
            document_id=request.documentId,
            title=request.title,
            document_type=request.documentType,
            source=request.source,
            user_id=request.userId,
            visibility_scope=request.visibilityScope,
            source_path=request.sourcePath,
            filename=request.filename,
            content_type=request.contentType,
            high_precision=request.highPrecision,
            progress_reporter=progress,
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
            progress_reporter=progress,
        )


@router.post("/documents/index-file", response_model=IndexResponse)
async def index_file(
    file: UploadFile = File(...),
    document_id: str = Form(...),
    title: str = Form(...),
    document_type: str = Form("document"),
    source: str = Form("upload"),
    user_id: str = Form(...),
    visibility_scope: str = Form("private"),
    source_path: str | None = Form(None),
    high_precision: bool = Form(False),
) -> IndexResponse:
    content = await file.read()
    process_logger = RagProcessLogger(document_id=document_id, user_id=user_id)
    with use_process_logger(process_logger):
        process_event(
            stage="api.index-file",
            action="index_file_received",
            message="Python 已接收上传文件索引请求",
            context={
                "title": title,
                "filename": file.filename,
                "documentType": document_type,
                "contentType": file.content_type,
                "size": len(content),
                "highPrecision": high_precision,
            },
        )
        progress = RagProgressReporter(document_id=document_id, user_id=user_id)
        progress.emit("index.request", "已接收上传文件索引请求", current_step=1, total_steps=8, percent=5)
        parsed = parser_router.parse_bytes(
            content=content,
            filename=file.filename or title,
            document_id=document_id,
            source_title=title,
            document_type=document_type,
            content_type=file.content_type,
            source_path=source_path,
            high_precision=high_precision,
            progress_reporter=progress,
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
            progress_reporter=progress,
        )


@router.get("/documents/{document_id}/evidences", response_model=EvidenceListResponse)
def list_document_evidences(document_id: str, limit: int = 20) -> EvidenceListResponse:
    safe_limit = min(max(limit, 1), 100)
    process_logger = RagProcessLogger(document_id=document_id, module="evidence")
    with use_process_logger(process_logger):
        process_event(
            stage="api.evidences",
            action="list_document_evidences_received",
            message="Python 已接收文档 evidence 查询请求",
            context={"limit": safe_limit},
        )
        return EvidenceListResponse(documentId=document_id, evidences=store.list_evidences(document_id, safe_limit))


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question is empty")
    user_id = str((request.metadataFilter or {}).get("userId") or "anonymous")
    process_logger = RagProcessLogger(document_id="query", user_id=user_id, module="query")
    with use_process_logger(process_logger):
        process_event(
            stage="api.query",
            action="query_received",
            message="Python 已接收 RAG 检索问答请求",
            context={"topK": request.topK, "metadataFilter": request.metadataFilter},
        )
        response = store.query(request)
        if not response.evidences:
            response.answer = "当前知识库没有检索到足够相关的证据，请先上传或索引学习资料。"
        return response


@router.post("/jd-analysis", response_model=JdAnalyzeResponse)
def analyze_jd(request: JdAnalyzeRequest) -> JdAnalyzeResponse:
    if not request.jobDescription.strip():
        raise HTTPException(status_code=400, detail="jobDescription is empty")
    process_logger = RagProcessLogger(document_id="jd-analysis", user_id=request.userId, module="jd-analysis")
    with use_process_logger(process_logger):
        process_event(
            stage="api.jd-analysis",
            action="jd_analysis_received",
            message="Python 已接收 JD 适配分析请求",
            context={"topK": request.topK},
        )
        skills = extract_jd_skills(request.jobDescription)
        skill_results: list[JdSkillResult] = []
        alignments: list[ResumeAlignmentResult] = []
        for skill in skills:
            query_response = store.query(
                QueryRequest(
                    question=f"{skill} 项目经验 学习证据 课程笔记",
                    topK=request.topK,
                    metadataFilter={"userId": request.userId, "visibilityScope": "private"},
                )
            )
            status = classify_skill(skill, query_response.evidences, request.resumeText or "")
            skill_results.append(JdSkillResult(skillName=skill, status=status, evidences=query_response.evidences))
            alignments.append(
                ResumeAlignmentResult(
                    requirement=skill,
                    evidence=build_alignment_text(skill, query_response.evidences, request.resumeText or ""),
                    status=status,
                )
            )

        mastered = sum(1 for item in skill_results if item.status == "supported")
        partial = sum(1 for item in skill_results if item.status == "weak")
        missing = sum(1 for item in skill_results if item.status == "missing")
        total = max(len(skill_results), 1)
        mastered_percent = round(mastered * 100 / total)
        partial_percent = round(partial * 100 / total)
        gap_percent = max(0, 100 - mastered_percent - partial_percent)
        match_score = min(100, mastered_percent + round(partial_percent * 0.5))

        return JdAnalyzeResponse(
            jobDescription=request.jobDescription,
            matchScore=match_score,
            masteredPercent=mastered_percent,
            partialPercent=partial_percent,
            gapPercent=gap_percent,
            skills=skill_results,
            learningPlan=build_learning_plan(skill_results),
            resumeAlignments=alignments,
        )


@router.get("/overview", response_model=OverviewResponse)
def overview() -> OverviewResponse:
    process_logger = RagProcessLogger(document_id="overview", module="overview")
    with use_process_logger(process_logger):
        process_event(
            stage="api.overview",
            action="overview_received",
            message="Python 已接收 RAG 概览请求",
        )
        return store.overview()


@logged_rag_method("jd.extract", "extract_jd_skills", "从 JD 文本抽取技能项")
def extract_jd_skills(job_description: str) -> list[str]:
    """从 JD 文本中抽取第一版技能项，保留常见中英文技术关键词。"""
    known_terms = [
        "RAG-Fusion", "Multi-Query", "BM25", "pgvector", "MinerU", "OCR", "ASR",
        "FastAPI", "Spring Boot", "React", "Vite", "MyBatis", "PostgreSQL",
        "向量检索", "混合检索", "递归切块", "提示词", "简历优化", "岗位适配",
        "视频检索", "字幕解析", "知识库", "学习计划", "证据引用",
    ]
    found: list[str] = []
    normalized = job_description.lower()
    for term in known_terms:
        if term.lower() in normalized:
            found.append(term)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_+#.-]{2,}", job_description):
        if token.lower() not in {"and", "the", "with", "for", "job", "api"}:
            found.append(token)
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,8}(?:检索|切块|识别|分析|适配|证据|知识库|学习计划)", job_description):
        found.append(phrase)
    unique = list(dict.fromkeys(found))
    return unique[:12] or ["岗位核心能力"]


@logged_rag_method("jd.classify", "classify_skill", "判断 JD 技能掌握状态")
def classify_skill(skill: str, evidences: list, resume_text: str) -> str:
    """根据 RAG evidence 和简历文本命中情况分类掌握状态。"""
    in_resume = skill.lower() in resume_text.lower()
    if len(evidences) >= 2:
        return "supported"
    if evidences or in_resume:
        return "weak"
    return "missing"


@logged_rag_method("jd.align", "build_alignment_text", "生成 JD 与证据对齐摘要")
def build_alignment_text(skill: str, evidences: list, resume_text: str) -> str:
    """生成 JD 要求和简历/RAG 证据的对齐摘要。"""
    if evidences:
        top = evidences[0]
        return f"命中知识库证据：{top.title} / {top.sectionName}，片段：{top.snippet}"
    if skill.lower() in resume_text.lower():
        return "简历文本中出现该能力关键词，但知识库暂未检索到可引用证据。"
    return "暂未在个人知识库或简历文本中找到足够证据，需要补充学习资料或项目记录。"


@logged_rag_method("jd.plan", "build_learning_plan", "生成 JD 学习计划")
def build_learning_plan(skills: list[JdSkillResult]) -> list[JdLearningPlanResult]:
    """根据缺口和半掌握技能生成学习计划。"""
    targets = [item for item in skills if item.status in {"missing", "weak"}]
    if not targets:
        return [
            JdLearningPlanResult(
                stepNo=1,
                title="整理高质量证据引用",
                description="当前 JD 能力项已有较充分证据，建议补充项目结果、指标和可复盘材料。",
            )
        ]
    plans: list[JdLearningPlanResult] = []
    for index, item in enumerate(targets[:5], start=1):
        action = "补充学习资料并完成一次项目实践" if item.status == "missing" else "补齐项目复盘和证据引用"
        plans.append(
            JdLearningPlanResult(
                stepNo=index,
                title=f"强化 {item.skillName}",
                description=f"{action}，完成后重新索引资料并在简历中加入可追溯证据。",
            )
        )
    return plans
