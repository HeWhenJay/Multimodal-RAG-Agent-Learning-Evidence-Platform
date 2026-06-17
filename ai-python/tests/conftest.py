import os


# 单元测试不调用外部百炼服务，避免依赖本机密钥和消耗模型额度。
os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("RAG_VECTOR_DIMENSIONS", "1024")
os.environ.setdefault("RAG_ANSWER_PROVIDER", "local")
os.environ.setdefault("RAG_RERANK_PROVIDER", "local")
