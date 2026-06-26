from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SectionKey = Literal[
    "personal_info",
    "summary",
    "education",
    "work_experience",
    "project_experience",
    "skills",
    "awards",
    "certifications",
    "research",
    "other",
]
EvidencePolicy = Literal["NONE", "OPTIONAL", "REQUIRED"]
PreviewStatus = Literal["READY", "PARTIAL", "UNAVAILABLE", "UNMAPPED"]
RiskFlag = Literal[
    "NONE",
    "MISSING_EVIDENCE",
    "LOW_CONFIDENCE",
    "OVER_LENGTH",
    "LAYOUT_RISK",
    "SENSITIVE_INFO",
    "UNSUPPORTED_REGION",
    "INJECTION_RISK",
]
PatchStatus = Literal["DRAFT", "VALIDATED", "CONFIRMED", "REJECTED", "EXPORTED"]
LayoutEditMode = Literal["PRESERVE_LAYOUT", "CONTROLLED_EDIT", "RELAYOUT"]
LayoutOperationType = Literal["TEXT_REPLACE", "STYLE_RANGE", "INSERT_PARAGRAPH", "DELETE_PARAGRAPH"]


class StrictModel(BaseModel):
    """统一禁止模型输出或接口输入携带未声明字段。"""

    model_config = ConfigDict(extra="forbid")


class ResumeTemplateLocationRef(StrictModel):
    partName: str = Field(..., min_length=1)
    containerType: Literal["paragraph", "table_cell"]
    paragraphIndex: int = Field(..., ge=0)
    tableIndex: int | None = Field(default=None, ge=0)
    rowIndex: int | None = Field(default=None, ge=0)
    cellIndex: int | None = Field(default=None, ge=0)
    runStart: int = Field(..., ge=0)
    runEnd: int = Field(..., ge=0)
    textStart: int = Field(default=0, ge=0)
    textEnd: int = Field(..., ge=0)


class ResumeTemplateBinding(StrictModel):
    templateId: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    fieldId: str = Field(..., min_length=1)
    sectionKey: SectionKey
    displayName: str = Field(..., min_length=1)
    sourceText: str
    sourceTextHash: str = Field(..., min_length=16)
    locationRefs: list[ResumeTemplateLocationRef] = Field(..., min_length=1)
    styleFingerprint: dict[str, Any]
    maxChars: int = Field(..., ge=1, le=2000)
    maxLines: int = Field(..., ge=1, le=20)
    requiredEvidencePolicy: EvidencePolicy
    unsupportedRegions: list[str] = Field(default_factory=list)


class ResumePatchEvidence(StrictModel):
    evidenceId: str = Field(..., min_length=1)
    documentTitle: str = ""
    sectionName: str = ""
    snippet: str = ""
    source: str = ""
    score: float = 0.0


class ResumeContentPatch(StrictModel):
    fieldId: str = Field(..., min_length=1)
    sourceTextHash: str = Field(..., min_length=16)
    newText: str = Field(..., min_length=0, max_length=2000)
    rewriteReason: str = Field(..., min_length=1, max_length=500)
    evidenceIds: list[str]
    confidence: float = Field(..., ge=0.0, le=1.0)
    riskFlags: list[RiskFlag]
    status: PatchStatus


class LayoutAllowedChange(StrictModel):
    """描述用户显式授权的版式变化范围。"""

    type: LayoutOperationType
    fieldId: str | None = None
    sectionKey: SectionKey | None = None
    textRange: str | None = Field(default=None, max_length=200)
    stylePatch: dict[str, Any] = Field(default_factory=dict)
    maxParagraphs: int = Field(default=0, ge=0, le=20)
    styleSource: Literal["current_run", "previous_paragraph", "section_default"] = "current_run"


class LayoutChangeContract(StrictModel):
    """定义本次简历导出允许发生的版式差异。"""

    mode: LayoutEditMode = "PRESERVE_LAYOUT"
    allowedChanges: list[LayoutAllowedChange] = Field(default_factory=list)
    maxPageDelta: int = Field(default=0, ge=0, le=5)
    maxParagraphDelta: int = Field(default=0, ge=0, le=50)
    maxRunDelta: int = Field(default=0, ge=0, le=200)
    requireVisualCheck: bool = True


