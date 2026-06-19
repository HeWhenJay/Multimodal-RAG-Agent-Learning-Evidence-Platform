from pathlib import Path

from video.asr.bailian_asr import BailianAsrClient, milliseconds_to_srt_timestamp, transcription_json_to_srt
from video.chunking.video_processing import (
    AudioSegment,
    TranscriptCue,
    cue_center_in_segment,
    estimate_srt_from_transcript,
    merge_transcript_cues,
    transcript_has_timestamps,
)


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


def test_filetrans_reports_poll_progress(monkeypatch):
    events = []

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code
            self.text = ""

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, timeout):
            self.poll_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, headers, json):
            return FakeResponse({"output": {"task_id": "task-progress"}})

        def get(self, url, headers=None):
            if url.endswith("/tasks/task-progress"):
                self.poll_count += 1
                status = "RUNNING" if self.poll_count == 1 else "SUCCEEDED"
                return FakeResponse(
                    {
                        "output": {
                            "task_status": status,
                            "result": {"transcription_url": "https://example.com/asr-result.json"},
                        }
                    }
                )
            return FakeResponse(
                {
                    "transcripts": [
                        {"sentences": [{"begin_time": 0, "end_time": 1000, "text": "进度测试"}]}
                    ]
                }
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = BailianAsrClient(
        api_key="test-key",
        provider="dashscope_filetrans",
        max_polls=3,
        poll_interval_seconds=0,
    )

    transcript, warnings = client.transcribe_source_url("https://example.com/course.mp4", progress_callback=events.append)

    assert warnings == []
    assert "进度测试" in transcript
    assert any(event.get("phase") == "submitted" for event in events)
    assert any(event.get("phase") == "poll" and event.get("taskStatus") == "RUNNING" for event in events)
    assert any(event.get("phase") == "download" for event in events)


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


def test_estimate_srt_from_plain_transcript_creates_timestamp_ranges():
    srt = estimate_srt_from_transcript("第一段讲 RAG。第二段讲 OCR。", 20)

    assert transcript_has_timestamps(srt)
    assert "00:00:00,000 --> 00:00:10,000" in srt
    assert "00:00:10,000 --> 00:00:20,000" in srt


def test_overlapped_audio_segment_only_indexes_nominal_window(tmp_path: Path):
    segment = AudioSegment(
        path=tmp_path / "audio.wav",
        nominal_start=300,
        nominal_end=600,
        extract_start=290,
        extract_end=610,
    )

    assert not cue_center_in_segment(TranscriptCue(1, 292, 296, "上一段上下文"), segment)
    assert cue_center_in_segment(TranscriptCue(2, 598, 603, "边界处连续讲解"), segment)


def test_merge_transcript_cues_deduplicates_overlap_text():
    cues = [
        TranscriptCue(1, 298, 303, "这里继续解释 RAG-Fusion。"),
        TranscriptCue(2, 302, 307, "这里继续解释 RAG-Fusion。"),
        TranscriptCue(3, 310, 315, "然后进入 BM25 和向量召回。"),
    ]

    merged = merge_transcript_cues(cues, overlap_seconds=10)

    assert [cue.text for cue in merged] == [
        "这里继续解释 RAG-Fusion。",
        "然后进入 BM25 和向量召回。",
    ]
