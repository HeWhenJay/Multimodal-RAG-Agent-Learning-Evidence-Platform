from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from rag.loaders.document_parsers import DocumentParserRouter
from rag.loaders.mineru_loader import MineruDocumentLoader
from rag.process_logger import RagProcessLogger, logged_rag_method, process_event, use_process_logger
from rag.progress import RagProgressReporter
from rag.retrievers.retrieval import create_rag_store
from rag.resume_template.docx_patch import (
    apply_resume_patches_to_docx,
    parse_resume_template_docx,
    validate_resume_patches,
)
from rag.resume_template.patch_generation import generate_resume_patches
from rag.resume_template.preview import build_resume_template_preview
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
    QueryTaskResponse,
    ResumeAlignmentResult,
)
from app.schemas.resume_template import (
    ResumePatchGenerationRequest,
    ResumePatchGenerationResponse,
    ResumePatchValidationRequest,
    ResumePatchValidationResponse,
    ResumeTemplateExportRequest,
    ResumeTemplateExportResponse,
    ResumeTemplateParseResponse,
    ResumeTemplatePreviewRequest,
    ResumeTemplatePreviewResponse,
)

import base64
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock


router = APIRouter(prefix="/internal/rag", tags=["RAG"])
loader = MineruDocumentLoader()
parser_router = DocumentParserRouter(loader)
store = create_rag_store()
query_task_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rag-query-task")
query_tasks: dict[str, dict] = {}
query_tasks_lock = Lock()
QUERY_TASK_TTL = timedelta(minutes=30)


@router.post("/documents/index-text", response_model=IndexResponse)
def index_text(request: IndexTextRequest) -> IndexResponse:
    process_logger = RagProcessLogger(document_id=request.documentId, user_id=request.userId)
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.index-text",
            "index_text_pipeline",
            "处理文本资料索引请求",
            context={"title": request.title, "documentType": request.documentType},
        ):
            process_event(
                stage="api.index-text",
                action="index_text_received",
                message="Python 已接收文本资料索引请求",
                context={"title": request.title, "documentType": request.documentType},
            )
            if not request.content.strip():
                process_event(
                    stage="api.index-text",
                    action="index_text_rejected",
                    message="文本资料内容为空，已拒绝索引",
                    level="WARN",
                    context={"title": request.title, "documentType": request.documentType},
                )
                raise HTTPException(status_code=400, detail="content is empty")
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
    process_logger = RagProcessLogger(document_id=request.documentId, user_id=request.userId)
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.index-video-source",
            "index_video_source_pipeline",
            "处理视频源索引请求",
            context={"title": request.title, "documentType": request.documentType, "sourcePath": request.sourcePath},
        ):
            process_event(
                stage="api.index-video-source",
                action="index_video_source_received",
                message="Python 已接收视频源索引请求",
                context={"title": request.title, "documentType": request.documentType, "sourcePath": request.sourcePath},
            )
            if not request.sourcePath.strip():
                process_event(
                    stage="api.index-video-source",
                    action="index_video_source_rejected",
                    message="视频源路径为空，已拒绝索引",
                    level="WARN",
                    context={"title": request.title, "documentType": request.documentType},
                )
                raise HTTPException(status_code=400, detail="sourcePath is empty")
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
    process_logger = RagProcessLogger(document_id=document_id, user_id=user_id)
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.index-file",
            "index_file_pipeline",
            "处理上传文件索引请求",
            context={
                "title": title,
                "filename": file.filename,
                "documentType": document_type,
                "contentType": file.content_type,
                "highPrecision": high_precision,
            },
        ):
            process_event(
                stage="api.index-file",
                action="index_file_received",
                message="Python 已接收上传文件索引请求，准备读取文件字节",
                context={
                    "title": title,
                    "filename": file.filename,
                    "documentType": document_type,
                    "contentType": file.content_type,
                    "highPrecision": high_precision,
                },
            )
            content = await file.read()
            process_event(
                stage="api.index-file",
                action="index_file_read_completed",
                message="上传文件字节读取完成",
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


@router.post("/resume/templates/parse", response_model=ResumeTemplateParseResponse)
async def parse_resume_template(
    file: UploadFile = File(...),
    template_id: str | None = Form(None),
    version: int = Form(1),
) -> ResumeTemplateParseResponse:
    """解析 Java 转发的受控 DOCX 文件，生成字段绑定和版式指纹。"""
    filename = file.filename or "resume-template.docx"
    process_logger = RagProcessLogger(document_id=template_id or "resume-template", module="resume-template")
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.resume-template.parse",
            "parse_resume_template_pipeline",
            "处理简历模板字段解析请求",
            context={"filename": filename, "version": version},
        ):
            content = await file.read()
            process_event(
                stage="api.resume-template.parse",
                action="resume_template_parse_received",
                message="Python 已接收简历模板解析请求",
                context={"filename": filename, "fileSize": len(content), "version": version},
            )
            try:
                return parse_resume_template_docx(content, filename, template_id=template_id, version=version)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                process_event(
                    stage="api.resume-template.parse",
                    action="resume_template_parse_failed",
                    message="简历模板解析失败",
                    status="FAILED",
                    context={"filename": filename, "error": str(exc)},
                )
                raise HTTPException(status_code=400, detail=f"简历模板解析失败：{exc}") from exc


