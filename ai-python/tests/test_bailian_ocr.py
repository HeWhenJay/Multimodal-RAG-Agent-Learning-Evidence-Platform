from video.ocr.bailian_ocr import BailianOcrClient, OcrResult
from rag.loaders.document_parsers import DocumentParserRouter
from rag.process_logger import RagProcessLogger, use_process_logger


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


class SequenceHttpClient:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def post(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


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


def test_bailian_ocr_request_failure_is_recoverable_warning(capsys):
    class FailingHttpClient:
        def post(self, url, headers, json, timeout):
            raise RuntimeError("network down")

    client = BailianOcrClient(
        api_key="test-api-key",
        enabled=True,
        model="qwen3.5-ocr",
        max_attempts=1,
        http_client=FailingHttpClient(),
    )
    process_logger = RagProcessLogger(document_id="material-ocr-warning", persist=False)

    with use_process_logger(process_logger):
        result = client.recognize_image_bytes(image_bytes=b"image-bytes", filename="frame-0115.jpg")

    output = capsys.readouterr().out
    assert result.text == ""
    assert any("Bailian OCR request failed" in warning for warning in result.warnings)
    assert "action=bailian_ocr_model_degraded" in output
    assert "level=WARN" in output
    assert "success=true" in output
    assert "frame-0115.jpg" in output
    assert "action=bailian_ocr_model_failed" not in output
    assert "level=ERROR" not in output


def test_bailian_ocr_http_failure_does_not_log_model_after(capsys):
    class ErrorResponse:
        status_code = 500
        text = "server error"
        headers = {}

    class ErrorHttpClient:
        def post(self, url, headers, json, timeout):
            return ErrorResponse()

    client = BailianOcrClient(
        api_key="test-api-key",
        enabled=True,
        model="qwen3.5-ocr",
        max_attempts=1,
        http_client=ErrorHttpClient(),
    )
    process_logger = RagProcessLogger(document_id="material-ocr-http-warning", persist=False)

    with use_process_logger(process_logger):
        result = client.recognize_image_bytes(image_bytes=b"image-bytes", filename="frame-0115.jpg")

    output = capsys.readouterr().out
    assert result.text == ""
    assert "Bailian OCR exhausted retries for frame-0115.jpg using qwen3.5-ocr" in result.warnings[0]
    assert "Bailian OCR returned HTTP 500" in result.warnings[0]
    assert "action=bailian_ocr_model_degraded" in output
    assert "action=bailian_ocr_model_after" not in output


def test_bailian_ocr_retries_before_success(capsys):
    retry_events = []
    http_client = SequenceHttpClient([RuntimeError("timeout-1"), RuntimeError("timeout-2"), FakeResponse()])
    client = BailianOcrClient(
        api_key="test-api-key",
        enabled=True,
        model="qwen3.5-ocr",
        max_attempts=3,
        retry_delay_seconds=0,
        http_client=http_client,
    )
    process_logger = RagProcessLogger(document_id="material-ocr-retry", persist=False)

    with use_process_logger(process_logger):
        result = client.recognize_image_bytes(
            image_bytes=b"image-bytes",
            filename="frame-0115.jpg",
            retry_callback=retry_events.append,
        )

    output = capsys.readouterr().out
    assert result.text == "标题\n\n正文"
    assert len(http_client.calls) == 3
    assert [event["attempt"] for event in retry_events] == [1, 2]
    assert retry_events[0]["nextAttempt"] == 2
    assert retry_events[1]["nextAttempt"] == 3
    assert "第 1/3 次 OCR 失败" in output
    assert "准备重试第 2 次" in output
    assert "action=bailian_ocr_model_after" in output


def test_bailian_ocr_exhausts_retries_before_degrade(capsys):
    retry_events = []
    http_client = SequenceHttpClient([RuntimeError("timeout-1"), RuntimeError("timeout-2"), RuntimeError("timeout-3")])
    client = BailianOcrClient(
        api_key="test-api-key",
        enabled=True,
        model="qwen3.5-ocr",
        max_attempts=3,
        retry_delay_seconds=0,
        http_client=http_client,
    )
    process_logger = RagProcessLogger(document_id="material-ocr-retry-failed", persist=False)

    with use_process_logger(process_logger):
        result = client.recognize_image_bytes(
            image_bytes=b"image-bytes",
            filename="frame-0115.jpg",
            retry_callback=retry_events.append,
        )

    output = capsys.readouterr().out
    assert result.text == ""
    assert len(http_client.calls) == 3
    assert [event["attempt"] for event in retry_events] == [1, 2, 3]
    assert retry_events[-1]["nextAttempt"] is None
    assert "已达到最大重试次数，等待降级处理" in output
    assert "Bailian OCR exhausted retries for frame-0115.jpg using qwen3.5-ocr" in result.warnings[0]


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
