from pathlib import Path

from app.storage.object_storage import StoredObject
from video.asr.bailian_asr import BailianAsrClient, milliseconds_to_srt_timestamp, transcription_json_to_srt
from video.chunking.video_processing import (
    AudioSegment,
    AudioSegmentAsrMicrobatchDispatcher,
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


def test_local_audio_segment_uses_oss_url_for_filetrans(monkeypatch, tmp_path: Path):
    """本地音频段应临时上传 OSS，用公开 URL 发起 filetrans 后清理对象。"""
    audio_path = tmp_path / "segment.mp3"
    audio_path.write_bytes(b"fake-audio")
    calls = {"stored": [], "deleted": [], "urls": []}

    class FakeStorage:
        storage_type = "oss"

        def store_file(self, source, filename, user_id, document_type, content_type=None):
            calls["stored"].append((Path(source), filename, user_id, document_type, content_type))
            return StoredObject(
                storage_type="oss",
                source_path="https://oss.example.com/asr-temp/segment.mp3",
                object_key="learning-evidence/asr-temp/asr-audio/segment.mp3",
                public_url="https://oss.example.com/asr-temp/segment.mp3",
            )

        def delete_object_key(self, key):
            calls["deleted"].append(key)

    monkeypatch.setenv("EVIDENCE_STORAGE_PROVIDER", "oss")
    monkeypatch.setenv("RAG_ASR_AUDIO_VIA_OSS", "true")
    monkeypatch.setenv("RAG_ASR_FILETRANS_ENABLED", "true")
    monkeypatch.setattr("app.storage.object_storage.build_rag_object_storage", lambda: FakeStorage())
    client = BailianAsrClient(api_key="test-key", provider="dashscope_filetrans")
    monkeypatch.setattr(
        client,
        "_call_filetrans",
        lambda url, progress_callback=None: calls["urls"].append(url) or "1\n00:00:00,000 --> 00:00:01,000\n转写文本",
    )

    text, warnings = client.transcribe_audio_file(audio_path)

    assert "转写文本" in text
    assert warnings == []
    assert calls["stored"][0][4] == "audio/mpeg"
    assert calls["urls"] == ["https://oss.example.com/asr-temp/segment.mp3"]
    assert calls["deleted"] == ["learning-evidence/asr-temp/asr-audio/segment.mp3"]


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


def test_audio_segment_asr_microbatch_preserves_submit_order(tmp_path: Path):
    """ASR 微批并发返回后，调用方可按提交 Future 保持原视频分段顺序。"""

    class FakeAsrClient:
        should_call_dashscope = False
        api_key = None
        model = "fake-asr"

        def transcribe_audio_file(self, audio_path):
            return f"{audio_path.stem} 转写文本", []

    segments = []
    for index in range(3):
        audio_path = tmp_path / f"segment-{index + 1}.wav"
        audio_path.write_bytes(b"fake-audio")
        segments.append(
            AudioSegment(
                path=audio_path,
                nominal_start=index * 300,
                nominal_end=(index + 1) * 300,
                extract_start=index * 300,
                extract_end=(index + 1) * 300,
            )
        )
    dispatcher = AudioSegmentAsrMicrobatchDispatcher(
        asr_client=FakeAsrClient(),
        batch_max_size=2,
        batch_wait_ms=10,
        max_in_flight=2,
        progress_reporter=None,
        total_segments=len(segments),
    )

    futures = [
        dispatcher.submit(index=segment_index, segment=segment)
        for segment_index, segment in enumerate(segments, start=1)
    ]
    dispatcher.close()
    results = [future.result() for future in futures]

    assert [result.index for result in results] == [1, 2, 3]
    assert [result.text for result in results] == ["segment-1 转写文本", "segment-2 转写文本", "segment-3 转写文本"]
    assert [result.batch_size for result in results] == [2, 2, 1]


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
