from __future__ import annotations

import base64
import io
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas.resume_template import (
    ResumeTemplateBinding,
    ResumeTemplatePreviewPage,
    ResumeTemplatePreviewRect,
    ResumeTemplatePreviewRegion,
    ResumeTemplatePreviewRequest,
    ResumeTemplatePreviewResponse,
)
from rag.loaders.document_parsers import convert_with_libreoffice


def build_resume_template_preview(request: ResumeTemplatePreviewRequest) -> ResumeTemplatePreviewResponse:
    """生成简历模板页面图片和字段相对坐标，失败时保留旧字段流程可继续使用。"""
    try:
        content = base64.b64decode(request.fileBase64)
    except Exception as exc:
        return unavailable_preview(request, [f"DOCX base64 解码失败: {exc}"])
    if not content:
        return unavailable_preview(request, ["DOCX 文件内容为空，无法生成图片预览"])
    try:
        import fitz  # type: ignore
    except Exception as exc:
        return unavailable_preview(request, [f"PyMuPDF 不可用，无法渲染预览图片: {exc}"])

    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="resume-template-preview-") as temp_dir:
        workdir = Path(temp_dir)
        docx_path = workdir / safe_filename(request.filename)
        docx_path.write_bytes(content)
        pdf_path = convert_with_libreoffice(docx_path, "pdf", workdir)
        if pdf_path is None or not pdf_path.exists():
            warnings.append("LibreOffice/soffice 不可用或 DOCX 转 PDF 失败，已生成字段草图预览；该预览用于确认字段边界，不代表 DOCX 精确版式")
            return build_field_sketch_preview(fitz, request, warnings)
        try:
            return render_pdf_preview(fitz, pdf_path, request, warnings)
        except Exception as exc:
            warnings.append(f"PDF 预览渲染失败，已生成字段草图预览: {exc}")
            try:
                return build_field_sketch_preview(fitz, request, warnings)
            except Exception as sketch_exc:
                return unavailable_preview(request, [*warnings, f"字段草图预览生成失败: {sketch_exc}"])


def render_pdf_preview(fitz_module: Any, pdf_path: Path, request: ResumeTemplatePreviewRequest, warnings: list[str]) -> ResumeTemplatePreviewResponse:
    """渲染 PDF 页面图片，并把 PDF 文本块匹配回已解析字段。"""
    pages: list[ResumeTemplatePreviewPage] = []
    text_blocks: list[dict[str, Any]] = []
    with fitz_module.open(str(pdf_path)) as document:
        for page_index, page in enumerate(document):
            rect = page.rect
            pixmap = page.get_pixmap(matrix=fitz_module.Matrix(2, 2), alpha=False)
            image_bytes = pixmap.tobytes("png")
            pages.append(
                ResumeTemplatePreviewPage(
                    pageIndex=page_index,
                    width=max(1, int(rect.width * 2)),
                    height=max(1, int(rect.height * 2)),
                    imageBase64=base64.b64encode(image_bytes).decode("ascii"),
                    imageMimeType="image/png",
                )
            )
            text_blocks.extend(extract_page_text_blocks(page, page_index, rect.width, rect.height))
    if not pages:
        return unavailable_preview(request, ["PDF 未渲染出页面，无法生成图片预览"])

    regions: list[ResumeTemplatePreviewRegion] = []
    unmapped: list[dict[str, Any]] = []
    used_block_indexes: set[int] = set()
    for field in request.fields:
        match = best_block_match(field, text_blocks, used_block_indexes)
        if match is None:
            unmapped.append(unmapped_field(field, "未在 PDF 文本块中匹配到字段原文"))
            continue
        used_block_indexes.add(match["index"])
        regions.append(
            ResumeTemplatePreviewRegion(
                fieldId=field.fieldId,
                displayName=field.displayName,
                sectionKey=field.sectionKey,
                sourceTextHash=field.sourceTextHash,
                pageIndex=match["pageIndex"],
                rect=ResumeTemplatePreviewRect(**match["rect"]),
                confidence=match["confidence"],
                previewStatus="READY",
            )
        )
    if unmapped:
        warnings.append(f"{len(unmapped)} 个字段未映射到图片坐标")
    status = "READY" if regions and not unmapped else "PARTIAL" if regions else "UNAVAILABLE"
    return ResumeTemplatePreviewResponse(
        templateId=request.templateId,
        version=request.version,
        previewStatus=status,
        pages=pages if status != "UNAVAILABLE" else [],
        regions=regions,
        unmappedFields=unmapped,
        warnings=warnings,
        generatedAt=now_iso(),
    )