@router.post("/resume/templates/patches/generate", response_model=ResumePatchGenerationResponse)
def generate_resume_template_patches(request: ResumePatchGenerationRequest) -> ResumePatchGenerationResponse:
    """基于 Structured Outputs 或严格 schema 校验生成字段级补丁草稿。"""
    process_logger = RagProcessLogger(document_id=request.templateId, module="resume-template")
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.resume-template.patch-generate",
            "generate_resume_template_patches_pipeline",
            "处理简历模板补丁生成请求",
            context={
                "templateId": request.templateId,
                "version": request.version,
                "fieldCount": len(request.fields),
                "evidenceCount": len(request.evidenceCandidates),
                "jobDescriptionLength": len(request.jobDescription),
            },
        ):
            process_event(
                stage="api.resume-template.patch-generate",
                action="resume_template_patch_generate_received",
                message="Python 已接收简历字段补丁生成请求",
                context={
                    "templateId": request.templateId,
                    "version": request.version,
                    "fieldCount": len(request.fields),
                    "evidenceCount": len(request.evidenceCandidates),
                    "provider": request.provider,
                },
            )
            return generate_resume_patches(request)


@router.post("/resume/templates/preview", response_model=ResumeTemplatePreviewResponse)
def preview_resume_template(request: ResumeTemplatePreviewRequest) -> ResumeTemplatePreviewResponse:
    """生成 DOCX 页面图片预览和字段图片坐标，坐标仅用于视觉确认。"""
    process_logger = RagProcessLogger(document_id=request.templateId, module="resume-template")
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.resume-template.preview",
            "preview_resume_template_pipeline",
            "处理简历模板图片预览请求",
            context={
                "templateId": request.templateId,
                "version": request.version,
                "filename": request.filename,
                "fieldCount": len(request.fields),
            },
        ):
            process_event(
                stage="api.resume-template.preview",
                action="resume_template_preview_received",
                message="Python 已接收简历模板图片预览请求",
                context={
                    "templateId": request.templateId,
                    "version": request.version,
                    "fieldCount": len(request.fields),
                },
            )
            return build_resume_template_preview(request)


@router.post("/resume/templates/patches/validate", response_model=ResumePatchValidationResponse)
def validate_resume_template_patch_request(request: ResumePatchValidationRequest) -> ResumePatchValidationResponse:
    """校验用户选择的字段补丁，拒绝排版字段和注入内容。"""
    return validate_resume_patches(
        template_id=request.templateId,
        version=request.version,
        fields=request.fields,
        patches=request.patches,
        allowed_evidence_ids=request.allowedEvidenceIds,
    )


