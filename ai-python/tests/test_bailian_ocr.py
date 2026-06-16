from rag.bailian_ocr import BailianOcrClient, OcrResult
from rag.document_parsers import DocumentParserRouter


class FakeResponse:
    status_code = 200
    headers = {"X-DashScope-Request-Id": "req-test"}

    def json(self):
        return {"choices": [{"message": {"content": "  标题\n\n\n正文  "}}]}


class FakeHttpClient:
    def __init__(self):
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()


def test_bailian_ocr_client_builds_openai_compatible_request():
    http_client = FakeHttpClient()
    client = BailianOcrClient(
        api_key="test-api-key",
        enabled=True,
        model="qwen3.5-ocr",
        http_client=http_client,
    )

    result = client.recognize_image_bytes(
        image_bytes=b"image-bytes",
        filename="scan.png",
        mime_type="image/png",
    )

    assert result.text == "标题\n\n正文"
    assert result.parser == "bailian-qwen-ocr"
    assert result.metadata["ocrModel"] == "qwen3.5-ocr"
    assert result.metadata["requestId"] == "req-test"
    assert len(http_client.calls) == 1
    call = http_client.calls[0]
    assert call["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-api-key"
    assert call["json"]["model"] == "qwen3.5-ocr"
    assert "test-api-key" not in str(call["json"])
    image_part = call["json"]["messages"][0]["content"][0]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_bailian_ocr_client_uses_dashscope_api_key(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-key")
    client = BailianOcrClient.from_env()

    assert client.api_key == "dashscope-test-key"
    assert client.enabled is True


class FakeOcrClient:
    enabled = True

    def __init__(self):
        self.calls = []

    def recognize_image_bytes(self, *, image_bytes, filename, mime_type=None, prompt=None):
        self.calls.append({"image_bytes": image_bytes, "filename": filename, "mime_type": mime_type})
        return OcrResult(
            text="百炼 OCR 识别文本",
            parser="bailian-qwen-ocr",
            confidence=0.93,
            metadata={"ocrModel": "fake-ocr"},
        )


def test_image_parser_prefers_bailian_ocr_document_block():
    fake_ocr = FakeOcrClient()
    parser = DocumentParserRouter(ocr_client=fake_ocr)

    parsed = parser.parse_bytes(
        content=b"fake-image-content",
        filename="ocr.png",
        document_id="doc-ocr",
        source_title="OCR 图片",
        document_type="png",
        source_path="uploads/rag/ocr.png",
    )

    assert parsed.status == "READY"
    assert parsed.parser == "bailian-qwen-ocr"
    assert fake_ocr.calls[0]["filename"] == "ocr.png"
    assert parsed.blocks[0].contentText == "百炼 OCR 识别文本"
    assert parsed.blocks[0].parseEngine == "bailian-qwen-ocr"
    assert parsed.blocks[0].metadata["ocrModel"] == "fake-ocr"