def build_field_sketch_preview(fitz_module: Any, request: ResumeTemplatePreviewRequest, warnings: list[str]) -> ResumeTemplatePreviewResponse:
    """在缺少 DOCX 精确渲染依赖时生成字段草图图片，保证前端确认流程可继续。"""
    page_width = 595.0
    page_height = 842.0
    margin_x = 46.0
    start_y = 72.0
    row_height = 52.0
    row_gap = 12.0
    rows_per_page = max(1, int((page_height - start_y - 54.0) // (row_height + row_gap)))
    document = fitz_module.open()
    pages: list[ResumeTemplatePreviewPage] = []
    regions: list[ResumeTemplatePreviewRegion] = []
    try:
        for page_start in range(0, len(request.fields), rows_per_page):
            page_index = page_start // rows_per_page
            page = document.new_page(width=page_width, height=page_height)
            draw_sketch_header(fitz_module, page, page_index, request.filename, margin_x, page_width)
            for offset, field in enumerate(request.fields[page_start:page_start + rows_per_page]):
                y = start_y + offset * (row_height + row_gap)
                rect = fitz_module.Rect(margin_x, y, page_width - margin_x, y + row_height)
                page.draw_rect(rect, color=(0.38, 0.48, 0.62), fill=(0.96, 0.98, 1.0), width=0.8)
                draw_sketch_field_text(fitz_module, page, rect, page_start + offset + 1, field)
                regions.append(
                    ResumeTemplatePreviewRegion(
                        fieldId=field.fieldId,
                        displayName=field.displayName,
                        sectionKey=field.sectionKey,
                        sourceTextHash=field.sourceTextHash,
                        pageIndex=page_index,
                        rect=ResumeTemplatePreviewRect(**clamp_rect(
                            x=margin_x / page_width,
                            y=y / page_height,
                            width=(page_width - margin_x * 2) / page_width,
                            height=row_height / page_height,
                        )),
                        confidence=0.36,
                        previewStatus="PARTIAL",
                    )
                )
            pixmap = page.get_pixmap(matrix=fitz_module.Matrix(2, 2), alpha=False)
            pages.append(
                ResumeTemplatePreviewPage(
                    pageIndex=page_index,
                    width=int(page_width * 2),
                    height=int(page_height * 2),
                    imageBase64=base64.b64encode(pixmap.tobytes("png")).decode("ascii"),
                    imageMimeType="image/png",
                )
            )
    finally:
        document.close()
    return ResumeTemplatePreviewResponse(
        templateId=request.templateId,
        version=request.version,
        previewStatus="PARTIAL",
        pages=pages,
        regions=regions,
        unmappedFields=[],
        warnings=warnings,
        generatedAt=now_iso(),
    )


def draw_sketch_header(fitz_module: Any, page: Any, page_index: int, filename: str, margin_x: float, page_width: float) -> None:
    """绘制草图页眉，提示这是字段确认用的降级预览。"""
    header_rect = fitz_module.Rect(margin_x, 26, page_width - margin_x, 56)
    text = f"Resume template field sketch · page {page_index + 1} · {filename[:48]}"
    page.insert_textbox(header_rect, text, fontsize=10, fontname="helv", color=(0.15, 0.23, 0.35))


def draw_sketch_field_text(fitz_module: Any, page: Any, rect: Any, index: int, field: ResumeTemplateBinding) -> None:
    """绘制字段摘要；中文字体不可用时降级为 ASCII 标签，避免预览生成失败。"""
    text_rect = fitz_module.Rect(rect.x0 + 10, rect.y0 + 8, rect.x1 - 10, rect.y1 - 6)
    source = re.sub(r"\s+", " ", field.sourceText or "").strip()
    label = f"{index}. {field.displayName} [{field.sectionKey}]\n{source[:72]}"
    try:
        page.insert_textbox(text_rect, label, fontsize=8.5, fontname="helv", color=(0.15, 0.23, 0.35))
    except Exception:
        fallback = f"Field {index}: {field.fieldId}\nSection: {field.sectionKey}"
        page.insert_textbox(text_rect, fallback, fontsize=8.5, fontname="helv", color=(0.15, 0.23, 0.35))


def extract_page_text_blocks(page: Any, page_index: int, page_width: float, page_height: float) -> list[dict[str, Any]]:
    """提取 PDF 页面文本块，并转换为 0..1 相对坐标。"""
    blocks: list[dict[str, Any]] = []
    for index, raw in enumerate(page.get_text("blocks") or []):
        if len(raw) < 5:
            continue
        x0, y0, x1, y1, text = raw[:5]
        normalized = normalize_for_match(str(text))
        if not normalized:
            continue
        rect = clamp_rect(
            x=float(x0) / max(page_width, 1.0),
            y=float(y0) / max(page_height, 1.0),
            width=(float(x1) - float(x0)) / max(page_width, 1.0),
            height=(float(y1) - float(y0)) / max(page_height, 1.0),
        )
        blocks.append(
            {
                "index": page_index * 100000 + index,
                "pageIndex": page_index,
                "text": str(text),
                "normalizedText": normalized,
                "rect": rect,
            }
        )
    return blocks


def best_block_match(field: ResumeTemplateBinding, blocks: list[dict[str, Any]], used_indexes: set[int]) -> dict[str, Any] | None:
    """把字段原文匹配到最可信的 PDF 文本块。"""
    source = normalize_for_match(field.sourceText)
    if not source:
        return None
    candidates: list[dict[str, Any]] = []
    for block in blocks:
        if block["index"] in used_indexes:
            continue
        target = block["normalizedText"]
        if source in target or target in source:
            overlap = min(len(source), len(target)) / max(len(source), len(target), 1)
            confidence = 0.95 if source == target else max(0.72, min(0.9, overlap))
            candidates.append({**block, "confidence": confidence})
            continue
        overlap = token_overlap(source, target)
        if overlap >= 0.65:
            candidates.append({**block, "confidence": min(0.7, overlap)})
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["confidence"])


def token_overlap(left: str, right: str) -> float:
    """计算字符二元组重叠度，兼容中英文混排字段。"""
    left_tokens = ngrams(left)
    right_tokens = ngrams(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), 1)


