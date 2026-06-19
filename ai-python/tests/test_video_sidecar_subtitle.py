from pathlib import Path

from video.chunking.video_processing import load_sidecar_subtitle


def test_load_sidecar_subtitle_prefers_source_path_same_stem(tmp_path: Path):
    source_video = tmp_path / "L2-扩展补充代码讲解2-父子索引.subtitled.mp4"
    source_video.write_bytes(b"fake-video")
    subtitle = tmp_path / "L2-扩展补充代码讲解2-父子索引.subtitled.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n这里讲父子索引。\n",
        encoding="utf-8",
    )

    text, source, warnings = load_sidecar_subtitle("temporary-upload.mp4", str(source_video))

    assert "这里讲父子索引" in text
    assert source == str(subtitle)
    assert warnings == []


def test_load_sidecar_subtitle_handles_subtitled_mp4_with_plain_srt(tmp_path: Path):
    source_video = tmp_path / "course.subtitled.mp4"
    source_video.write_bytes(b"fake-video")
    subtitle = tmp_path / "course.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n这里讲父子索引。\n",
        encoding="utf-8",
    )

    text, source, warnings = load_sidecar_subtitle(str(source_video), str(source_video))

    assert "这里讲父子索引" in text
    assert source == str(subtitle)
    assert warnings == []


def test_load_sidecar_subtitle_reads_gb18030(tmp_path: Path):
    source_video = tmp_path / "course.mp4"
    source_video.write_bytes(b"fake-video")
    subtitle = tmp_path / "course.srt"
    subtitle.write_bytes("1\n00:00:00,000 --> 00:00:03,000\n中文字幕。\n".encode("gb18030"))

    text, source, warnings = load_sidecar_subtitle(str(source_video), str(source_video))

    assert "中文字幕" in text
    assert source == str(subtitle)
    assert warnings == []


def test_load_sidecar_subtitle_reads_timestamped_txt(tmp_path: Path):
    source_video = tmp_path / "course.subtitled.mp4"
    source_video.write_bytes(b"fake-video")
    transcript = tmp_path / "course.txt"
    transcript.write_text("[00:00:01] 这里讲 RAG-Fusion。\n[00:00:04] 然后讲 BM25。", encoding="utf-8")

    text, source, warnings = load_sidecar_subtitle(str(source_video), str(source_video))

    assert "RAG-Fusion" in text
    assert source == str(transcript)
    assert warnings == []
