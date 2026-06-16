from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field


ParseStatus = Literal["PENDING", "PARSING", "READY", "PARTIAL", "FAILED", "REINDEXING"]
BlockType = Literal["heading", "text", "table", "image", "chart", "formula", "code", "list"]


class DocumentBlock(BaseModel):
    documentId: str = Field(validation_alias=AliasChoices("documentId", "document_id"))
    blockId: str = Field(validation_alias=AliasChoices("blockId", "block_id"))
    fileType: str = Field(validation_alias=AliasChoices("fileType", "file_type"))
    blockType: BlockType = Field(validation_alias=AliasChoices("blockType", "block_type"))
    pageIndex: int | None = Field(default=None, validation_alias=AliasChoices("pageIndex", "page_index"))
    slideIndex: int | None = Field(default=None, validation_alias=AliasChoices("slideIndex", "slide_index"))
    sheetName: str | None = Field(default=None, validation_alias=AliasChoices("sheetName", "sheet_name"))
    cellRange: str | None = Field(default=None, validation_alias=AliasChoices("cellRange", "cell_range"))
    sectionTitle: str | None = Field(default=None, validation_alias=AliasChoices("sectionTitle", "section_title"))
    contentText: str = Field(default="", validation_alias=AliasChoices("contentText", "content_text"))
    contentHtml: str | None = Field(default=None, validation_alias=AliasChoices("contentHtml", "content_html"))
    assetPath: str | None = Field(default=None, validation_alias=AliasChoices("assetPath", "asset_path"))
    bbox: list[float] | None = None
    parseEngine: str = Field(validation_alias=AliasChoices("parseEngine", "parse_engine"))
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sourceTitle: str = Field(validation_alias=AliasChoices("sourceTitle", "source_title"))
    sourcePath: str | None = Field(default=None, validation_alias=AliasChoices("sourcePath", "source_path"))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseQuality(BaseModel):
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    nativeTextChars: int = 0
    paragraphCount: int = 0
    tableCount: int = 0
    imageCount: int = 0
    shapeCount: int = 0
    textBoxCount: int = 0
    drawingCount: int = 0
    embeddedObjectCount: int = 0
    mergedCellCount: int = 0
    emptyCellRatio: float = Field(default=0.0, ge=0.0, le=1.0)
    screenshotLike: bool = False
    highPrecision: bool = False
    needsSupplement: bool = False
    messages: list[str] = Field(default_factory=list)


class IndexTextRequest(BaseModel):
    documentId: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    documentType: str = "text"
    source: str = "manual"
    userId: str = "demo-user"
    visibilityScope: str = "private"
    language: str = "zh-CN"
    parser: str = "manual-text"
    sourcePath: str | None = None
    content: str = Field(..., min_length=1)


class IndexResponse(BaseModel):
    documentId: str
    title: str
    status: ParseStatus
    chunkCount: int
    parser: str
    documentSummary: str
    parseQuality: ParseQuality = Field(default_factory=ParseQuality)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    topK: int = Field(default=5, ge=1, le=20)
    metadataFilter: dict[str, Any] | None = None


class Evidence(BaseModel):
    evidenceId: str = Field(validation_alias=AliasChoices("evidenceId", "evidence_id"))
    documentId: str = Field(validation_alias=AliasChoices("documentId", "document_id"))
    documentTitle: str = Field(validation_alias=AliasChoices("documentTitle", "document_title"))
    blockId: str | None = Field(default=None, validation_alias=AliasChoices("blockId", "block_id"))
    blockType: str | None = Field(default=None, validation_alias=AliasChoices("blockType", "block_type"))
    pageIndex: int | None = Field(default=None, validation_alias=AliasChoices("pageIndex", "page_index"))
    slideIndex: int | None = Field(default=None, validation_alias=AliasChoices("slideIndex", "slide_index"))
    sheetName: str | None = Field(default=None, validation_alias=AliasChoices("sheetName", "sheet_name"))
    cellRange: str | None = Field(default=None, validation_alias=AliasChoices("cellRange", "cell_range"))
    sectionTitle: str | None = Field(default=None, validation_alias=AliasChoices("sectionTitle", "section_title"))
    title: str
    snippet: str
    source: str
    sourcePath: str | None = Field(default=None, validation_alias=AliasChoices("sourcePath", "source_path"))
    assetPath: str | None = Field(default=None, validation_alias=AliasChoices("assetPath", "asset_path"))
    sectionName: str
    documentType: str
    score: float
    retrievalSource: Literal["bm25", "vector", "summary", "fusion"] = Field(
        default="fusion",
        validation_alias=AliasChoices("retrievalSource", "retrieval_source"),
    )
    parseEngine: str | None = Field(default=None, validation_alias=AliasChoices("parseEngine", "parse_engine"))
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceListResponse(BaseModel):
    documentId: str
    evidences: list[Evidence]


class QueryResponse(BaseModel):
    answer: str
    expandedQueries: list[str]
    evidences: list[Evidence]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class OverviewResponse(BaseModel):
    documentCount: int
    chunkCount: int
    evidenceCount: int
    lastIndexedTitle: str | None = None
