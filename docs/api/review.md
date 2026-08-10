# 学习复习与提醒接口文档

更新日期：2026-08-10

## 交互式分段生成工作台（2026-08-10）

现有 `SEGMENTED` 模式会在服务端把按原位置排序的 evidence 按“每段最多 24 条、最多约 12000 字”自动切段，随后逐段使用宽松门禁生成，最后自动按规范化问题和来源键去重合并。该模式只展示总体进度，用户看不到每段原文，也不能给单独一段补充说明或编辑中间候选。

新增交互式分段工作台后，一键 `SEGMENTED` 快捷模式继续保留，同时增加以下人工流程：

1. 用户打开资料的“分段工作台”，读取稳定的分段 ID、章节/时间范围、原始 evidence 内容和当前正式卡片版本。
2. 用户勾选一个或多个分段，并为每段填写独立提示词；未勾选分段不会调用模型。
3. 后台任务只生成本轮选中的分段。每段独立返回成功、失败、摘要、质量反馈和带真实 evidenceId 的候选卡片；一个分段失败不影响其他分段结果。
4. 用户可以继续选择其他分段、修改提示词重新生成某段、编辑已生成问题/答案/提示，或取消某段参与最终合并。
5. “合并为正式复习卡片”会携带资料索引版本、原活动卡片 ID/指纹和全部候选 evidenceId。服务端重新校验资料归属、并发版本和逐卡 evidence 忠实度后，在同一事务中替换正式卡片；合并前不会写入正式卡片或改变 FSRS 状态。

同一轮选中的分段会进入进程级共享 I/O 线程池并发生成，默认最多 16 个；多份资料共用这一个池，避免每个任务各建线程导致并发膨胀。LangExtract 的本地切分、映射和聚合按 CPU/内存密集阶段限制为 n+1=9，实际模型网络请求仍受 16 个 I/O 并发槽约束。

### 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/reviews/materials/{materialId}/segments` | 返回当前用户资料的分段原文、evidence、原卡片版本与合并基线 |
| `POST` | `/api/reviews/materials/{materialId}/segment-tasks` | 按用户选择的 `segmentIds` 和逐段 `prompts` 创建后台生成任务 |
| `GET` | `/api/reviews/materials/{materialId}/segment-tasks/{taskId}` | 查询所选分段的生成进度、逐段结果和质量反馈 |
| `GET` | `/api/reviews/materials/{materialId}/segment-tasks/latest` | 重新打开工作台时恢复最近一次后台任务 |
| `POST` | `/api/reviews/materials/{materialId}/segments/merge` | 校验用户编辑后的候选并原子发布为正式复习卡片 |

分段生成请求示例：

```json
{
  "segmentIds": ["segment-a1", "segment-b2"],
  "prompts": {
    "segment-a1": "重点模拟面试官追问主从同步流程",
    "segment-b2": "保留脑裂参数及其作用"
  },
  "mode": "RELAXED"
}
```

公开响应继续使用 `Result<T>` 信封；用户身份只从 `Authorization` 对应的服务端上下文读取。分段 ID 必须属于当前资料当前索引版本，过期或跨资料 ID 返回中文业务失败。任务状态为 `QUEUED`、`RUNNING`、`SUCCEEDED` 或 `FAILED`。`SUCCEEDED` 表示任务编排完成，允许其中个别分段为 `FAILED`，前端应保留成功分段并允许用户重试失败段。

## 质量门禁分级与人工决策（2026-08-10）

### 变更摘要

复习生成不再把“所有结构化问句和所有 LangExtract 候选 100% 覆盖”作为唯一发布条件。单卡片仍必须
引用真实 evidence、拒绝噪声和资料外事实；资料级完整性改为可解释的分级门禁。当自动修复耗尽后，
用户可以保留当前可用卡片、补充提示词按标准门禁重试、使用宽松门禁重试，或把资料分段生成后合并。

生成失败或进入 `NEEDS_REVIEW` 时，不再停用上一次已发布的 AI 卡片，也不重置其 FSRS 状态、到期时间、
评分次数和遗忘次数。新候选只有完整通过所选模式门禁后才原子替换旧 AI 卡片。首次生成没有旧卡片时，
“保留当前卡片”不可用，用户仍可选择另外三种重新生成方式。

### 接口

`POST /api/reviews/materials/{materialId}/generate`

- 鉴权：`Authorization: Bearer <token>`，资料归属只从当前登录用户推导。
- 默认请求体可以省略，兼容原有标准后台生成。
- `REGENERATE` 立即返回 `GENERATING`，真实模型任务在线程池继续运行；前端沿用资料列表轮询
  `generationProgress`、`qualityFeedback` 和终态。
- `KEEP_CURRENT` 不调用模型；存在当前活动卡片时立即返回 `GENERATED`，否则返回中文业务失败信封。

请求体：

```json
{
  "action": "REGENERATE",
  "mode": "RELAXED",
  "userFeedback": "缓存穿透与布隆过滤器分别成卡，忽略重复口语问句"
}
```

| 字段 | 可选值 | 说明 |
| --- | --- | --- |
| `action` | `REGENERATE`、`KEEP_CURRENT` | 默认 `REGENERATE`；保留当前卡片时不进入后台模型任务 |
| `mode` | `STANDARD`、`RELAXED`、`SEGMENTED` | 默认 `STANDARD`；`KEEP_CURRENT` 时忽略 |
| `userFeedback` | 0-2000 字 | 用户补充的重点、问题范围或原始问句，不作为事实来源 |

