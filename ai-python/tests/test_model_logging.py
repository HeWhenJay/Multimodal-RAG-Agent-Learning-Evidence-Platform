from rag.observability.model_logging import log_model_call
from rag.observability.process_logger import RagProcessLogger, use_process_logger


def test_model_call_logs_before_and_after(capsys):
    process_logger = RagProcessLogger(document_id="material-model-log", persist=False)

    with use_process_logger(process_logger):
        with log_model_call(
            stage="query.answer",
            action="unit_model",
            model_name="qwen-plus",
            event="测试回答生成",
        ):
            pass

    output = capsys.readouterr().out
    assert "目前在使用 qwen-plus 模型完成测试回答生成事件" in output
    assert "已使用 qwen-plus 模型完成测试回答生成事件" in output


def test_recoverable_model_failure_logs_degraded_warning(capsys):
    process_logger = RagProcessLogger(document_id="material-model-log", persist=False)

    with use_process_logger(process_logger):
        try:
            with log_model_call(
                stage="parse.video.ocr",
                action="bailian_ocr",
                model_name="qwen3.5-ocr",
                event="图片或关键帧 OCR 识别",
                recoverable=True,
                fallback_message="使用 qwen3.5-ocr 模型完成图片或关键帧 OCR 识别事件失败，已降级继续处理",
            ):
                raise RuntimeError("HTTP 500")
        except RuntimeError:
            pass

    output = capsys.readouterr().out
    assert "action=bailian_ocr_model_degraded" in output
    assert "level=WARN" in output
    assert "success=true" in output
    assert "已降级继续处理" in output
    assert "action=bailian_ocr_model_failed" not in output
    assert "level=ERROR" not in output
