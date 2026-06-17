from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from rag.loaders.document_parsers import DocumentParserRouter
from rag.loaders.mineru_loader import MineruDocumentLoader
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


@router.post("/documents/index-video-source", response_model=IndexResponse)
def index_video_source(request: IndexVideoSourceRequest) -> IndexResponse:
    if not request.sourcePath.strip():
        raise HTTPException(status_code=400, detail="sourcePath is empty")
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
    user_id: str = Form(...),
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


@router.post("/jd-analysis", response_model=JdAnalyzeResponse)
def analyze_jd(request: JdAnalyzeRequest) -> JdAnalyzeResponse:
    if not request.jobDescription.strip():
        raise HTTPException(status_code=400, detail="jobDescription is empty")
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
    return store.overview()


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


def classify_skill(skill: str, evidences: list, resume_text: str) -> str:
    """根据 RAG evidence 和简历文本命中情况分类掌握状态。"""
    in_resume = skill.lower() in resume_text.lower()
    if len(evidences) >= 2:
        return "supported"
    if evidences or in_resume:
        return "weak"
    return "missing"


def build_alignment_text(skill: str, evidences: list, resume_text: str) -> str:
    """生成 JD 要求和简历/RAG 证据的对齐摘要。"""
    if evidences:
        top = evidences[0]
        return f"命中知识库证据：{top.title} / {top.sectionName}，片段：{top.snippet}"
    if skill.lower() in resume_text.lower():
        return "简历文本中出现该能力关键词，但知识库暂未检索到可引用证据。"
    return "暂未在个人知识库或简历文本中找到足够证据，需要补充学习资料或项目记录。"


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
