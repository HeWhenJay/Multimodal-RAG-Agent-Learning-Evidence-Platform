import json

from app.schemas.rag import DocumentBlock
from rag.loaders.document_parsers import DocumentParserRouter
from video.correction.recognition_text_correction import (
    BailianRecognitionTextCorrector,
    CorrectionBatchResult,
    RecognitionTextItem,
    correct_recognition_blocks,
)
from video.ocr.bailian_ocr import OcrResult


class FakeCorrectionResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {"blockId": "asr-1", "correctedText": "这里使用向量召回。"},
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }


class FakeHttpClient:
    def __init__(self):
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeCorrectionResponse()


class MappingCorrector:
    available = True
    enabled = True
    skip_reason = ""
    max_batch_items = 32
    max_batch_chars = 12000

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def correct_batch(self, items: list[RecognitionTextItem]) -> CorrectionBatchResult:
        self.calls.append(items)
        return CorrectionBatchResult(
            corrected_texts={item.block_id: self.mapping.get(item.block_id, item.text) for item in items},
            provider="fake",
            model="fake-corrector",
        )


class FailingCorrector(MappingCorrector):
    def correct_batch(self, items: list[RecognitionTextItem]) -> CorrectionBatchResult:
        raise TimeoutError("模拟纠错超时")


def make_block(
    block_id: str,
    content: str,
    *,
    parse_engine: str,
    block_type: str = "text",
    metadata=None,
) -> DocumentBlock:
    return DocumentBlock(
        documentId="doc-correction",
        blockId=block_id,
        fileType="mp4",
        blockType=block_type,
        contentText=content,
        parseEngine=parse_engine,
        sourceTitle="纠错测试",
        metadata=metadata or {},
    )


def test_bailian_correction_client_builds_batch_json_request():
    """纠错客户端应批量发送通道和 blockId，并解析严格 JSON 响应。"""
    http_client = FakeHttpClient()
    corrector = BailianRecognitionTextCorrector(
        api_key="test-key",
        enabled=True,
        model="qwen-plus",
        http_client=http_client,
    )

    result = corrector.correct_batch(
        [RecognitionTextItem(block_id="asr-1", channel="asr", text="这里使用向量索回。")]
    )

    assert result.corrected_texts == {"asr-1": "这里使用向量召回。"}
    assert result.provider == "dashscope"
    call = http_client.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["json"]["temperature"] == 0
    assert call["json"]["response_format"] == {"type": "json_object"}
    assert "向量索回" in call["json"]["messages"][1]["content"]
    assert "不得总结" in call["json"]["messages"][0]["content"]


def test_correction_node_updates_asr_and_ocr_while_preserving_original_evidence():
    """ASR 与 OCR 应在同一节点纠错，并保留原文、时间标题和模型诊断。"""
    blocks = [
        make_block(
            "asr-1",
            "这里使用向量索回。",
            parse_engine="bailian-asr-transcript",
            metadata={"evidenceChannel": "subtitle", "startTime": "00:00:01"},
        ),
        make_block(
            "ocr-1",
            "视频画面 00:00:05\n这是知是库架构图。",
            parse_engine="bailian-qwen-ocr",
            block_type="image",
            metadata={"evidenceChannel": "frame_ocr", "frameTime": "00:00:05"},
        ),
        make_block("native-1", "原生 PDF 文本", parse_engine="pymupdf"),
    ]
    corrector = MappingCorrector(
        {
            "asr-1": "这里使用向量召回。",
            "ocr-1": "这是知识库架构图。",
        }
    )

    corrected, warnings = correct_recognition_blocks(blocks, corrector=corrector)

    assert warnings == []
    assert len(corrector.calls) == 1
    assert corrected[0].contentText == "这里使用向量召回。"
    assert corrected[0].metadata["originalContentText"] == "这里使用向量索回。"
    assert corrected[0].metadata["correctionStatus"] == "applied"
    assert corrected[0].parseEngine.endswith("+text-correction")
    assert corrected[1].contentText == "视频画面 00:00:05\n这是知识库架构图。"
    assert corrected[1].metadata["originalContentText"] == "视频画面 00:00:05\n这是知是库架构图。"
    assert corrected[1].metadata["frameTime"] == "00:00:05"
    assert corrected[2] == blocks[2]


def test_correction_node_rejects_model_rewrite_and_keeps_raw_text():
    """模型大幅改写或扩写时应拒绝结果，避免污染可追溯 evidence。"""
    block = make_block(
        "asr-rewrite",
        "今天讲知识库。",
        parse_engine="bailian-asr-transcript",
        metadata={"evidenceChannel": "subtitle"},
    )
    corrector = MappingCorrector(
        {"asr-rewrite": "本文全面总结了企业知识库建设的背景、方案、风险以及未来规划。"}
    )

    corrected, warnings = correct_recognition_blocks([block], corrector=corrector)

    assert warnings == []
    assert corrected[0].contentText == block.contentText
    assert corrected[0].metadata["correctionStatus"] == "rejected"
    assert corrected[0].metadata["correctionApplied"] is False


def test_correction_node_falls_back_to_original_when_model_fails():
    """纠错模型超时不应中断解析，应保留原识别结果并产生可诊断告警。"""
    block = make_block(
        "asr-timeout",
        "这里使用向量索回。",
        parse_engine="bailian-asr-transcript",
        metadata={"evidenceChannel": "subtitle"},
    )

    corrected, warnings = correct_recognition_blocks([block], corrector=FailingCorrector({}))

    assert corrected[0].contentText == block.contentText
    assert corrected[0].metadata["correctionStatus"] == "failed"
    assert "TimeoutError" in warnings[0]


def test_document_parser_runs_correction_after_image_ocr():
    """图片 OCR 结果应在资料摘要和最终切块前经过纠错节点。"""
    class FakeOcrClient:
        enabled = True

        def recognize_image_bytes(self, *, image_bytes, filename, mime_type=None, prompt=None):
            return OcrResult(
                text="这是知是库架构图。",
                parser="bailian-qwen-ocr",
                confidence=0.93,
            )

    parser = DocumentParserRouter(
        ocr_client=FakeOcrClient(),
        text_corrector=MappingCorrector({"doc-image-image-1": "这是知识库架构图。"}),
    )

    parsed = parser.parse_bytes(
        content=b"fake-image",
        filename="architecture.png",
        document_id="doc-image",
        source_title="架构图",
        document_type="png",
    )

    assert parsed.blocks[0].contentText == "这是知识库架构图。"
    assert parsed.blocks[0].metadata["originalContentText"] == "这是知是库架构图。"
    assert parsed.parser == "bailian-qwen-ocr+text-correction"
