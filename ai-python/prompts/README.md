# LLM Prompt 目录

项目内自定义的系统 Prompt、用户 Prompt 和版本号集中维护在本目录：

- `review.py`：学习资料复习卡片提炼
- `rag.py`：Multi-Query 与 evidence grounded answer
- `agent.py`：Planner、Executor、Repair、Acceptance、Answer Writer 及上下文压缩
- `resume.py`：简历字段补丁生成
- `vision.py`：图片 OCR
- `media.py`：音频 ASR

Prompt 函数只接收已经完成长度限制和敏感字段过滤的动态输入。调用方负责模型选择、超时、重试和响应校验；修改模板时同步更新对应版本常量和测试。