@router.post("/resume/templates/exports", response_model=ResumeTemplateExportResponse)
def export_resume_template(request: ResumeTemplateExportRequest) -> ResumeTemplateExportResponse:
    """把确认后的字段补丁确定性应用到 DOCX 字节流。"""
    try:
        content = base64.b64decode(request.fileBase64)
        return apply_resume_patches_to_docx(
            content=content,
            filename=request.filename,
            template_id=request.templateId,
            version=request.version,
            fields=request.fields,
            patches=request.patches,
            allowed_evidence_ids=request.allowedEvidenceIds,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "RESUME_LAYOUT_CHANGED" in detail or "hash 已变化" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/documents/{document_id}/evidences", response_model=EvidenceListResponse)
def list_document_evidences(document_id: str, limit: int = 20) -> EvidenceListResponse:
    safe_limit = min(max(limit, 1), 100)
    process_logger = RagProcessLogger(document_id=document_id, module="evidence")
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.evidences",
            "list_document_evidences_pipeline",
            "处理文档 evidence 查询请求",
            context={"limit": safe_limit},
        ):
            process_event(
                stage="api.evidences",
                action="list_document_evidences_received",
                message="Python 已接收文档 evidence 查询请求",
                context={"limit": safe_limit},
            )
            return EvidenceListResponse(documentId=document_id, evidences=store.list_evidences(document_id, safe_limit))


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    user_id = str((request.metadataFilter or {}).get("userId") or "anonymous")
    process_logger = RagProcessLogger(document_id="query", user_id=user_id, module="query")
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.query",
            "query_pipeline",
            "处理 RAG 检索问答请求",
            context={"topK": request.topK, "candidateMultiplier": request.candidateMultiplier, "metadataFilter": request.metadataFilter},
        ):
            process_event(
                stage="api.query",
                action="query_received",
                message="Python 已接收 RAG 检索问答请求",
                context={"topK": request.topK, "candidateMultiplier": request.candidateMultiplier, "metadataFilter": request.metadataFilter},
            )
            if not request.question.strip():
                process_event(
                    stage="api.query",
                    action="query_rejected",
                    message="检索问题为空，已拒绝请求",
                    level="WARN",
                    context={"topK": request.topK, "candidateMultiplier": request.candidateMultiplier, "metadataFilter": request.metadataFilter},
                )
                raise HTTPException(status_code=400, detail="question is empty")
            return store.query(request)


@router.post("/query/tasks", response_model=QueryTaskResponse)
def start_query_task(request: QueryRequest) -> QueryTaskResponse:
    """创建 RAG 查询后台任务，让 Java 和前端可以轮询真实阶段事件。"""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question is empty")
    cleanup_expired_query_tasks()
    task_id = uuid.uuid4().hex
    now = now_iso()
    task = {
        "taskId": task_id,
        "status": "RUNNING",
        "message": "正在执行 RAG 检索问答",
        "progressEvents": [],
        "result": None,
        "errorMessage": None,
        "createdAt": now,
        "updatedAt": now,
    }
    with query_tasks_lock:
        query_tasks[task_id] = task
    query_task_executor.submit(run_query_task, task_id, request)
    return task_response(task)


@router.get("/query/tasks/{task_id}", response_model=QueryTaskResponse)
def get_query_task(task_id: str) -> QueryTaskResponse:
    """读取 RAG 查询任务当前状态和已产生的阶段事件。"""
    cleanup_expired_query_tasks()
    with query_tasks_lock:
        task = query_tasks.get(task_id)
        if task is None:
            return QueryTaskResponse(
                taskId=task_id,
                status="EXPIRED",
                message="RAG 查询任务不存在或已过期",
                progressEvents=[],
                result=None,
                errorMessage="RAG 查询任务不存在或已过期",
                createdAt=None,
                updatedAt=None,
            )
        return task_response(task)


