# RAG 架构说明

## 阶段边界

第一阶段完成 RAG，不实现 Agent 任务编排。页面中可以保留 Agent 入口和业务概念，但后端不提供自主规划、工具调用、长任务调度等 Agent 能力。

```mermaid
flowchart LR
    A["React 前端"] --> B["Java Spring Boot API"]
    B --> C["MyBatis Mapper / H2 或 PostgreSQL"]
    B --> D["Python FastAPI RAG 服务"]
    D --> E["MinerU 文档识别适配器"]
    D --> F["递归切块"]
    D --> G["摘要索引"]
    D --> H["BM25 + PostgreSQL/pgvector 检索"]
    H --> I["RRF / RAG-Fusion"]
    I --> J["证据引用回答"]
```

## Python RAG 流程

索引阶段：

1. 接收 Java 传入的文件或文本。
2. 文件优先使用 `MINERU_COMMAND` 指定的 MinerU 命令解析。
3. 未配置 MinerU 或解析失败时，使用本地文本/PDF/DOCX 降级解析。
4. 按标题、段落、句子和长度预算做递归切块。
5. 为文档和章节建立摘要索引。
6. 为 chunk 建 BM25 词项统计和确定性哈希向量，并写入 PostgreSQL/pgvector 的 `rag_chunk.embedding`。

查询阶段：

1. 基于原问题生成 Multi-Query 变体。
2. 按 metadata 过滤用户、文档类型、来源和可见范围。
3. 对每个 query 同时执行 BM25 和 pgvector 向量召回。
4. 使用 Reciprocal Rank Fusion 合并多路排名。
5. 返回可追溯 evidence，并生成确定性回答摘要。

## Stitch 前端视觉基准

前端基于 Chrome 中 Stitch 项目 `学迹智配管理后台` 的生成页面复刻：

| 维度 | 取值 |
| --- | --- |
| 主色 | `#4F46E5` |
| 辅色 | `#0EA5E9` |
| 强调色 | `#A54100` |
| 背景 | `#F9FAFB` |
| 字体 | `Inter`，代码/标签使用 `JetBrains Mono` |
| 卡片圆角 | 约 `8px` |
| 布局 | 左侧固定导航 + 顶部搜索/上传栏 + 信息密度适中的工作台 |

Stitch 页面包含的核心模块：

- 工作台统计卡片。
- 知识库智能检索 RAG。
- 多模态数据接入通道。
- 岗位适配分析入口。
- 视频知识切片回顾。
- 简历证据对齐。
