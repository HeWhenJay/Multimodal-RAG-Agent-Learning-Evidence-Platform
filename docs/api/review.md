# 学习复习与提醒接口文档

更新日期：2026-08-04

## 变更摘要

新增 `/api/reviews/*` 公开控制面。系统在资料完成 RAG 入库后识别八股背诵、面经、课程讲解、技术原理、学习笔记等学习型内容，从已有 RAG evidence 提炼短小的关键知识点卡片，并使用 FSRS 间隔重复算法计算下次复习时间。每日队列以用户上传资料为 group；每日上限按资料份数计算，被选中的资料会返回该资料全部当前到期的小卡片，不再按 group 截断卡片数量。

新增用户自定义复习文件夹。用户以整份文档为最小归档单位，把一份资料及其全部复习卡片移动到一个文件夹；点击文件夹进入独立详情页，按文档查看该文件夹中的全部活动卡片。归档不改变 FSRS 到期时间、RAG 索引或资料排除状态。已归档资料会从主页面“资料归档”列表隐藏，但到期时仍出现在“今日复习资料”队列；用户可从今日资料组直接进入所属文件夹并定位对应文档。删除文件夹或“移出文件夹”只解除文档归档，资料会重新回到主页面归档列表。

新增按文档“对话补漏”。用户可以用自然语言指出疑似遗漏主题，例如“还讲了页缓存和零拷贝”，服务只在该文档的 RAG evidence 中寻找有原文支撑、且未被现有活动卡片覆盖的知识点。补漏使用独立的 add-only 写入路径：只插入新卡片，不重新生成资料，不停用、更新或替换任何既有卡片，也不改变既有 `fsrs_card_json`、到期时间、评分次数、遗忘次数和复习日志。没有找到合格新知识点时返回正常的零新增结果，不把资料标记为失败。

今日资料标题上的拖拽手柄同时支持文件夹投放。用户把整份文档拖到文件夹卡片并松手后，前端调用既有批量归档接口，只改变该文档的文件夹归属；拖拽经过今日队列造成的临时排序会恢复，不额外写入资料优先级。主页面资料归档区支持逐份勾选和“全选未归档资料”，选择目标文件夹后一次批量进入；触屏与键盘用户无需使用拖拽也能完成批量归档。

结构化视频不再受普通资料“最多 8 张”的固定截断影响。提炼前先从整份清洗后 evidence 中提取讲者、课件或字幕已经明确列出的原始问题，同时由 LangExtract 从陈述式讲解中发现定义、机制、流程、因果、对比和实践结论。当结构化问题或严格定位候选超过 8 个时，单份资料动态放宽到最多 32 张；同主题连续细节可合并，但不得遗漏候选知识 ID。服务端继续逐卡执行问题完整性、答案忠实度和 evidence 引用门禁，不用数量目标放宽质量要求。

新增卡片级和资料组级删除。卡片删除会立即停用卡片并保留稳定来源键排除记录，后续同步或重新生成不得恢复同一卡片；资料组删除会停用该资料的全部复习卡片并写入资料排除记录，后续自动同步、索引完成回调和手动生成都必须跳过。资料组删除只影响复习中心，不删除用户上传的原始文件、RAG 文档、切块或 evidence；既有 FSRS 评分日志继续保留，避免删除后篡改“今日已完成”等历史统计。

每份复习资料新增一份由 DeepSeek 生成的复习摘要。`learning_material.document_summary` 是 RAG 索引摘要，可能只是截断后的开头内容，不能直接作为复习总结展示；它只作为本地前置过滤和模型判断资料范围的辅助输入。本地只判断资料是否值得进入复习中心并分配内部类别；通过过滤后的复习摘要、问题、答案和提示必须在同一次 DeepSeek 请求中完整生成并持久化，不能由本地规则拼写、补齐或降级生成。每日卡片 group 与资料列表都返回同一份模型摘要。

今日复习资料 group 支持用户拖拽排序。排序以“当前用户 + 复习资料”为持久化单位保存在 PostgreSQL；前端提交当前可见 group 的完整顺序后，服务端将这些资料置于用户队列前部，其余复习资料保持原有相对顺序。该顺序影响 `GET /api/reviews/due` 与 `GET /api/reviews/due-groups` 的资料选择顺序，但不改变卡片组内到期顺序、FSRS 状态或评分算法。

复习卡片不得要求用户重新阅读整篇文档或观看整段视频。每张卡片必须保留 `evidenceRefs`，字段与现有 RAG `Evidence` 一致；视频 evidence 必须保留 `startTime/endTime/playbackUrl` 并复用 `/videos` 时间段跳转，其他格式复用 `/preview/material/{id}` 展示原始文本或带章节位置的 RAG 提取视图。

所有端点使用 `Authorization: Bearer <token>`，只允许访问当前用户的数据，并保持 `{code,msg,data}` 的 `Result<T>` 响应信封。请求校验和业务失败返回 HTTP 200、`code=0` 与中文错误说明。

## 方案约定

