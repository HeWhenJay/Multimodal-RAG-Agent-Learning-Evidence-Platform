"""音视频模型使用的 Prompt。"""


DEFAULT_ASR_PROMPT = (
    "请将音频转写为 SRT 字幕格式，只输出字幕内容。"
    "每段必须包含序号、HH:MM:SS,mmm --> HH:MM:SS,mmm 时间范围和中文转写文本。"
)
