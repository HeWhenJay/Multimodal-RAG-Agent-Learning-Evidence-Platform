from __future__ import annotations

from dataclasses import dataclass

from app.schemas.rag import ParseQuality


@dataclass
class QualitySignals:
    native_text_chars: int = 0
    paragraph_count: int = 0
    table_count: int = 0
    image_count: int = 0
    shape_count: int = 0
    text_box_count: int = 0
    drawing_count: int = 0
    embedded_object_count: int = 0
    merged_cell_count: int = 0
    empty_cell_ratio: float = 0.0


def evaluate_parse_quality(signals: QualitySignals, high_precision: bool = False) -> ParseQuality:
    """Score native parser output and decide whether PDF/MinerU/OCR supplement is needed."""
    score = 1.0
    messages: list[str] = []

    if signals.native_text_chars < 80:
        score -= 0.35
        messages.append("native text is sparse")
    elif signals.native_text_chars < 300:
        score -= 0.15
        messages.append("native text is short")

    if signals.paragraph_count == 0 and signals.table_count == 0:
        score -= 0.25
        messages.append("no paragraph or table block")

    media_count = signals.image_count + signals.shape_count + signals.drawing_count
    screenshot_like = signals.native_text_chars < 120 and media_count >= 1
    if screenshot_like:
        score -= 0.3
        messages.append("document looks like screenshot or image-heavy file")

    if signals.empty_cell_ratio >= 0.75:
        score -= 0.15
        messages.append("spreadsheet has high empty cell ratio")

    if signals.embedded_object_count > 0:
        score -= 0.08
        messages.append("embedded objects may need layout parser")

    if high_precision:
        messages.append("high precision parse requested")

    score = max(0.0, min(1.0, score))
    needs_supplement = high_precision or screenshot_like or score < 0.68
    return ParseQuality(
        score=round(score, 4),
        nativeTextChars=signals.native_text_chars,
        paragraphCount=signals.paragraph_count,
        tableCount=signals.table_count,
        imageCount=signals.image_count,
        shapeCount=signals.shape_count,
        textBoxCount=signals.text_box_count,
        drawingCount=signals.drawing_count,
        embeddedObjectCount=signals.embedded_object_count,
        mergedCellCount=signals.merged_cell_count,
        emptyCellRatio=round(max(0.0, min(1.0, signals.empty_cell_ratio)), 4),
        screenshotLike=screenshot_like,
        highPrecision=high_precision,
        needsSupplement=needs_supplement,
        messages=messages,
    )


def merge_quality(primary: ParseQuality, supplement: ParseQuality | None = None) -> ParseQuality:
    if supplement is None:
        return primary
    messages = list(dict.fromkeys([*primary.messages, *supplement.messages]))
    return ParseQuality(
        score=max(primary.score, supplement.score),
        nativeTextChars=primary.nativeTextChars + supplement.nativeTextChars,
        paragraphCount=primary.paragraphCount + supplement.paragraphCount,
        tableCount=primary.tableCount + supplement.tableCount,
        imageCount=primary.imageCount + supplement.imageCount,
        shapeCount=primary.shapeCount + supplement.shapeCount,
        textBoxCount=primary.textBoxCount + supplement.textBoxCount,
        drawingCount=primary.drawingCount + supplement.drawingCount,
        embeddedObjectCount=primary.embeddedObjectCount + supplement.embeddedObjectCount,
        mergedCellCount=primary.mergedCellCount + supplement.mergedCellCount,
        emptyCellRatio=max(primary.emptyCellRatio, supplement.emptyCellRatio),
        screenshotLike=primary.screenshotLike or supplement.screenshotLike,
        highPrecision=primary.highPrecision or supplement.highPrecision,
        needsSupplement=False,
        messages=messages,
    )