def ngrams(value: str) -> set[str]:
    """生成用于模糊匹配的二元字符片段。"""
    if len(value) <= 2:
        return {value}
    return {value[index:index + 2] for index in range(len(value) - 1)}


def normalize_for_match(value: str) -> str:
    """归一化文本用于 DOCX 字段和 PDF 文本块匹配。"""
    return re.sub(r"\s+", "", value or "").lower()


def clamp_rect(x: float, y: float, width: float, height: float) -> dict[str, float]:
    """把矩形限制在 0..1 范围内，并保留可见最小宽高。"""
    safe_x = min(max(x, 0.0), 0.999)
    safe_y = min(max(y, 0.0), 0.999)
    safe_width = min(max(width, 0.001), max(0.001, 1.0 - safe_x))
    safe_height = min(max(height, 0.001), max(0.001, 1.0 - safe_y))
    return {
        "x": round(safe_x, 6),
        "y": round(safe_y, 6),
        "width": round(safe_width, 6),
        "height": round(safe_height, 6),
    }


def unmapped_field(field: ResumeTemplateBinding, reason: str) -> dict[str, Any]:
    """构造未映射字段摘要，避免返回 DOCX 定位细节。"""
    return {
        "fieldId": field.fieldId,
        "displayName": field.displayName,
        "sectionKey": field.sectionKey,
        "sourceTextHash": field.sourceTextHash,
        "previewStatus": "UNMAPPED",
        "reason": reason,
    }


def unavailable_preview(request: ResumeTemplatePreviewRequest, warnings: list[str]) -> ResumeTemplatePreviewResponse:
    """构造不可用预览响应，让 Java 和前端保持可恢复流程。"""
    return ResumeTemplatePreviewResponse(
        templateId=request.templateId,
        version=request.version,
        previewStatus="UNAVAILABLE",
        pages=[],
        regions=[],
        unmappedFields=[unmapped_field(field, "预览依赖不可用") for field in request.fields],
        warnings=warnings,
        generatedAt=now_iso(),
    )


def safe_filename(filename: str) -> str:
    """清理临时 DOCX 文件名，避免路径穿越。"""
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", filename or "resume-template.docx").strip()
    return cleaned if cleaned.lower().endswith(".docx") else f"{cleaned}.docx"


def now_iso() -> str:
    """生成接口使用的 UTC ISO 时间。"""
    return datetime.now(timezone.utc).isoformat()
