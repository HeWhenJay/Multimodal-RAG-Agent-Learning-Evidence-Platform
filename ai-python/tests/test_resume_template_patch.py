import base64
import io
import zipfile
from pathlib import Path

import pytest
from docx import Document

from app.schemas.resume_template import ResumeContentPatch, ResumePatchGenerationRequest
from rag.resume_template.docx_patch import (
    apply_resume_patches_to_docx,
    parse_resume_template_docx,
    validate_resume_patches,
)
from rag.resume_template.patch_generation import generate_resume_patches, resume_patch_json_schema


def docx_bytes(document: Document) -> bytes:
    """把测试 DOCX 文档保存为字节。"""
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_parse_resume_template_builds_bindings_and_replaces_single_run_table_cell():
    """解析字段绑定后，只替换表格单元格中的既有 w:t 文本节点。"""
    document = Document()
    document.add_heading("项目经历", level=1)
    document.add_paragraph("多模态 RAG 学习证据平台，负责 FastAPI 检索接口。")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "技能：Python、Spring Boot、React"
    content = docx_bytes(document)

    parsed = parse_resume_template_docx(content, "resume.docx", template_id="tpl-1")
    table_field = next(field for field in parsed.fields if field.locationRefs[0].containerType == "table_cell")
    patch = ResumeContentPatch(
        fieldId=table_field.fieldId,
        sourceTextHash=table_field.sourceTextHash,
        newText="技能：Python、FastAPI、Spring Boot、React",
        rewriteReason="根据 JD 强化 FastAPI 表述",
        evidenceIds=["e1"],
        confidence=0.9,
        riskFlags=["NONE"],
        status="CONFIRMED",
    )

    exported = apply_resume_patches_to_docx(
        content,
        "resume.docx",
        "tpl-1",
        1,
        parsed.fields,
        [patch],
        allowed_evidence_ids=["e1"],
    )
    exported_bytes = base64.b64decode(exported.fileBase64)
    result_doc = Document(io.BytesIO(exported_bytes))

    assert exported.appliedPatchCount == 1
    assert exported.layoutValidation["status"] == "PASSED"
    assert result_doc.tables[0].cell(0, 0).text == "技能：Python、FastAPI、Spring Boot、React"


def test_validate_resume_patches_rejects_unknown_hash_and_markup_injection():
    """补丁校验拒绝未知字段、hash 不匹配和 HTML/XML/Markdown 注入。"""
    document = Document()
    document.add_paragraph("项目：RAG 检索平台")
    parsed = parse_resume_template_docx(docx_bytes(document), "resume.docx", template_id="tpl-2")
    field = parsed.fields[0]
    patches = [
        ResumeContentPatch(
            fieldId="missing",
            sourceTextHash=field.sourceTextHash,
            newText="无效字段",
            rewriteReason="测试未知字段",
            evidenceIds=[],
            confidence=0.1,
            riskFlags=["LOW_CONFIDENCE"],
            status="DRAFT",
        ),
        ResumeContentPatch(
            fieldId=field.fieldId,
            sourceTextHash="bad-hash-value-0000",
            newText="<w:t>注入</w:t>",
            rewriteReason="测试注入",
            evidenceIds=[],
            confidence=0.1,
            riskFlags=["INJECTION_RISK"],
            status="DRAFT",
        ),
    ]

    result = validate_resume_patches("tpl-2", 1, parsed.fields, patches, allowed_evidence_ids=[])

    assert not result.patches
    assert "不存在对应字段" in "；".join(result.validationErrors)
    assert "原文字段 hash 不匹配" in "；".join(result.validationErrors)
    assert "注入风险" in "；".join(result.validationErrors)


def test_apply_rejects_cross_run_field_to_protect_layout():
    """跨 run 字段首版拒绝自动替换，避免清空 follower run 改变结构语义。"""
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("项目：")
    paragraph.add_run("RAG 平台")
    content = docx_bytes(document)
    parsed = parse_resume_template_docx(content, "resume.docx", template_id="tpl-3")

    assert parsed.fields == []


def test_export_rejects_when_source_hash_changed():
    """导出时如果当前 DOCX 字段文本已变化，返回版本冲突式错误。"""
    document = Document()
    document.add_paragraph("原始项目经历")
    content = docx_bytes(document)
    parsed = parse_resume_template_docx(content, "resume.docx", template_id="tpl-4")
    field = parsed.fields[0]
    patch = ResumeContentPatch(
        fieldId=field.fieldId,
        sourceTextHash=field.sourceTextHash,
        newText="确认后的项目经历",
        rewriteReason="用户确认改写",
        evidenceIds=["e1"],
        confidence=0.9,
        riskFlags=["NONE"],
        status="CONFIRMED",
    )
    changed = Document(io.BytesIO(content))
    changed.paragraphs[0].runs[0].text = "用户已经手动改过"

    with pytest.raises(ValueError, match="hash 已变化"):
        apply_resume_patches_to_docx(docx_bytes(changed), "resume.docx", "tpl-4", 1, parsed.fields, [patch], allowed_evidence_ids=["e1"])


def test_unsupported_hyperlink_region_blocks_export(tmp_path: Path):
    """包含超链接关系的 DOCX 会进入 unsupportedRegions，并拒绝导出。"""
    document = Document()
    document.add_paragraph("项目经历")
    content = add_hyperlink_xml(docx_bytes(document), tmp_path)
    parsed = parse_resume_template_docx(content, "resume.docx", template_id="tpl-5")

    assert any("超链接" in item for item in parsed.unsupportedRegions)
    with pytest.raises(ValueError, match="复杂结构"):
        apply_resume_patches_to_docx(content, "resume.docx", "tpl-5", 1, parsed.fields, [])


def test_local_patch_generation_returns_strict_schema_and_safe_drafts():
    """无模型环境下返回严格 schema 和保留原文的安全草稿。"""
    document = Document()
    document.add_paragraph("技能：RAG、BM25、FastAPI")
    parsed = parse_resume_template_docx(docx_bytes(document), "resume.docx", template_id="tpl-6")
    response = generate_resume_patches(
        ResumePatchGenerationRequest.model_validate({
            "templateId": "tpl-6",
            "version": 1,
            "jobDescription": "需要 RAG 和 FastAPI 经验",
            "fields": [field.model_dump() for field in parsed.fields],
            "evidenceCandidates": [],
            "provider": "local",
        })
    )
    schema = resume_patch_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["patches"]["items"]["additionalProperties"] is False
    assert response.provider == "local"
    assert response.patches[0].newText == parsed.fields[0].sourceText


def add_hyperlink_xml(content: bytes, tmp_path: Path) -> bytes:
    """直接在 document.xml 注入 hyperlink，用于测试复杂结构检测。"""
    source = tmp_path / "source.docx"
    target = tmp_path / "target.docx"
    source.write_bytes(content)
    with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(target, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                text = data.decode("utf-8")
                text = text.replace("<w:t>项目经历</w:t>", "<w:hyperlink><w:r><w:t>项目经历</w:t></w:r></w:hyperlink>")
                data = text.encode("utf-8")
            zout.writestr(item, data)
    return target.read_bytes()
