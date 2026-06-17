from pathlib import Path

from rag.bailian_asr import BailianAsrClient, milliseconds_to_srt_timestamp, transcription_json_to_srt


def test_filetrans_result_converts_to_srt(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")
    captured = {}

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code
            self.text = ""

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, headers, json):
            captured["post_url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse({"output": {"task_id": "task-1"}})

        def get(self, url, headers=None):
            if url.endswith("/tasks/task-1"):
                return FakeResponse(
                    {
                        "output": {
                            "task_status": "SUCCEEDED",
                            "result": {"transcription_url": "https://example.com/asr-result.json"},
                        }
                    }
                )
            return FakeResponse(
                {
                    "transcripts": [
                        {
                            "sentences": [
                                {"begin_time": 100, "end_time": 3820, "text": "这里讲到了 RAG-Fusion。"}
                            ]
                        }
                    ]
                }
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = BailianAsrClient(
        api_key="test-key",
        provider="dashscope_filetrans",
        max_polls=1,
        poll_interval_seconds=0,
    )

    transcript, warnings = client.transcribe_audio_file(audio_path, source_url="https://example.com/course.mp4")

    assert warnings == []
    assert "00:00:00,100 --> 00:00:03,820" in transcript
    assert "这里讲到了 RAG-Fusion。" in transcript
    assert captured["headers"]["X-DashScope-Async"] == "enable"
    assert captured["json"]["model"] == "qwen3-asr-flash-filetrans"
    assert captured["json"]["input"]["file_url"] == "https://example.com/course.mp4"


def test_transcription_json_to_srt_requires_timestamped_sentences():
    srt = transcription_json_to_srt(
        {
            "transcripts": [
                {
                    "sentences": [
                        {"begin_time": 0, "end_time": 1500, "text": "第一句"},
                        {"begin_time": 1600, "end_time": 3000, "text": "第二句"},
                    ]
                }
            ]
        }
    )

    assert "1\n00:00:00,000 --> 00:00:01,500\n第一句" in srt
    assert "2\n00:00:01,600 --> 00:00:03,000\n第二句" in srt


def test_milliseconds_to_srt_timestamp_formats_hours():
    assert milliseconds_to_srt_timestamp(3_661_042) == "01:01:01,042"
