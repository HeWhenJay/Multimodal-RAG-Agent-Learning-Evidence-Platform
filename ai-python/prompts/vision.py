"""视觉 OCR 模型 Prompt。"""


DEFAULT_OCR_PROMPT = (
    "请只返回图片中的 OCR 文本，保留自然段、标题和表格结构。"
    "如果是表格，请优先使用 Markdown 表格；不要输出解释、免责声明或额外说明。"
)