- 排程算法：`FSRS`，默认目标记忆率 `0.90`，支持用户设置 `0.80-0.97`。
- 评分：`1=忘记`、`2=困难`、`3=记得`、`4=轻松`。忘记必须与“困难”分开，避免排程间隔被错误拉长。
- 首次卡片立即进入待复习队列；首次作答后由 FSRS 直接计算后续间隔，不使用固定的“第 1/2/4/7/15/30 天”硬编码表。
- 默认每日上限 `20` 份资料。待复习计数由持久化 `dueAt` 实时计算，服务重启后不丢失；同一资料当天首次评分即占用一份额度。
- 文件夹只负责资料管理与查找，不改变到期规则。文件夹中的活动卡片继续参与 `dueCount`、每日资料额度和今日复习队列；今日资料组返回 `folderId/folderName` 供前端定位。
- `dueCount` 表示全部到期卡片积压，`todayReviewedCount` 表示当天已经开始复习的资料数，`actionableDueCount` 表示扣除资料额度后仍可进入队列的资料数；同一资料已开始复习时，即使额度用尽也会继续展示该资料剩余到期卡片。顶部徽标和浏览器通知只使用后者。
- 资料同步按 `learning_material.index_request_version` 与提炼器版本幂等。资料重建索引或 Prompt 升级后，按资料分批刷新模型摘要、知识点正文和 evidence；同一稳定来源键的卡片继续保留既有 FSRS 学习状态。
- 资料先执行确定性的本地前置过滤，只判断是否属于学习资料并分配内部类别；纯时间码、字幕水印、口头语、会议纪要、日志、歌词等杂项直接写入 `SKIPPED`，不调用 DeepSeek。通过过滤后，独立的复习 PAE/ReAct LangGraph 执行“规划—LangExtract Curator—生成—质量观察—修复”循环。Curator 对完整 evidence 只运行一次，Repair 复用候选，不重复执行长文抽取。图的 `recursion_limit` 固定为 `999`，卡片 DeepSeek 真实模型调用默认最多 `8` 次，可通过 `REVIEW_GENERATION_MAX_ATTEMPTS` 调整；递归上限不能被解释为 999 次模型请求。
- `DEEPSEEK_API_KEY` 缺失等不可执行错误写入 `FAILED`。模型输出未通过问题完整性、结构化问题覆盖与 evidence 门禁时，观察节点必须形成逐项中文诊断并送入下一轮 Prompt；尝试耗尽或 LangGraph 递归异常后写入 `NEEDS_REVIEW`，停用该资料当前活跃卡片，并持久化 `generationAttempts` 与 `qualityFeedback`。`NEEDS_REVIEW` 不参加后台自动重试，必须由用户携带补充说明再次生成。
- `FAILED` 和 `NEEDS_REVIEW` 资料的 `reason` 是可诊断字段，前端资料分组会直接展示失败或人工处理原因。服务启动日志也会提示密钥缺失，但不会阻止其他 RAG 接口启动。Windows 本地开发中，若长时间运行的 PyCharm 没有继承新设置的用户环境变量，服务会只读当前用户的 `HKCU\Environment` 并把 `DEEPSEEK_API_KEY` 注入当前进程，随后由 `run.py` 启动的 API 与 worker 统一继承；不会读取其他账户、不会把密钥写入配置文件或日志。
- 复习生成期间，`ReviewMaterial.generationProgress` 持久化当前阶段和最近 12 条事件。阶段包括 evidence 整理、Planner 规划、LangExtract 知识发现、DeepSeek 生成、Observer 质量校验、Repair 自动修复、卡片保存和人工处理；Curator 会公开原始候选、精确定位候选、最终选择数与模型请求数。`percent`、`currentStep/totalSteps`、`attempt/maxAttempts` 与 `detail` 可直接驱动前端进度条和流程时间线。服务重启后仍可读取最后阶段快照，超过 20 分钟未更新的 `GENERATING` 资料允许下一轮同步恢复。
- 复习功能的所有 LLM Prompt 统一放在 `ai-python/prompts/`，当前复习卡片版本为 `review-card-v11`。v11 在 v10 的问题清洗、可选问号、`sourceQuestion` 审计和增量 Repair 基础上，引入最多 32 个 LangExtract `knowledgeUnitId`；模型必须为卡片回传覆盖 ID，服务端校验候选 evidence 与卡片 evidence 至少有一项重合，并拒绝未完整覆盖的结果。DeepSeek 空响应或非法 JSON 在当前质量轮内最多短程重试 3 次，不消耗 LangGraph 质量修复轮次。逐论断答案仍必须由所引用 evidence 支撑，业务模块不得生成或改写面向用户的摘要、问题、答案与提示。
- 所有复习 LLM 调用固定使用 DeepSeek 官方模型标识 `deepseek-v4-flash`（官方滚动指向最新正式版），显式开启 `thinking.type=enabled`，思考强度固定为 `reasoning_effort=max`。请求地址固定使用 DeepSeek 官方 OpenAI 兼容 Base URL `https://api.deepseek.com`，密钥只读取用户环境变量 `DEEPSEEK_API_KEY`；不得继承 `RAG_LLM_MODEL`、`DASHSCOPE_API_KEY` 或第三方代理 URL。
- 视频、面经和讲解类资料在送模前先从原始 transcript evidence 抽取原始问句候选，父段摘要与 OCR 转场不得进入候选。模型必须优先选择资料中已经明确提出、且由后续原文回答的重点问题，并在输出中回传 `sourceQuestion` 作为来源审计；最终 `question` 仍由 DeepSeek 输出为去除口头语、指代完整、可脱离上下文独立理解的问句。只有没有合适原问句时才允许根据重点事实生成新问题，且不得生成脱离资料表述的泛化问题。
- 发布前质量门禁必须逐卡拒绝：`父段摘要：`、时间码或 OCR 水印；“那是什么意思”“这些是什么”等无上下文指代；陈述句、转场句和未完成问句；“本节关键知识点是什么”等泛化占位题；答案为空、答案与问题明显错位、引用不存在或答案缺少 evidence 支撑。学习资料还必须包含一份非空模型摘要和至少一张通过门禁的卡片，否则整次结果为 `FAILED`。
- evidence 在本地前置过滤阶段会移除纯时间码、重复字幕水印和无事实内容的口头转场；未通过学习内容过滤的资料直接跳过且不创建卡片，历史旧版本卡片会在增量同步时停用并重建。本地过滤不得生成或改写资料总结、问题、答案和提示。
- 用户删除使用 PostgreSQL tombstone 作为唯一事实来源。`learning_review_card_exclusion` 按 `materialId + sourceKey` 阻止同一卡片复活，`learning_review_material_exclusion` 按 `materialId` 永久阻止整份资料再次进入复习中心；不能只依赖前端隐藏或 Redis 缓存。
- 删除接口必须幂等并校验当前用户归属。卡片删除与并发评分使用行锁串行化；资料删除与并发生成共同锁定 `learning_material`，保证最终状态要么完整生成、随后被删除，要么生成结果在保存前被排除。
- PostgreSQL 的 `dueAt` 和评分日志是唯一事实来源，不缓存卡片排程；配置 `REDIS_URL` 时只使用带 TTL 的生成短锁，防止多实例对同一资料重复调用 LLM。
- 资料拖拽顺序保存在 `learning_review_material.display_order`。批量排序先锁定当前用户仍在复习中心的资料行并校验全部 ID，再在同一事务中连续重编号；重复提交同一顺序不改写未变化的行。未设置顺序的新资料追加在已排序资料之后，并保留现有到期时间兜底顺序。