class ResumePatchGenerationRequest(StrictModel):
    templateId: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    jobDescription: str = Field(..., min_length=1)
    resumeText: str = Field(default="", max_length=8000)
    fields: list[ResumeTemplateBinding] = Field(..., min_length=1)
    evidenceCandidates: list[ResumePatchEvidence] = Field(default_factory=list)
    provider: Literal["auto", "openai", "dashscope", "local"] = "auto"
    fieldInstructions: dict[str, str] = Field(default_factory=dict)
    fieldEvidencePolicies: dict[str, EvidencePolicy] = Field(default_factory=dict)


class ResumePatchGenerationResponse(StrictModel):
    templateId: str
    version: int
    provider: str
    schemaName: str
    strictSchema: dict[str, Any]
    patches: list[ResumeContentPatch]
    validationErrors: list[str] = Field(default_factory=list)


class ResumePatchValidationRequest(StrictModel):
    templateId: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    fields: list[ResumeTemplateBinding] = Field(..., min_length=1)
    patches: list[ResumeContentPatch] = Field(..., min_length=1)
    allowedEvidenceIds: list[str] = Field(default_factory=list)
    layoutContract: LayoutChangeContract = Field(default_factory=LayoutChangeContract)


class ResumePatchValidationResponse(StrictModel):
    templateId: str
    version: int
    patches: list[ResumeContentPatch]
    validationErrors: list[str] = Field(default_factory=list)
    layoutValidation: dict[str, Any] = Field(default_factory=dict)


class ResumeTemplateParseResponse(StrictModel):
    templateId: str
    version: int
    filename: str
    fields: list[ResumeTemplateBinding]
    unsupportedRegions: list[str] = Field(default_factory=list)
    layoutFingerprint: dict[str, Any]


class ResumeTemplatePreviewRect(StrictModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    width: float = Field(..., gt=0.0, le=1.0)
    height: float = Field(..., gt=0.0, le=1.0)


class ResumeTemplatePreviewPage(StrictModel):
    pageIndex: int = Field(..., ge=0)
    width: int = Field(..., ge=1)
    height: int = Field(..., ge=1)
    imageBase64: str = Field(..., min_length=1)
    imageMimeType: str = "image/png"


class ResumeTemplatePreviewRegion(StrictModel):
    fieldId: str = Field(..., min_length=1)
    displayName: str = Field(..., min_length=1)
    sectionKey: SectionKey
    sourceTextHash: str = Field(..., min_length=16)
    pageIndex: int = Field(..., ge=0)
    rect: ResumeTemplatePreviewRect
    confidence: float = Field(..., ge=0.0, le=1.0)
    previewStatus: PreviewStatus = "READY"


class ResumeTemplatePreviewRequest(StrictModel):
    templateId: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    filename: str = Field(..., min_length=1)
    fileBase64: str = Field(..., min_length=1)
    fields: list[ResumeTemplateBinding] = Field(..., min_length=1)


class ResumeTemplatePreviewResponse(StrictModel):
    templateId: str
    version: int
    previewStatus: Literal["READY", "PARTIAL", "UNAVAILABLE"]
    pages: list[ResumeTemplatePreviewPage] = Field(default_factory=list)
    regions: list[ResumeTemplatePreviewRegion] = Field(default_factory=list)
    unmappedFields: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generatedAt: str


class ResumeTemplateExportResponse(StrictModel):
    templateId: str
    version: int
    filename: str
    fileBase64: str
    layoutValidation: dict[str, Any]
    appliedPatchCount: int


class ResumeTemplateExportRequest(StrictModel):
    templateId: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    filename: str = Field(..., min_length=1)
    fileBase64: str = Field(..., min_length=1)
    fields: list[ResumeTemplateBinding] = Field(..., min_length=1)
    patches: list[ResumeContentPatch] = Field(..., min_length=1)
    allowedEvidenceIds: list[str] = Field(default_factory=list)
    layoutContract: LayoutChangeContract = Field(default_factory=LayoutChangeContract)