成功信封保持 `Result<ReviewMaterial>`：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "materialId": 42,
    "status": "GENERATING",
    "cardCount": 13,
    "needsManualReview": false
  }
}
```

`cardCount` 在 `GENERATING`、`FAILED` 或 `NEEDS_REVIEW` 阶段可以保留上一个已发布版本的活动卡片数。
这表示旧版仍可复习，不表示新候选已发布。

### 三档生成策略

| 模式 | 资料级覆盖门禁 | 单卡片 evidence 门禁 | 适用场景 |
| --- | --- | --- | --- |
| `STANDARD` | 口语结构化问题至少 65%，干净结构化资料至少 85%，LangExtract 候选至少 60% | 逐论断字符覆盖与连续原文片段使用原严格阈值 | 默认生成，兼顾完整性与稳定性 |
| `RELAXED` | 结构化问题至少 45%，LangExtract 候选至少 40% | 仍要求真实 evidence、无噪声、每个论断与原文存在可核对重合；只适度降低字符阈值 | 已有较多合格卡但被完整性门禁卡住 |
| `SEGMENTED` | 按连续 evidence/章节自动分段，每段使用宽松门禁，成功段按稳定来源键和规范化问题去重合并 | 每段独立校验；失败段不能污染成功段 | 长视频、知识点较多、单次模型难以覆盖完整资料 |

以上比例是最低发布线，不是卡片数量目标。缺失候选仍写入 `qualityFeedback` 供用户审计。任何模式都不能
接受不存在的 evidenceId、空答案、时间码/OCR 水印、无上下文问题或完全没有原文支撑的答案。

### 失败与保留规则

- 模型不可用、返回非法结构、所有分段都失败或没有任何合格卡片时，状态仍为 `FAILED`/`NEEDS_REVIEW`。
- 失败只更新诊断、尝试次数和进度终态；已有活动卡片保持原样，新生成候选不部分覆盖数据库。
- `KEEP_CURRENT` 只确认继续使用当前活动卡片；没有活动卡片时返回
  `当前没有可保留的复习卡片，请选择重新生成方式`。
- 宽松和分段模式仍是后台任务，重复点击同一资料继续受进程内任务去重和资料级生成锁保护。
- Java 无需新增 AI 逻辑；React 只扩展请求类型和人工决策弹窗。

## 变更摘要

新增 `/api/reviews/*` 公开控制面。系统在资料完成 RAG 入库后识别八股背诵、面经、课程讲解、技术原理、学习笔记等学习型内容，从已有 RAG evidence 提炼短小的关键知识点卡片，并使用 FSRS 间隔重复算法计算下次复习时间。每日队列以用户上传资料为 group；每日上限按资料份数计算，被选中的资料会返回该资料全部当前到期的小卡片，不再按 group 截断卡片数量。

新增用户自定义复习文件夹。用户以整份文档为最小归档单位，把一份资料及其全部复习卡片移动到一个文件夹；点击文件夹进入独立详情页，按文档查看该文件夹中的全部活动卡片。归档不改变 FSRS 到期时间、RAG 索引或资料排除状态。已归档资料会从主页面“资料归档”列表隐藏，但到期时仍出现在“今日复习资料”队列；用户可从今日资料组直接进入所属文件夹并定位对应文档。删除文件夹或“移出文件夹”只解除文档归档，资料会重新回到主页面归档列表。

新增按文档“对话补漏”。用户可以用自然语言指出疑似遗漏主题，例如“还讲了页缓存和零拷贝”，服务只在该文档的 RAG evidence 中寻找有原文支撑、且未被现有活动卡片覆盖的知识点。补漏使用独立的 add-only 写入路径：只插入新卡片，不重新生成资料，不停用、更新或替换任何既有卡片，也不改变既有 `fsrs_card_json`、到期时间、评分次数、遗忘次数和复习日志。没有找到合格新知识点时返回正常的零新增结果，不把资料标记为失败。

新增用户手动建卡。用户可以在主页面资料行或文件夹详情的文档操作区直接填写问题、答案和可选提示；卡片以 `manual:*` 来源键进入同一 FSRS 队列，立即到期但不生成或伪造 RAG evidence，页面会明确标记“手动卡片”。手动卡片仍支持揭示答案、评分和删除，既有 AI 卡片及其复习记录不会被修改。

新增卡片正文编辑与 Markdown 展示。问题、答案和提示都允许用户直接修改，答案与提示保留安全 Markdown 换行、列表、标题、代码块、引用和强调格式；复习生成 Prompt 也会优先使用 Markdown 组织步骤、并列项和关键术语。编辑只更新卡面内容，不重置 FSRS 状态、到期时间、评分次数或历史日志；AI 卡片一旦被用户编辑会转为 `custom:*` 用户编辑来源键，资料再次生成时不会自动停用该卡片。

新增全量卡片库与 LLM 改写对比。`/reviews/cards` 页面按文档展示所有活动卡片，即使卡片已复习、因此从今日队列隐藏，也仍可查看和编辑；该页面不提供评分入口。LLM 改写先返回原卡片—新卡片对比预览，用户可在同一弹窗继续修改新卡片的 Markdown 内容，随后选择“回退并关闭”或“应用新卡片”。来源约束分为严格依赖原文、尽量以原文为主、原文仅参考三档；预览本身不写数据库，严格档位在应用时再次执行 evidence 忠实度校验。

新增资料级 AI 合并改写。主页面和文件夹详情均提供同一入口：读取当前资料的全部活动卡片，默认要求模型把多张卡片合并成 1 张综合卡片，并返回原摘要、原卡片与候选摘要/卡片的对比。预览接口不写数据库；应用接口携带 `sourceVersion`、原卡片 ID 列表和正文指纹 `originalFingerprint`，服务端在事务内再次校验资料、卡片集合与卡片内容未变化后，写入用户确认的候选、停用旧卡片并保存来源排除记录，避免旧卡片在下一次自动同步时复活。用户在预览页可继续编辑候选摘要、问题、答案和提示，只有点击“确认覆盖”为 1 张后才会真正替换。

补漏请求改为后台任务模式。提交后立即返回任务编号，用户可以关闭对话窗口，任务仍由 Python 进程继续执行；前端通过任务状态接口读取排队、证据定位、模型核对、卡片写入和成功/失败阶段。原同步补漏端点保留，供旧客户端兼容使用。

今日资料标题上的拖拽手柄同时支持文件夹投放。用户把整份文档拖到文件夹卡片并松手后，前端调用既有批量归档接口，只改变该文档的文件夹归属；拖拽经过今日队列造成的临时排序会恢复，不额外写入资料优先级。主页面资料归档区支持逐份勾选和“全选未归档资料”，选择目标文件夹后一次批量进入；触屏与键盘用户无需使用拖拽也能完成批量归档。主页面和文件夹详情的文档排序均在松手位置显示虚影，松手后一次提交完整顺序，失败时恢复原顺序。

结构化视频和普通学习资料均不设置卡片数量上限。提炼前先从整份清洗后 evidence 中提取讲者、课件或字幕已经明确列出的原始问题，同时由 LangExtract 从陈述式讲解中发现定义、机制、流程、因果、对比和实践结论。每个独立且通过 evidence 门禁的知识点都可以生成卡片；仅合并重复事实，不因数量抽样或截断候选。服务端继续逐卡执行问题完整性、答案忠实度和 evidence 引用门禁，不用数量目标放宽质量要求。

所有 AI 生成、补漏和改写卡片的 `question` 都必须模拟真实面试官向候选人提问，优先使用“请你解释一下……？”、“你会如何……？”、“为什么……？”、“如果……你会怎么处理？”等自然问法。资料中的标题、考点或主动回忆指令需要转换成完整面试问题；不得直接发布教材标题、名词短语、学习任务或自问自答。面试问法只改变卡面表达，答案仍只能使用当前资料 evidence 支撑的事实。

新增卡片级和资料组级删除。卡片删除会立即停用卡片并保留稳定来源键排除记录，后续同步或重新生成不得恢复同一卡片；资料组删除会停用该资料的全部复习卡片并写入资料排除记录，后续自动同步、索引完成回调和手动生成都必须跳过。资料组删除只影响复习中心，不删除用户上传的原始文件、RAG 文档、切块或 evidence；既有 FSRS 评分日志继续保留，避免删除后篡改“今日已完成”等历史统计。

每份复习资料新增一份由复习模型生成的摘要，主路径默认使用 `gpt-5.6-terra`。`learning_material.document_summary` 是 RAG 索引摘要，可能只是截断后的开头内容，不能直接作为复习总结展示；它只作为本地前置过滤和模型判断资料范围的辅助输入。本地只判断资料是否值得进入复习中心并分配内部类别；通过过滤后的复习摘要、问题、答案和提示必须在同一次模型请求中完整生成并持久化，不能由本地规则拼写、补齐或降级生成。每日卡片 group 与资料列表都返回同一份模型摘要。

今日复习资料 group 支持用户拖拽排序。排序以“当前用户 + 复习资料”为持久化单位保存在 PostgreSQL；前端提交当前可见 group 的完整顺序后，服务端将这些资料置于用户队列前部，其余复习资料保持原有相对顺序。该顺序影响 `GET /api/reviews/due` 与 `GET /api/reviews/due-groups` 的资料选择顺序，但不改变卡片组内到期顺序、FSRS 状态或评分算法。

复习中心的高频读取遵循“先取稳定 ID，再按 ID 批量读取状态”的策略：未归档资料列表先读取 `learning_material`，再批量读取 `learning_review_material`；文件夹详情先读取文件夹关联表中的 `material_id/display_order`，再批量读取资料和复习状态。主页面到期队列中仅用于判断“是否已归档”的文件夹联表改为带索引的 `NOT EXISTS`。需要在同一事务中锁定资料、校验索引版本或完成队列选取的查询保留必要联表，避免为减少 `JOIN` 而增加往返次数或破坏一致性。高频条件对应联合索引已登记在 `20260805_0200_optimize_review_query_indexes.sql`。

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
- 资料先执行确定性的本地前置过滤，只判断是否属于学习资料并分配内部类别；纯时间码、字幕水印、口头语、会议纪要、日志、歌词等杂项直接写入 `SKIPPED`，不调用 `gpt-5.6-terra`。通过过滤后，独立的复习 PAE/ReAct LangGraph 执行“规划—LangExtract Curator—Actor—单卡 Observer—多卡 Observer—保存/定向合并”循环。Curator 对完整 evidence 只运行一次，Actor Repair 复用候选，不重复执行长文抽取；多卡 Observer 只输出结构化合并计划，Merge Repair 只改点名卡片组并重新进入单卡 Observer。图的 `recursion_limit` 固定为 `999`，卡片 `gpt-5.6-terra` 真实调用默认最多 `8` 次，可通过 `REVIEW_GENERATION_MAX_ATTEMPTS` 调整；多卡合并默认最多 `4` 轮，可通过 `REVIEW_GENERATION_MAX_MERGE_ROUNDS` 调整，两个计数相互独立；递归上限不能被解释为 999 次模型请求。
- `REVIEW_LLM_API_KEY` 缺失等不可执行错误写入 `FAILED`。`DEEPSEEK_API_KEY` 或 `REVIEW_LLM_FALLBACK_API_KEY` 仅用于主中转失败后的 DeepSeek 降级，不能代替主密钥。模型输出未通过问题完整性、结构化问题覆盖与 evidence 门禁时，观察节点必须形成逐项中文诊断并送入下一轮 Prompt；尝试耗尽或 LangGraph 递归异常后写入 `NEEDS_REVIEW`，保留该资料当前活动卡片，并持久化 `generationAttempts` 与 `qualityFeedback`。`NEEDS_REVIEW` 不参加后台自动重试，用户可在决策弹窗中保留旧版、输入提示词重试、降低门禁或选择分段合并。
- `FAILED` 和 `NEEDS_REVIEW` 资料的 `reason` 是可诊断字段，前端资料分组会直接展示失败或人工处理原因。服务启动日志也会提示密钥缺失，但不会阻止其他 RAG 接口启动。Windows 本地开发中，若长时间运行的 PyCharm 没有继承新设置的用户环境变量，服务会只读当前用户的 `HKCU\Environment` 并把 `REVIEW_LLM_API_KEY`、`DEEPSEEK_API_KEY` 等实际已配置密钥注入当前进程，随后由 `run.py` 启动的 API 与 worker 统一继承；不会读取其他账户、不会把密钥写入配置文件或日志。
- 复习生成期间，`ReviewMaterial.generationProgress` 持久化当前阶段和最近 12 条事件。阶段包括 evidence 整理、Planner 规划、LangExtract 知识发现、`gpt-5.6-terra` 生成、单卡 Observer、多卡 Observer、Merge Repair、Repair 自动修复、卡片保存和人工处理；多卡阶段会记录 `mergeRound/maxMergeRounds`，Actor 阶段记录 `attempt/maxAttempts`。Curator 会公开原始候选、精确定位候选、最终选择数与模型请求数。`percent`、`currentStep/totalSteps`、`attempt/maxAttempts`、合并轮次与 `detail` 可直接驱动前端进度条和流程时间线。`app.workers.review_task_worker` 每 2 秒从 PostgreSQL 原子领取 `review.queued` 或超过 20 分钟未更新的 `GENERATING` 任务，使用 `FOR UPDATE SKIP LOCKED` 防止多个 worker 重复领取，默认按 2n=16 并发恢复。服务重启后无需再次打开页面即可继续处理；恢复失败只更新诊断，已有活动卡片保持可用。
- 复习功能的所有 LLM Prompt 统一放在 `ai-python/prompts/`，当前资料提炼卡片版本为 `review-card-v15`，单卡片改写 Prompt 为 `review-card-rewrite-v2`，资料级改写 Prompt 为 `review-material-rewrite-v3`。资料级改写的 `targetCardCount` 只要求为正整数，不设固定上限；模型必须返回与目标数量一致的 `cards` 数组。v15 在知识单元覆盖、问题清洗、`sourceQuestion(s)` 多问题审计、合并/不合并边界、问题不泄露答案和增量 Repair 基础上，明确要求所有 `question` 模拟真实面试官向候选人提问，并继续要求安全 Markdown 与 evidence 忠实度。资料提炼会把全部 LangExtract `knowledgeUnitId` 传入模型，模型必须为卡片回传覆盖 ID，服务端校验候选 evidence 与卡片 evidence 至少有一项重合，并拒绝未达到所选门禁档位的结果；合并卡会保留多个原始问题、knowledge unit、evidence 和答案论断并集。`gpt-5.6-terra` 空响应或非法 JSON 在当前质量轮内最多短程重试 3 次，不消耗 LangGraph 质量修复轮次。逐论断答案仍必须由所引用 evidence 支撑，业务模块不得生成或改写面向用户的摘要、问题、答案与提示。
- 所有复习 LLM 调用主路径统一使用本机 OpenAI-compatible Cockpit 中转 `http://localhost:58966/v1` 的 `gpt-5.6-terra`，显式开启 `thinking.type=enabled`，思考强度固定为 `reasoning_effort=max`。主密钥读取 `REVIEW_LLM_API_KEY`。项目客户端按 Cockpit“长等待方案”给内部账号与平台轮换留出完整窗口：流打开窗口 180 秒、空闲窗口 240 秒、启动重试 1 次、请求重试 1 次、退避 300-1500 毫秒；因此默认客户端等待窗口为 615 秒。连接失败、超时、429、5xx、Cockpit 账号授权失效导致的 401/403 会先向 Cockpit 重试一次，只有两次 Cockpit 请求都失败后才允许直连 DeepSeek `deepseek-v4-flash`。400、404、422 等确定性请求错误不在 Cockpit 重试，避免错误请求形成重试风暴。进度、摘要和失败原因必须区分“Cockpit 重试”与“DeepSeek 降级”。不得继承通用 `RAG_LLM_MODEL`、`DASHSCOPE_API_KEY` 或其他代理配置。
- 视频、面经和讲解类资料在送模前先从原始 transcript evidence 抽取原始问句候选，父段摘要与 OCR 转场不得进入候选。模型必须优先选择资料中已经明确提出、且由后续原文回答的重点问题；单问题卡兼容回传 `sourceQuestion`，合并卡通过 `sourceQuestions/coveredSourceQuestionKeys` 保留多个原始问题覆盖。最终 `question` 由本次实际调用模型输出为去除口头语、指代完整、可脱离上下文独立理解且不提前泄露答案的面试问题。只有没有合适原问句时才允许根据重点事实生成新问题，且不得生成脱离资料表述的泛化问题。
- 发布前质量门禁必须逐卡拒绝：`父段摘要：`、时间码或 OCR 水印；“那是什么意思”“这些是什么”等无上下文指代；陈述句、转场句和未完成问句；“本节关键知识点是什么”等泛化占位题；答案为空、答案与问题明显错位、引用不存在或答案缺少 evidence 支撑。学习资料还必须包含一份非空模型摘要和至少一张通过门禁的卡片，否则整次结果为 `FAILED`。
- evidence 在本地前置过滤阶段会移除纯时间码、重复字幕水印和无事实内容的口头转场；未通过学习内容过滤的资料直接跳过且不创建卡片，历史旧版本卡片会在增量同步时停用并重建。本地过滤不得生成或改写资料总结、问题、答案和提示。
- 用户删除使用 PostgreSQL tombstone 作为唯一事实来源。`learning_review_card_exclusion` 按 `materialId + sourceKey` 阻止同一卡片复活，`learning_review_material_exclusion` 按 `materialId` 永久阻止整份资料再次进入复习中心；不能只依赖前端隐藏或 Redis 缓存。
- 删除接口必须幂等并校验当前用户归属。卡片删除与并发评分使用行锁串行化；资料删除与并发生成共同锁定 `learning_material`，保证最终状态要么完整生成、随后被删除，要么生成结果在保存前被排除。
- PostgreSQL 的 `dueAt` 和评分日志是唯一事实来源，不缓存卡片排程；配置 `REDIS_URL` 时只使用带 TTL 的生成短锁，防止多实例对同一资料重复调用 LLM。
- 资料拖拽顺序保存在 `learning_review_material.display_order`。批量排序先锁定当前用户仍在复习中心的资料行并校验全部 ID，再在同一事务中连续重编号；重复提交同一顺序不改写未变化的行。未设置顺序的新资料追加在已排序资料之后，并保留现有到期时间兜底顺序。

## 模型运行配置

复习服务默认请求本机 OpenAI-compatible Cockpit 中转 `http://localhost:58966/v1` 的 `gpt-5.6-terra`。请求同时携带 `thinking={"type":"enabled"}` 与 `reasoning_effort="max"`，不传 `temperature`；`REVIEW_LLM_BASE_URL`、`REVIEW_LLM_MODEL`、`REVIEW_LLM_REASONING_EFFORT` 和 `REVIEW_LLM_THINKING_ENABLED` 可通过环境变量覆盖。项目关闭 OpenAI SDK 的隐式重试，由统一 Cockpit 策略显式记录每次尝试。`REVIEW_LLM_FALLBACK_ENABLED=true` 且存在 `DEEPSEEK_API_KEY` 或 `REVIEW_LLM_FALLBACK_API_KEY` 时，才会在 Cockpit 尝试耗尽后执行一次 DeepSeek 降级。

Cockpit 重试参数默认与本机“长等待方案”保持一致：

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `REVIEW_COCKPIT_STREAM_OPEN_TIMEOUT_SECONDS` | `180` | Cockpit 等待上游开始返回流的单次窗口 |
| `REVIEW_COCKPIT_STREAM_IDLE_TIMEOUT_SECONDS` | `240` | 流建立后允许无新数据的窗口 |
| `REVIEW_COCKPIT_BOOTSTRAP_RETRIES` | `1` | Cockpit 流启动失败后的重试次数 |
| `REVIEW_COCKPIT_REQUEST_RETRIES` | `1` | 项目客户端在降级前重新请求 Cockpit 的次数 |
| `REVIEW_COCKPIT_RETRY_BASE_DELAY_MS` | `300` | 首次重试基础退避 |
| `REVIEW_COCKPIT_RETRY_MAX_DELAY_MS` | `1500` | 指数退避上限 |
| `REVIEW_COCKPIT_KEEPALIVE_SECONDS` | `15` | 与 Cockpit SSE keepalive 配置一致，纳入等待预算和诊断 |
| `REVIEW_EXTRACTION_TIMEOUT_SECONDS` | `615` | 单次 Cockpit 请求等待窗口；默认覆盖两次 180 秒流打开、一次 240 秒空闲和 15 秒余量 |

线上默认并发与预算：

本项目并发基准固定为 `n=8`：模型、LangExtract、embedding、rerank、OCR、ASR 和数据库等待等 I/O 阶段默认 `2n=16`；视频解码、递归切块和本地计算等 CPU/内存阶段默认 `n+1=9`。环境变量仍可按实际配额下调。

| 环节 | 默认值 | 说明 |
| --- | ---: | --- |
| `LLM_IO_MAX_WORKERS` | `16` | Agent、RAG、复习、简历、OCR/ASR 等在线模型请求共用的专用 I/O 线程池，硬上限 64 |
| `REVIEW_LANGEXTRACT_MAX_WORKERS` | `16` | 同一 pass 内并发处理文本块；模型等待属于 I/O，硬上限 64 |
| `REVIEW_LANGEXTRACT_EXTRACTION_PASSES` | `2` | 两轮串行执行，提高召回；不会跨 pass 并发 |
| `REVIEW_LANGEXTRACT_MAX_CHAR_BUFFER` | `8000` | 单个文本块字符预算 |
| `REVIEW_LANGEXTRACT_MAX_MODEL_REQUESTS` | `32` | 单份资料 LangExtract 请求总预算 |
| `REVIEW_LANGEXTRACT_TIMEOUT_SECONDS` | `120` | 单个 LangExtract 请求超时 |
| `REVIEW_DEEPSEEK_MAX_IN_FLIGHT` | `16` | 卡片生成请求的进程级 I/O 并发闸门 |
| `REVIEW_TASK_WORKER_ENABLED` | `true` | 是否由 `run.py` 启动复习生成恢复 worker |
| `REVIEW_TASK_WORKER_CONCURRENCY` | `16` | 同时执行的资料级复习生成数，模型等待属于 I/O |
| `REVIEW_TASK_WORKER_BATCH_SIZE` | `16` | 每轮最多从 PostgreSQL 原子领取的任务数 |
| `REVIEW_TASK_WORKER_POLL_SECONDS` | `2` | 无可用任务时的轮询间隔 |
| `REVIEW_TASK_WORKER_STALE_SECONDS` | `1200` | `GENERATING` 状态多久未更新后允许重新领取 |

LangExtract provider 内部使用 `ThreadPoolExecutor` 并行等待当前实际模型 HTTP；项目在客户端代理外再增加进程级共享闸门，默认按 2n=16 控制 I/O 并发，避免多份资料叠加后形成无界请求数。配置超过 64 时回退或限制在安全范围，降低配置不当造成的限流和内存压力。

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
| PUT | `/api/reviews/folders/{folderId}/materials/order` | 批量保存当前用户文件夹内文档的拖拽顺序 |
| GET | `/api/reviews/materials` | 获取主页面未归档资料的分类、生成状态和卡片数；已归档资料仅在文件夹详情返回 |
| PUT | `/api/reviews/materials/folder` | 以文档为单位批量移入指定文件夹，`folderId=null` 时移出文件夹 |
| POST | `/api/reviews/materials/{materialId}/generate` | 对一条当前用户资料重新分类并生成卡片，可携带人工补充说明 |
| POST | `/api/reviews/materials/{materialId}/rewrite-preview` | 兼容旧客户端：同步生成资料级前后对比候选，不写数据库 |
| POST | `/api/reviews/materials/{materialId}/rewrite-tasks` | 创建资料级后台合并/重新生成任务，立即返回任务编号 |
| GET | `/api/reviews/materials/{materialId}/rewrite-tasks/latest` | 获取当前资料最近一次合并/重新生成任务 |
| GET | `/api/reviews/materials/{materialId}/rewrite-tasks/{taskId}` | 查询资料级改写任务的阶段、进度和对比结果 |
| POST | `/api/reviews/materials/{materialId}/rewrite-apply` | 携带预览版本和用户确认后的任意数量候选，事务内替换旧卡片 |
| POST | `/api/reviews/materials/{materialId}/cards` | 创建一张用户手动复习卡片，不依赖 AI 补漏 |
| POST | `/api/reviews/materials/{materialId}/missing-knowledge` | 兼容旧客户端：同步查找并追加遗漏知识点 |
| POST | `/api/reviews/materials/{materialId}/missing-knowledge/tasks` | 创建后台补漏任务，立即返回任务编号 |
| GET | `/api/reviews/materials/{materialId}/missing-knowledge/tasks/latest` | 获取当前资料最近一次补漏任务，便于关闭弹窗后恢复查看 |
| GET | `/api/reviews/materials/{materialId}/missing-knowledge/tasks/{taskId}` | 查询指定补漏任务的阶段、进度和结果 |
| POST | `/api/reviews/materials/batch-delete` | 批量将多份资料移出复习中心 |
| DELETE | `/api/reviews/materials/{materialId}` | 将整份资料永久移出复习中心，保留原始 RAG 文件 |
| GET | `/api/reviews/cards/library` | 按文档读取当前用户全部活动卡片，包含已复习卡片和完整 Markdown 正文，不提供评分 |
| GET | `/api/reviews/folders` | 获取当前用户的文件夹及文档、卡片和到期统计 |
| POST | `/api/reviews/folders` | 新建复习文件夹 |
| GET | `/api/reviews/folders/{folderId}` | 进入文件夹并按文档读取全部活动卡片，答案保持隐藏 |
| PATCH | `/api/reviews/folders/{folderId}` | 重命名复习文件夹 |
| DELETE | `/api/reviews/folders/{folderId}` | 删除文件夹并把其中资料改为未归档 |
| GET | `/api/reviews/cards/{cardId}` | 用户主动揭示答案时获取答案、提示和原文 evidence |
| POST | `/api/reviews/cards/{cardId}/rewrite-preview` | 兼容旧客户端：同步生成原卡片—新卡片的无副作用 LLM 对比预览 |
| POST | `/api/reviews/cards/{cardId}/rewrite-tasks` | 创建单卡片后台改写任务，立即返回任务编号 |
| GET | `/api/reviews/cards/{cardId}/rewrite-tasks/latest` | 获取当前卡片最近一次后台改写任务 |
| GET | `/api/reviews/cards/{cardId}/rewrite-tasks/{taskId}` | 查询单卡片改写任务的阶段、进度和对比结果 |
| PUT | `/api/reviews/cards/{cardId}` | 应用用户编辑或确认后的 AI 卡片正文，保留 FSRS 状态 |
| POST | `/api/reviews/cards/{cardId}/grade` | 提交四档回忆结果并更新 FSRS 状态 |
| POST | `/api/reviews/cards/batch-delete` | 批量删除多张复习卡片 |
| DELETE | `/api/reviews/cards/{cardId}` | 删除一张卡片并阻止同一稳定来源卡片再次生成 |
| PUT | `/api/reviews/settings` | 更新复习提醒开关、目标记忆率、每日上限和提醒时间 |

### 查看全部卡片

```http
GET /api/reviews/cards/library
Authorization: Bearer <token>
```

响应按文档返回全部活动卡片，`reviewCount > 0` 的已复习卡片不会被过滤；卡片答案和 evidence 在此只读管理页面中直接返回。`totalMaterialCount`、`totalCardCount`、`reviewedCardCount` 提供页面统计，文档项同时返回文件夹归属、资料总结和全部卡片。该接口不改变到期状态，也不提供评分语义。

### 直接修改卡片

```http
PUT /api/reviews/cards/81
Content-Type: application/json
Authorization: Bearer <token>

{
  "question": "ISR 的核心作用是什么？",
  "answer": "- **跟踪**与 Leader 保持同步的副本\n- 为故障后的 Leader 选举提供候选",
  "hint": "回忆 `Leader` 与同步副本集合"
}
```

`question` 最长 500 字，`answer` 最长 5000 字，`hint` 最长 1000 字；答案和提示只去除首尾空白并保留 Markdown 结构。服务端按认证用户锁定活动卡片，只更新正文和 `updatedAt`，不修改 `fsrs_card_json`、`dueAt`、`reviewCount`、`lapseCount` 或评分日志。首次修改模型卡片后，响应中的 `isUserEdited=true`。

### LLM 三档改写与对比

新客户端通过后台任务接口提交改写，HTTP 请求不会等待 LLM 完成：

```http
POST /api/reviews/cards/81/rewrite-tasks
Content-Type: application/json
Authorization: Bearer <token>

{
  "instruction": "把答案改成三步列表，并突出容易混淆的术语",
  "mode": "SOURCE_FIRST"
}
```

响应中的 `status` 为 `QUEUED`、`RUNNING`、`SUCCEEDED` 或 `FAILED`，并包含 `taskId`、原始 `instruction`、`mode`、当前 `progress`、最终 `result` 或中文 `error`。前端可关闭弹窗并继续操作；重新打开时调用 `latest`，运行中每约 1.2 秒按任务编号查询。`SUCCEEDED` 后的 `result` 与下方同步预览响应结构相同，仍只用于前后对比，必须由用户点击应用后才会覆盖原卡片。同一用户、同一卡片已有运行任务时会复用该任务，避免重复调用模型。

任务进度当前保存在 Python API 进程内，服务重启后不会恢复未完成或历史任务；此时用户可以重新提交。同步 `rewrite-preview` 仅保留给旧客户端兼容。

```http
POST /api/reviews/cards/81/rewrite-preview
Content-Type: application/json
Authorization: Bearer <token>

{
  "instruction": "把答案改成三步列表，并突出容易混淆的术语",
  "mode": "SOURCE_FIRST"
}
```

`mode` 可选：

- `STRICT_SOURCE`：严格依赖原文，候选答案必须引用真实 evidence，预览和应用阶段都执行忠实度校验。
- `SOURCE_FIRST`：尽量以原文为主，允许轻量解释与结构重组。
- `SOURCE_REFERENCE`：原文仅参考，优先满足用户的表达和补充想法；模型没有引用原文时 `evidenceRefs` 可为空。

成功响应包含 `original`、`proposed`、`evidenceRefs` 和 `modelName`，不会写入卡片。用户可直接回退关闭，也可在前端继续编辑 `proposed` 后使用 `PUT /api/reviews/cards/{cardId}` 应用；AI 应用请求额外携带本次 `rewriteMode` 与 `evidenceIds`，服务端重新校验证据属于当前用户资料。严格档位下，用户二次编辑造成答案脱离引用原文时，应用会返回“编辑后的答案未通过严格原文忠实度校验”。

### 资料级合并改写与确认覆盖

资料合并/重新生成同样优先使用后台任务接口：

```http
POST /api/reviews/materials/12/rewrite-tasks
Content-Type: application/json
Authorization: Bearer <token>

{
  "instruction": "保留这次生成的内容，新添一张卡片，说明旧项目表不可修改时如何维护 inbox 和 outbox",
  "mode": "SOURCE_FIRST",
  "targetCardCount": 2
}
```

关闭弹窗不会取消任务；重新打开时通过 `GET /api/reviews/materials/12/rewrite-tasks/latest` 恢复，再查询具体任务。`targetCardCount` 可指定任意正整数；不传时服务端会识别“新增一张/返回两张”等中文要求，自动推断总数为 2。用户在已有预览上点击“重新生成候选”时，前端会把当前编辑后的候选作为 `baseCards` 一并提交；服务端将这些基础候选原样固定为结果前缀，只采用模型生成的末尾新增卡，硬保证“保留这次生成的内容”不会被模型重新改写。完成后的 `result.proposedCards` 数量必须等于目标数量；数据库仍保持不变。用户确认后才调用 `rewrite-apply`，应用接口在一个事务内停用旧活动卡片并插入全部确认后的候选，不设固定数量上限。同一用户、同一资料的运行任务会被复用。任务也采用进程内状态，Python API 重启后需重新提交。

当旧项目表属于只读遗留表时，不应直接修改它：

- `Inbox` 表接收外部消息或待处理候选，保存幂等键、原始 payload、处理状态、重试次数和错误信息，供消费者安全重放。
- `Outbox` 表与本地业务事务一起记录待发布事件；事务提交后由发布器投递到 Kafka/任务队列，成功标记 `PUBLISHED`，失败按退避策略重试。它解决“数据库已写入但消息没有发出去”的双写问题。
- 复习卡片场景可把 AI 候选先写入 Inbox，用户确认后在事务中写入新卡片并写 Outbox 事件；旧表只读，旧卡片通过排除记录或新表视图隐藏，不需要回写遗留表。

```http
POST /api/reviews/materials/12/rewrite-preview
Content-Type: application/json
Authorization: Bearer <token>

{
  "instruction": "将当前 4 张 Kafka 卡片合并成 1 张综合卡片，保留零拷贝、压缩、批量发送、分区、顺序写和页缓存",
  "mode": "SOURCE_FIRST"
}
```

预览响应包含 `originalCards`、目标数量的 `proposedCards`、`targetCardCount`、`originalSummary`、`proposedSummary`、`sourceVersion` 和 `originalFingerprint`，不会改变数据库。用户可以逐张编辑候选内容后提交：

```http
POST /api/reviews/materials/12/rewrite-apply
Content-Type: application/json
Authorization: Bearer <token>

{
  "sourceVersion": 7,
  "originalFingerprint": "<预览响应中的指纹>",
  "originalCardIds": [81, 82, 83, 84],
  "proposedSummary": "Kafka 高性能设计的综合复习总结",
  "proposedCards": [{
    "question": "Kafka 如何实现高性能？",
    "answer": "保留并组织四张原卡片的核心机制……",
    "hint": "回忆分区、顺序写、页缓存和零拷贝",
    "rewriteMode": "SOURCE_FIRST",
    "evidenceIds": ["chunk-1", "chunk-2"]
  }, {
    "question": "旧项目表不可修改时，Inbox 和 Outbox 如何协作？",
    "answer": "Inbox 负责接收和暂存待处理消息；Outbox 在事务内记录待发布事件，由发布器可靠投递并按幂等键去重。",
    "hint": "回忆接收、事务记录、发布和重试",
    "rewriteMode": "SOURCE_FIRST",
    "evidenceIds": ["chunk-3"]
  }]
}
```

应用时服务端会再次校验资料版本、活动卡片 ID 和正文指纹；任一变化都会拒绝覆盖并要求重新预览。确认成功后，旧卡片停用并写入来源排除记录，新卡片使用 `custom:` 来源键保留用户修改；旧卡片不会在后续自动同步中复活。

### 用户手动创建复习卡片

```http
POST /api/reviews/materials/12/cards
Content-Type: application/json
Authorization: Bearer <token>

{
  "question": "类方法如何定义和调用？",
  "answer": "使用 @classmethod 定义，第一个参数通常命名为 cls；调用时可以通过类或实例调用。",
  "hint": "回忆 cls 与 self 的区别"
}
```

手动卡片使用与 AI 卡片相同的评分和 FSRS 排程，但 `sourceType` 为 `MANUAL`，`evidenceRefs` 为空；它表示用户主动补充的复习内容，不代表该答案已通过当前资料 RAG 原文门禁。

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

### 后台补漏任务

```http
POST /api/reviews/materials/12/missing-knowledge/tasks
Content-Type: application/json
Authorization: Bearer <token>

{
  "message": "视频后半段还讲了页缓存和零拷贝，请找出漏掉的知识点",
  "conversation": []
}
```

成功响应仍使用 `Result<T>`，任务状态为 `QUEUED` 或 `RUNNING`：

```json
{
  "taskId": "missing-knowledge-7a7d4c4b",
  "materialId": 12,
  "message": "视频后半段还讲了页缓存和零拷贝，请找出漏掉的知识点",
  "status": "QUEUED",
  "progress": {
    "stageCode": "missing.queue",
    "stageLabel": "后台排队",
    "message": "已收到补漏请求，任务将在后台继续执行",
    "status": "RUNNING",
    "percent": 0,
    "events": []
  },
  "result": null,
  "error": null
}
```

前端关闭对话窗口不会取消任务；重新打开同一资料的“补充遗漏”入口时，先调用 `latest` 接口恢复最近任务，再按任务编号轮询详情。`status` 可能为 `QUEUED`、`RUNNING`、`SUCCEEDED` 或 `FAILED`。成功时 `result` 使用同步接口的 `ReviewMissingKnowledgeResult` 结构；失败时 `error` 为可直接展示的中文业务错误。任务状态当前保存在 Python 服务进程内，服务重启后不保留未完成任务。

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
      "sourceType": "RAG",
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

`materialSummary` 只取当前 `review-card-v15` 复习提炼阶段生成并持久化的模型摘要，不回退到 RAG 的 `document_summary`。`folderId/folderName` 表示资料当前归档位置，为空时定位到主页面“资料归档”列表；非空时前端跳转 `/reviews/folders/{folderId}?materialId={materialId}`，文件夹详情自动展开、滚动并高亮对应文档。`limit` 表示最多选择多少份资料，不是卡片数量；每个返回的 group 会包含该资料全部当前到期卡片。`remainingToday` 表示当天还可开始复习的新资料份数。

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

### 保存文件夹内文档顺序

```http
PUT /api/reviews/folders/7/materials/order
Content-Type: application/json
Authorization: Bearer <token>

{"materialIds": [21, 12, 8]}
```

`materialIds` 使用与资料 group 排序相同的校验规则，但服务端只在指定文件夹边界内校验和写入。请求中的每个 ID 必须属于当前认证用户的目标文件夹；任一资料不属于该文件夹时整次请求失败，不会部分更新。文件夹内新归档的资料默认追加到末尾，排序成功后详情接口按 `display_order` 返回文档。

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
| evidence 清洗后只剩字幕水印、时间码或口头噪声 | 本地过滤记录为 `SKIPPED`，不调用复习模型，不生成卡片 |
| `REVIEW_LLM_API_KEY` 未配置 | 分类记录为 `FAILED`，停用当前活跃卡片，显示“未配置 REVIEW_LLM_API_KEY”，等待重新生成 |
| Terra 主中转与可选 DeepSeek 降级均不可用 | 分类记录为 `FAILED`，停用当前活跃卡片，不发布本地降级内容，后续可重试 |
| 复习模型返回非法 JSON 或未通过质量门禁 | 分类记录为 `FAILED`，记录不含模型隐私内容的中文原因，不发布部分坏卡 |
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