## 模型运行配置

DeepSeek 官方 OpenAI 兼容入口为 `https://api.deepseek.com`，复习服务固定请求 `deepseek-v4-flash`。官方说明该模型标识会滚动指向最新正式版本；思考模式请求同时携带 `thinking={"type":"enabled"}` 与 `reasoning_effort="max"`，不传 `temperature`。参数依据：[首次 API 调用](https://api-docs.deepseek.com/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode)。

线上默认并发与预算：

| 环节 | 默认值 | 说明 |
| --- | ---: | --- |
| `REVIEW_LANGEXTRACT_MAX_WORKERS` | `8` | 同一 pass 内并发处理文本块，硬上限 10 |
| `REVIEW_LANGEXTRACT_EXTRACTION_PASSES` | `2` | 两轮串行执行，提高召回；不会跨 pass 并发 |
| `REVIEW_LANGEXTRACT_MAX_CHAR_BUFFER` | `8000` | 单个文本块字符预算 |
| `REVIEW_LANGEXTRACT_MAX_MODEL_REQUESTS` | `32` | 单份资料 LangExtract 请求总预算 |
| `REVIEW_LANGEXTRACT_TIMEOUT_SECONDS` | `120` | 单个 LangExtract 请求超时 |
| `REVIEW_DEEPSEEK_MAX_IN_FLIGHT` | `8` | 卡片生成请求的进程级并发闸门 |

LangExtract provider 内部使用 `ThreadPoolExecutor` 并行等待 DeepSeek HTTP；项目在客户端代理外再增加进程级共享闸门，保证多份资料同时生成时不会把“每份 8 个 worker”相乘成无界请求数。配置超过 10 时回退或限制在安全范围，降低配置不当造成的限流和内存压力。

密钥只从用户环境变量 `DEEPSEEK_API_KEY` 读取。默认优先使用当前进程环境；Windows 进程环境缺失时，再只读当前用户的 `HKCU\Environment`，并缓存到当前进程供受监督 worker 继承。Linux、容器和服务器环境仍只使用标准进程环境。复习生成不会读取 `SUBAI_BASE_URL`、`SU_BAI_API_KEY`、`DASHSCOPE_API_KEY` 或通用 RAG 模型配置，也不存在本地内容降级。Windows 用户级配置示例：

```powershell
[Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', '<your-key>', 'User')
```

设置后重新运行 `run.py` 即可；即使 PyCharm 自身仍持有旧环境快照，Windows 用户环境变量回退也能生效。配置文件、接口响应和日志均不得记录密钥值。

## 公开端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/reviews/sync?limit=1` | 扫描当前用户已入库但尚未同步的一份资料，完成分类和卡片生成 |
| GET | `/api/reviews/overview` | 获取待复习数、今日完成数、下次到期时间和用户设置 |
| GET | `/api/reviews/due?limit=20` | 获取当前到期的关键知识点卡片 |
| GET | `/api/reviews/due-groups?limit=20` | 按上传资料 group 获取今日到期卡片，列表不返回答案正文 |
| PUT | `/api/reviews/due-groups/order` | 批量保存当前用户今日资料 group 的拖拽顺序 |
| GET | `/api/reviews/materials` | 获取主页面未归档资料的分类、生成状态和卡片数；已归档资料仅在文件夹详情返回 |
| PUT | `/api/reviews/materials/folder` | 以文档为单位批量移入指定文件夹，`folderId=null` 时移出文件夹 |
| POST | `/api/reviews/materials/{materialId}/generate` | 对一条当前用户资料重新分类并生成卡片，可携带人工补充说明 |
| POST | `/api/reviews/materials/{materialId}/missing-knowledge` | 根据用户对话提示从当前文档 evidence 中寻找遗漏知识点，只追加新卡片 |
| POST | `/api/reviews/materials/batch-delete` | 批量将多份资料移出复习中心 |
| DELETE | `/api/reviews/materials/{materialId}` | 将整份资料永久移出复习中心，保留原始 RAG 文件 |
| GET | `/api/reviews/folders` | 获取当前用户的文件夹及文档、卡片和到期统计 |
| POST | `/api/reviews/folders` | 新建复习文件夹 |
| GET | `/api/reviews/folders/{folderId}` | 进入文件夹并按文档读取全部活动卡片，答案保持隐藏 |
| PATCH | `/api/reviews/folders/{folderId}` | 重命名复习文件夹 |
| DELETE | `/api/reviews/folders/{folderId}` | 删除文件夹并把其中资料改为未归档 |
| GET | `/api/reviews/cards/{cardId}` | 用户主动揭示答案时获取答案、提示和原文 evidence |
| POST | `/api/reviews/cards/{cardId}/grade` | 提交四档回忆结果并更新 FSRS 状态 |
| POST | `/api/reviews/cards/batch-delete` | 批量删除多张复习卡片 |
| DELETE | `/api/reviews/cards/{cardId}` | 删除一张卡片并阻止同一稳定来源卡片再次生成 |
| PUT | `/api/reviews/settings` | 更新复习提醒开关、目标记忆率、每日上限和提醒时间 |

### 对话补充遗漏知识点

```http
POST /api/reviews/materials/12/missing-knowledge
Content-Type: application/json
Authorization: Bearer <token>

{
  "message": "视频后半段还讲了页缓存和零拷贝，请找出漏掉的知识点",
  "conversation": [
    {"role": "USER", "content": "先检查 Kafka 高性能相关内容"},
    {"role": "ASSISTANT", "content": "已补充 1 张顺序写相关卡片。"}
  ]
}
```

`message` 为本轮用户提示，长度 1-2000 字；`conversation` 是前端会话级上下文，最多携带最近 12 条用户或助手消息。服务端不会信任请求中的用户 ID，只读取当前认证用户拥有的资料、evidence 和活动卡片。对话历史默认不持久化；刷新页面后可以丢失，但已成功追加的卡片永久保存。

```json
{
  "materialId": 12,
  "assistantMessage": "找到并追加了 2 个有原文支撑、且未被现有卡片覆盖的知识点。",
  "addedCount": 2,
  "skippedCount": 1,
  "cards": [
    {
      "id": 91,
      "materialId": 12,
      "materialTitle": "Kafka 高性能设计",
      "documentType": "mp4",
      "question": "Kafka 的零拷贝为什么能够提升数据传输性能？",
      "answer": "……",
      "hint": "从磁盘 IO、内核空间和用户空间之间的数据流转回忆",
      "evidenceRefs": [],
      "dueAt": "2026-08-04T09:00:00+08:00",
      "retrievability": 0,
      "reviewCount": 0,
      "lapseCount": 0
    }
  ]
}
```

补漏模型只能引用当前文档中 1-2 个真实 `evidenceId`，答案逐论断通过现有 evidence 忠实度门禁。模型会看到现有问题、答案和来源作为禁止重复基线；服务端还会执行问题规范化、文本相似度、来源键、永久排除记录和数据库唯一约束去重。即使并发提交相同提示，也只能插入尚不存在且未被排除的新卡片。

## 数据结构

### `ReviewSettings`

```json
{
  "enabled": true,
  "desiredRetention": 0.9,
  "dailyLimit": 20,
  "reminderTime": "09:00",
  "timezone": "Asia/Shanghai"
}
```

### `ReviewCard`

```json
{
  "id": 81,
  "materialId": 12,
  "materialTitle": "Kafka 的高可用性（视频讲解）",
  "documentType": "mp4",
  "question": "Kafka 的副本机制如何保证分区高可用？",
  "answer": null,
  "hint": "先回忆 Leader、Follower 与 ISR 的关系",
  "evidenceRefs": [
    {
      "evidenceId": "material-12-7",
      "documentId": "material-12",
      "documentTitle": "Kafka 的高可用性（视频讲解）",
      "title": "Kafka 的高可用性（视频讲解）",
      "sectionName": "ISR 与故障转移",
      "snippet": "……",
      "source": "upload",
      "documentType": "mp4",
      "startTime": "00:08:12",
      "endTime": "00:10:05",
      "playbackUrl": "/videos?documentId=material-12&startTime=00%3A08%3A12",
      "score": 1.0,
      "retrievalSource": "summary",
      "metadata": {}
    }
  ],
  "dueAt": "2026-08-01T09:00:00+08:00",
  "retrievability": 0.9,
  "reviewCount": 0,
  "lapseCount": 0
}
```

`GET /api/reviews/due` 与 `GET /api/reviews/due-groups` 中的 `answer` 固定为 `null`。用户点击“查看答案”后，前端再调用 `GET /api/reviews/cards/{cardId}` 获取答案和完整 `evidenceRefs`，避免预加载时提前暴露答案。卡片评分接口不接受前端提交答案或来源。

### `ReviewDueGroups`

```json
{
  "totalDueCount": 6,
  "remainingToday": 14,
  "groups": [
    {
      "materialId": 12,
      "materialTitle": "Kafka 的高可用性（视频讲解）",
      "materialSummary": "视频围绕 Kafka Broker 集群、分区副本、Leader/Follower 与 ISR 故障转移说明高可用机制。",
      "documentType": "mp4",
      "folderId": 7,
      "folderName": "消息中间件",
      "dueCardCount": 1,
      "cards": [
        {
          "id": 81,
          "materialId": 12,
          "materialTitle": "Kafka 的高可用性（视频讲解）",
          "documentType": "mp4",
          "question": "Kafka 的副本机制如何保证分区高可用？",
          "answer": null,
          "evidenceRefs": [],
          "dueAt": "2026-08-01T09:00:00+08:00",
          "retrievability": 0.9,
          "reviewCount": 0,
          "lapseCount": 0
        }
      ]
    }
  ]
}
```

`materialSummary` 只取当前 `review-card-v11` 复习提炼阶段生成并持久化的 DeepSeek 摘要，不回退到 RAG 的 `document_summary`。`folderId/folderName` 表示资料当前归档位置，为空时定位到主页面“资料归档”列表；非空时前端跳转 `/reviews/folders/{folderId}?materialId={materialId}`，文件夹详情自动展开、滚动并高亮对应文档。`limit` 表示最多选择多少份资料，不是卡片数量；每个返回的 group 会包含该资料全部当前到期卡片。`remainingToday` 表示当天还可开始复习的新资料份数。

### 保存资料 group 顺序

```http
PUT /api/reviews/due-groups/order
Content-Type: application/json
Authorization: Bearer <token>

{"materialIds": [12, 8, 21]}
```

`materialIds` 必须包含 1 到 100 个互不重复的正整数资料 ID，并保持用户拖拽后的顺序。服务端不会信任前端用户 ID：它会在一个事务中锁定当前认证用户仍处于复习中心的资料，要求请求中的每个 ID 都命中后才更新；任一资料不存在、已移除或属于其他用户时，整次请求返回“复习资料不存在”且不做部分更新。命中的资料会按请求顺序置前，未出现在本次请求中的资料保持原有相对顺序并稳定追加。并发排序请求通过用户设置行和资料行锁串行化，最后完成的完整请求生效；重复提交同一顺序不会改写未变化的行。

排序只影响队列资料优先级，不改变卡片的 FSRS 状态、到期时间或资料原始 RAG 内容；成功响应返回本次接受的 ID 顺序。

`groups` 数组已按用户保存的资料优先级返回。每个 group 内的 `cards` 仍按 `dueAt`、卡片 ID 的原有稳定规则排序，排序接口不会修改卡片字段。

### `ReviewGroupOrderResult`

```json
{
  "materialIds": [13, 12],
  "orderedCount": 2
}
```

`materialIds` 原样返回本次已接受的资料顺序，`orderedCount` 表示参与本次前置排序的资料数。重复提交相同顺序返回同一结果。

### `ReviewMaterial`

```json
{
  "materialId": 12,
  "title": "Kafka 的高可用性（视频讲解）",
  "summary": "视频围绕 Kafka Broker 集群、分区副本、Leader/Follower 与 ISR 故障转移说明高可用机制。",
  "documentType": "mp4",
  "materialStatus": "READY",
  "isLearningContent": true,
  "category": "面试复习",
  "status": "GENERATED",
  "cardCount": 4,
  "generationAttempts": 2,
  "qualityFeedback": ["第 1 次：卡片 2 的答案未通过 evidence 忠实度校验"],
  "generationProgress": {
    "stageCode": "review.observer",
    "stageLabel": "质量校验",
    "message": "正在校验第 2/6 版卡片的完整性与 evidence 忠实度",
    "status": "RUNNING",
    "currentStep": 3,
    "totalSteps": 4,
    "percent": 46,
    "attempt": 2,
    "maxAttempts": 6,
    "detail": "检查摘要、完整问句、提示、sourceQuestion、evidenceId、逐论断忠实度和问题覆盖率",
    "createdAt": "2026-08-03T21:30:00+08:00",
    "events": []
  },
  "needsManualReview": false,
  "folderId": 7,
  "folderName": "Python 面试",
  "indexRequestVersion": 1,
  "syncedIndexRequestVersion": 1
}
```

`status` 可能为 `PENDING`、`GENERATING`、`GENERATED`、`SKIPPED`、`FAILED` 或 `NEEDS_REVIEW`。`PENDING` 表示位于串行队列，`GENERATING` 表示后端已真正开始执行；前端可轮询 `GET /api/reviews/materials` 展示 `generationProgress`。`NEEDS_REVIEW` 表示自动修复已经耗尽，前端应展示质量反馈并提供人工说明输入框。用户再次生成时可以发送：

```http
POST /api/reviews/materials/12/generate
Content-Type: application/json
Authorization: Bearer <token>

{"userFeedback":"本节只讨论 Kafka delete 与 compact 两类日志清理策略，请围绕视频原问题生成。"}
```

`userFeedback` 可省略；存在时去除首尾空白，最长 2000 字，只作为当前复习图的修复上下文，不改变原始 RAG evidence。无请求体的旧调用保持兼容。

### `ReviewFolder`

```json
{
  "id": 7,
  "name": "Python 面试",
  "materialCount": 3,
  "cardCount": 42,
  "dueCardCount": 8,
  "updatedAt": "2026-08-03T09:30:00+08:00"
}
```

文件夹名称去除首尾空白后必须为 1-80 个字符；同一用户不能创建同名文件夹。所有统计只包含当前用户且未从复习中心排除的资料，`cardCount` 只统计当前提炼器版本的活动卡片。

### `ReviewFolderDetail`

```json
{
  "folder": {
    "id": 7,
    "name": "Python 面试",
    "materialCount": 1,
    "cardCount": 20,
    "dueCardCount": 6,
    "updatedAt": "2026-08-03T09:30:00+08:00"
  },
  "materials": [
    {
      "materialId": 12,
      "title": "python基础面经—秋招实习小白必看常见考点和问题",
      "summary": "资料按原视频问题顺序覆盖 Python 基础面试考点。",
      "documentType": "mp4",
      "cardCount": 20,
      "cards": [
        {
          "id": 81,
          "materialId": 12,
          "materialTitle": "python基础面经—秋招实习小白必看常见考点和问题",
          "documentType": "mp4",
          "question": "Python 的深拷贝与浅拷贝有什么区别？",
          "answer": null,
          "evidenceRefs": [],
          "dueAt": "2026-08-03T09:00:00+08:00",
          "reviewCount": 0,
          "lapseCount": 0
        }
      ]
    }
  ]
}
```

文件夹详情返回每份文档的全部活动卡片，不受“今日到期”条件和每日文档上限影响；列表中的答案与 evidence 仍保持隐藏，用户点击“查看答案”后复用 `GET /api/reviews/cards/{cardId}`。文件夹详情用于浏览和主动回忆，不允许绕过 `POST /cards/{cardId}/grade` 的到期校验。

### 文档归档

```http
PUT /api/reviews/materials/folder
Content-Type: application/json
Authorization: Bearer <token>

{"materialIds": [12, 13], "folderId": 7}
```

`materialIds` 必须包含 1-100 个互不重复的正整数资料 ID。服务端在单个事务中校验文件夹和所有资料都属于当前用户且资料未被排除，任一 ID 无效则整次不做部分移动。`folderId=null` 表示把资料移回未归档状态。成功返回：

```json
{"folderId": 7, "materialIds": [12, 13], "movedCount": 2}
```

### `ReviewOverview`

```json
{
  "dueCount": 6,
  "todayReviewedCount": 12,
  "totalCardCount": 38,
  "activeMaterialCount": 5,
  "nextDueAt": "2026-08-01T14:30:00Z",
  "settings": {
    "enabled": true,
    "desiredRetention": 0.9,
    "dailyLimit": 20,
    "reminderTime": "09:00",
    "timezone": "Asia/Shanghai"
  }
}
```

### `ReviewDeletionResult`

```json
{
  "scope": "CARD",
  "materialId": 12,
  "cardId": 81,
  "deleted": true
}
```

资料组删除时 `scope` 为 `MATERIAL`，`cardId` 为 `null`。响应中的 `deleted=true` 表示排除 tombstone 已存在且复习内容不可见；重复调用返回同一成功语义，不因卡片正文已经删除而报错。

### `ReviewBatchDeletionResult`

```json
{
  "scope": "CARD",
  "requestedCount": 3,
  "deletedCount": 3,
  "cardIds": [81, 82, 83],
  "materialIds": []
}
```

批量接口每次最多接受 100 个 ID，服务端会去重并按 ID 排序后在单个事务中处理。`deletedCount` 表示属于当前用户且最终处于排除状态的 ID 数量；已经存在 tombstone 的重复删除仍计为幂等成功。无归属或不存在的 ID 不计入结果，若一个 ID 都未命中则返回对应的“不存在”错误。

## 请求与响应

### 同步学习资料

```http
POST /api/reviews/sync?limit=1
Authorization: Bearer <token>
```

成功数据：

```json
{
  "processedMaterialCount": 1,
  "generatedCardCount": 4,
  "skippedMaterialCount": 0,
  "failedMaterialCount": 0
}
```

同步只处理 `READY/PARTIAL` 资料，并且单次只领取一份，避免串行 LLM 调用造成网关超时。非学习内容写入 `SKIPPED` 分类结果，不生成卡片；后续索引版本变化时允许重新判定。

### 提交复习评分

```http
POST /api/reviews/cards/81/grade
Content-Type: application/json
Authorization: Bearer <token>

{
  "rating": 3,
  "durationMs": 18500
}
```

成功数据：

```json
{
  "card": {},
  "previousDueAt": "2026-08-01T01:00:00Z",
  "nextDueAt": "2026-08-04T01:00:00Z",
  "intervalDays": 3.0,
  "retrievability": 0.9
}
```

评分必须为 `1-4`；卡片不存在、已停用或不属于当前用户时统一返回“复习卡片不存在”，未到期卡片返回“复习卡片尚未到期”。评分事务先锁定当前用户设置行，再核对每日额度并锁定卡片，避免同一用户并发评分突破每日上限。同一评分请求完成后同时更新卡片状态并追加复习日志，二者位于同一数据库事务。

### 查看答案与原文

```http
GET /api/reviews/cards/81
Authorization: Bearer <token>
```

成功数据为完整 `ReviewCard`。非视频 evidence 提供资料预览和章节 anchor；视频 evidence 提供 `startTime/endTime/playbackUrl`。前端先显示简洁答案，再由用户独立点击“查看 RAG 原文”；原文对话框中的“定位原文”进入原始文本或 RAG 提取视图，“从此处播放”进入现有视频播放器。

### 更新设置

```http
PUT /api/reviews/settings
Content-Type: application/json
Authorization: Bearer <token>

{
  "enabled": true,
  "desiredRetention": 0.9,
  "dailyLimit": 20,
  "reminderTime": "09:00",
  "timezone": "Asia/Shanghai"
}
```

`desiredRetention` 越高，预计复习频率越高。当前版本只影响后续评分生成的间隔，不批量改写既有卡片到期时间，避免突然制造大量到期任务。

### 删除卡片

```http
DELETE /api/reviews/cards/81
Authorization: Bearer <token>
```

服务端在同一事务中锁定卡片、写入包含原始 `cardId` 与稳定 `sourceKey` 的排除记录、停用卡片，并重新统计资料卡片数。既有评分日志继续保留；后续资料重建或 Prompt 升级时，相同 `sourceKey` 的候选卡片会在写入前被过滤。重复请求通过原始 `cardId` 排除记录幂等返回成功。

### 将资料移出复习中心

```http
DELETE /api/reviews/materials/12
Authorization: Bearer <token>
```

服务端锁定当前用户的 `learning_material`，写入资料排除记录，将该资料的复习卡片全部停用并把可见卡片数归零。原始上传文件、RAG 文档、切块、索引任务、evidence 和既有评分日志不受影响。资料排除后不再出现在 `materials`、到期队列和概览统计中，自动同步、索引完成后的生成以及手动生成都不会恢复它。

批量请求使用 `POST` 避免不同代理对带 JSON body 的 `DELETE` 支持不一致：

```http
POST /api/reviews/cards/batch-delete
Content-Type: application/json
Authorization: Bearer <token>

{"cardIds": [81, 82, 83]}
```

资料批量移出使用相同语义：

```http
POST /api/reviews/materials/batch-delete
Content-Type: application/json
Authorization: Bearer <token>

{"materialIds": [12, 13]}
```

## 错误与失败处理

| 场景 | 对外错误或行为 |
| --- | --- |
| 未登录或 token 失效 | 沿用认证模块中文错误 |
| 评分不在 `1-4` | `复习评分必须是 1 到 4` |
| 当日评分尝试开启新资料且达到每日资料上限 | `今日复习文档上限已达到` |
| 资料或卡片越权 | `学习资料不存在` / `复习卡片不存在` |
| 重复删除本人已经删除的卡片或资料 | 幂等返回 `deleted=true` |
| 批量 ID 超过 100 个、为空或含非正整数 | 返回请求校验错误，不执行任何批量操作 |
| 批量 ID 包含不存在或不属于当前用户的记录 | 其余命中项仍返回成功，`deletedCount` 小于 `requestedCount` |
| 排序列表为空、超过 100 个、包含非正整数或重复 ID | 返回请求校验错误，不修改既有顺序 |
| 排序列表包含不存在、已移除或不属于当前用户的资料 | `复习资料不存在`，整次排序不做部分更新 |
| 文件夹名称为空、超过 80 字或同名 | 返回中文校验错误，不创建或覆盖文件夹 |
| 文件夹不存在或不属于当前用户 | `复习文件夹不存在` |
| 文档归档包含不存在、越权或已排除资料 | `复习资料不存在`，整次移动不做部分更新 |
| 删除文件夹 | 文件夹消失，原文档和卡片保留并回到未归档状态 |
| 手动生成已移出复习中心的资料 | `该资料已从复习中心移除` |
| 同一资料正在由其他请求生成 | `该资料的复习卡片正在生成，请稍后刷新` |
| 资料尚未完成索引 | `学习资料尚未完成索引` |
| 无可用 evidence | 分类记录为 `FAILED`，不生成无来源卡片 |
| evidence 清洗后只剩字幕水印、时间码或口头噪声 | 本地过滤记录为 `SKIPPED`，不调用 DeepSeek，不生成卡片 |
| `DEEPSEEK_API_KEY` 未配置 | 分类记录为 `FAILED`，停用当前活跃卡片，显示“未配置 DeepSeek 密钥”，等待重新生成 |
| DeepSeek 请求超时、限流或服务异常 | 分类记录为 `FAILED`，停用当前活跃卡片，不发布本地降级内容，后续可重试 |
| DeepSeek 返回非法 JSON 或未通过质量门禁 | 分类记录为 `FAILED`，记录不含模型隐私内容的中文原因，不发布部分坏卡 |
| FSRS 状态损坏 | 使用当前卡片创建时间重建初始状态并记录受控日志，不回显内部状态 |

## 前端影响

- 新增 `/reviews` 复习中心，按上传资料展示每日 group；资料总结以卡片网格首位的全宽独立 box 展示，不参与回忆评分，标题区只保留资料元信息。group 内展示该资料全部到期卡片、答案揭示、四档评分、来源 evidence 和下一次复习时间。今日资料支持拖拽手柄、键盘方向键/Home/End，以及移动端上移/下移按钮调整优先级。
- 复习中心新增文件夹区。文件夹卡片展示文档数、卡片数和到期数；点击后跳转到 `/reviews/folders/{folderId}`，详情页按文档分组显示全部卡片，并保留返回复习中心的面包屑。归档资料只从主页面“资料归档”列表隐藏；到期后仍显示在今日复习队列，并可通过“定位资料”直接进入对应文件夹文档。
- 资料列表支持批量选择后移入文件夹或移回未归档；新建、重命名和删除文件夹都提供明确的页面内交互，删除提示必须说明不会删除文档与卡片。
- 侧栏新增“复习中心”；顶部通知按钮展示到期数量并跳转到复习中心。
- 页面打开时调用一次 `POST /api/reviews/sync`，之后定时刷新 `overview`；文件上传后会在 RAG 状态进入 `READY/PARTIAL` 时按 `materialId` 调用生成接口，避免上传响应早于 evidence 入库而漏生成卡片，也避免历史候选抢占本次上传。资料同步失败不阻断其他页面。
- 卡片右上角提供图标删除操作；资料 group 标题和右侧资料列表提供“移出复习中心”操作。两种操作都先显示明确确认对话框，资料确认文案必须说明原始 RAG 文件不会删除且移除后不会重新生成。
- 今日卡片支持复选框多选并批量删除，资料列表支持复选框多选并批量移出；批量操作按钮仅在有选中项时出现，并显示选中数量与对应影响范围。
- 今日资料 group 支持通过拖拽手柄调整优先级；前端拖拽结束后一次提交当前可见 group 的完整 `materialIds` 顺序，成功后沿用本地顺序，失败时恢复服务端最近一次顺序并显示错误。
- 删除成功后前端立即移除对应卡片或 group，并分别刷新 `overview`、`due-groups` 和 `materials`；刷新失败不能把已经成功的删除误报为删除失败。
- 浏览器通知只在用户主动授权后启用；后端持久化到期时间是唯一事实来源，前端不能自行计算 FSRS 间隔。
- 浏览器通知会等待用户设置时区中的 `reminderTime`，同一自然日最多发送一次；浏览器关闭后的系统级通知仍需后续接入 Web Push 或邮件基础设施。

## Java/Python 集成说明

当前公开复习控制面由 Python FastAPI 直接承载，文件夹归属、卡片读取和结构化问题提炼均在 Python 服务与 PostgreSQL 中完成；本次不新增 Java 侧 AI 或目录业务逻辑。若后续恢复 Java 业务壳，Java 只透传上述 `Result<T>` 契约并注入认证用户，不能在 Java 中重新生成、截断或合并卡片。