def run_query_task(task_id: str, request: QueryRequest) -> None:
    """在后台执行 RAG 检索，并把每个 reporter 事件写入任务快照。"""
    user_id = str((request.metadataFilter or {}).get("userId") or "anonymous")
    process_logger = RagProcessLogger(document_id="query", user_id=user_id, module="query")

    def on_emit(event) -> None:
        with query_tasks_lock:
            task = query_tasks.get(task_id)
            if task is None:
                return
            task["progressEvents"] = [*task["progressEvents"], event]
            task["message"] = event.message
            task["updatedAt"] = now_iso()

    try:
        with use_process_logger(process_logger):
            with process_logger.step(
                "api.query.task",
                "query_task_pipeline",
                "处理 RAG 检索问答任务",
                context={"taskId": task_id, "topK": request.topK, "candidateMultiplier": request.candidateMultiplier, "metadataFilter": request.metadataFilter},
            ):
                process_event(
                    stage="api.query.task",
                    action="query_task_received",
                    message="Python 已接收 RAG 检索问答任务",
                    context={"taskId": task_id, "topK": request.topK, "candidateMultiplier": request.candidateMultiplier, "metadataFilter": request.metadataFilter},
                )
                progress = RagProgressReporter(document_id="query", user_id=user_id, persist=False, on_emit=on_emit)
                response = store.query(request, progress_reporter=progress)
                with query_tasks_lock:
                    task = query_tasks.get(task_id)
                    if task is None:
                        return
                    task["status"] = "COMPLETED"
                    task["message"] = "RAG 检索问答完成"
                    task["progressEvents"] = response.progressEvents
                    task["result"] = response
                    task["updatedAt"] = now_iso()
    except Exception as exc:
        with query_tasks_lock:
            task = query_tasks.get(task_id)
            if task is None:
                return
            task["status"] = "FAILED"
            task["message"] = "RAG 检索问答失败"
            task["errorMessage"] = str(exc)
            task["updatedAt"] = now_iso()


def task_response(task: dict) -> QueryTaskResponse:
    """把内部任务字典转换为接口响应模型，避免外部持有可变引用。"""
    return QueryTaskResponse(
        taskId=task["taskId"],
        status=task["status"],
        message=task["message"],
        progressEvents=list(task["progressEvents"]),
        result=task["result"],
        errorMessage=task["errorMessage"],
        createdAt=task["createdAt"],
        updatedAt=task["updatedAt"],
    )


def cleanup_expired_query_tasks() -> None:
    """清理过期查询任务，避免临时进度快照长期占用内存。"""
    cutoff = datetime.now(timezone.utc) - QUERY_TASK_TTL
    with query_tasks_lock:
        expired_ids = [
            task_id
            for task_id, task in query_tasks.items()
            if parse_iso_datetime(task.get("updatedAt")) < cutoff
        ]
        for task_id in expired_ids:
            query_tasks.pop(task_id, None)


def now_iso() -> str:
    """生成接口使用的 UTC ISO 时间。"""
    return datetime.now(timezone.utc).isoformat()


def parse_iso_datetime(value: str | None) -> datetime:
    """解析任务更新时间，异常值按最早时间处理以便清理。"""
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


@router.post("/jd-analysis", response_model=JdAnalyzeResponse)
def analyze_jd(request: JdAnalyzeRequest) -> JdAnalyzeResponse:
    process_logger = RagProcessLogger(document_id="jd-analysis", user_id=request.userId, module="jd-analysis")
    with use_process_logger(process_logger):
        with process_logger.step(
            "api.jd-analysis",
            "jd_analysis_pipeline",
            "处理 JD 适配分析请求",
            context={"topK": request.topK},
        ):
            process_event(
                stage="api.jd-analysis",
                action="jd_analysis_received",
                message="Python 已接收 JD 适配分析请求",
                context={"topK": request.topK},
            )
            if not request.jobDescription.strip():
                process_event(
                    stage="api.jd-analysis",
                    action="jd_analysis_rejected",
                    message="JD 文本为空，已拒绝请求",
                    level="WARN",
                    context={"topK": request.topK},
                )
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
    process_logger = RagProcessLogger(document_id="overview", module="overview")
    with use_process_logger(process_logger):
        with process_logger.step("api.overview", "overview_pipeline", "处理 RAG 概览请求"):
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
