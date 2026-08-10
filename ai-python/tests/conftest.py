import os
import sys
from pathlib import Path

AI_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(AI_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(AI_PYTHON_DIR))


# 单元测试不调用外部百炼服务，避免依赖本机密钥和消耗模型额度。
os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("RAG_VECTOR_DIMENSIONS", "1024")
os.environ.setdefault("RAG_ANSWER_PROVIDER", "local")
os.environ.setdefault("RAG_RERANK_PROVIDER", "local")
os.environ["RAG_QUERY_EXPANSION_PROVIDER"] = "local"
# 普通单测不调用 ASR/OCR 纠错模型，纠错模块测试会显式注入本地替身。
os.environ.setdefault("RAG_TEXT_CORRECTION_ENABLED", "false")
# 仅显式的 LangExtract 集成测试启用真实候选节点，避免普通单测访问远程复习模型。
os.environ.setdefault("REVIEW_LANGEXTRACT_ENABLED", "false")
