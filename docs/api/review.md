# 学习复习与提醒接口文档

更新日期：2026-08-02

## 变更摘要

新增 `/api/reviews/*` 公开控制面。系统在资料完成 RAG 入库后识别八股背诵、面经、课程讲解、技术原理、学习笔记等学习型内容，从已有 RAG evidence 提炼短小的关键知识点卡片，并使用 FSRS 间隔重复算法计算下次复习时间。每日队列以用户上传资料为 group，每个 group 最多包含该资料当前到期的 4 张小卡片，避免单份资料占满全天额度。

新增卡片级和资料组级删除。卡片删除会立即停用卡片并保留稳定来源键排除记录，后续同步或重新生成不得恢复同一卡片；资料组删除会停用该资料的全部复习卡片并写入资料排除记录，后续自动同步、索引完成回调和手动生成都必须跳过。资料组删除只影响复习中心，不删除用户上传的原始文件、RAG 文档、切块或 evidence；既有 FSRS 评分日志继续保留，避免删除后篡改“今日已完成”等历史统计。

复习卡片不得要求用户重新阅读整篇文档或观看整段视频。每张卡片必须保留 `evidenceRefs`，字段与现有 RAG `Evidence` 一致；视频 evidence 必须保留 `startTime/endTime/playbackUrl` 并复用 `/videos` 时间段跳转，其他格式复用 `/preview/material/{id}` 展示原始文本或带章节位置的 RAG 提取视图。

所有端点使用 `Authorization: Bearer <token>`，只允许访问当前用户的数据，并保持 `{code,msg,data}` 的 `Result<T>` 响应信封。请求校验和业务失败返回 HTTP 200、`code=0` 与中文错误说明。

## 方案约定

- 排程算法：`FSRS`，默认目标记忆率 `0.90`，支持用户设置 `0.80-0.97`。
- 评分：`1=忘记`、`2=困难`、`3=记得`、`4=轻松`。忘记必须与“困难”分开，避免排程间隔被错误拉长。
- 首次卡片立即进入待复习队列；首次作答后由 FSRS 直接计算后续间隔，不使用固定的“第 1/2/4/7/15/30 天”硬编码表。
- 默认每日上限 `20` 张。待复习计数由持久化 `dueAt` 实时计算，服务重启后不丢失。
- `dueCount` 表示全部到期积压，`actionableDueCount` 表示扣除今日已完成数量后当前真正可复习的数量；顶部徽标和浏览器通知只使用后者。
- 资料同步按 `learning_material.index_request_version` 与提炼器版本幂等。资料重建索引或 Prompt/本地降级规则升级后，只按资料分批刷新知识点正文和 evidence，不清空已有卡片的 FSRS 学习状态。
- 同一资料的同一 `indexRequestVersion` 最多执行一次 LLM 请求，一次请求生成该 group 的全部卡片；模型提炼失败或未配置模型时使用确定性本地提炼，不能因为外部模型不可用而阻断复习提醒。
- 复习功能的所有 LLM Prompt 统一放在 `ai-python/prompts/`，当前复习卡片版本为 `review-card-v3`；业务模块只负责组装已清洗的动态输入并调用模型。
- evidence 进入模型前会移除纯时间码、重复字幕水印和无事实内容的口头转场；清洗后没有有效知识点的资料不会创建卡片，历史旧版本卡片会在增量同步时停用并重建。
- 用户删除使用 PostgreSQL tombstone 作为唯一事实来源。`learning_review_card_exclusion` 按 `materialId + sourceKey` 阻止同一卡片复活，`learning_review_material_exclusion` 按 `materialId` 永久阻止整份资料再次进入复习中心；不能只依赖前端隐藏或 Redis 缓存。
- 删除接口必须幂等并校验当前用户归属。卡片删除与并发评分使用行锁串行化；资料删除与并发生成共同锁定 `learning_material`，保证最终状态要么完整生成、随后被删除，要么生成结果在保存前被排除。
- PostgreSQL 的 `dueAt` 和评分日志是唯一事实来源，不缓存卡片排程；配置 `REDIS_URL` 时只使用带 TTL 的生成短锁，防止多实例对同一资料重复调用 LLM。

## 公开端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/reviews/sync?limit=1` | 扫描当前用户已入库但尚未同步的一份资料，完成分类和卡片生成 |
| GET | `/api/reviews/overview` | 获取待复习数、今日完成数、下次到期时间和用户设置 |
| GET | `/api/reviews/due?limit=20` | 获取当前到期的关键知识点卡片 |
| GET | `/api/reviews/due-groups?limit=20` | 按上传资料 group 获取今日到期卡片，列表不返回答案正文 |
| GET | `/api/reviews/materials` | 获取资料分类、生成状态和卡片数 |
| POST | `/api/reviews/materials/{materialId}/generate` | 对一条当前用户资料重新分类并生成卡片 |
| POST | `/api/reviews/materials/batch-delete` | 批量将多份资料移出复习中心 |
| DELETE | `/api/reviews/materials/{materialId}` | 将整份资料永久移出复习中心，保留原始 RAG 文件 |
| GET | `/api/reviews/cards/{cardId}` | 用户主动揭示答案时获取答案、提示和原文 evidence |
| POST | `/api/reviews/cards/{cardId}/grade` | 提交四档回忆结果并更新 FSRS 状态 |
| POST | `/api/reviews/cards/batch-delete` | 批量删除多张复习卡片 |
| DELETE | `/api/reviews/cards/{cardId}` | 删除一张卡片并阻止同一稳定来源卡片再次生成 |
| PUT | `/api/reviews/settings` | 更新复习提醒开关、目标记忆率、每日上限和提醒时间 |

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
      "documentType": "mp4",
      "dueCardCount": 3,
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

## 错误与降级

| 场景 | 对外错误或行为 |
| --- | --- |
| 未登录或 token 失效 | 沿用认证模块中文错误 |
| 评分不在 `1-4` | `复习评分必须是 1 到 4` |
| 当日评分达到每日上限 | `今日复习上限已达到` |
| 资料或卡片越权 | `学习资料不存在` / `复习卡片不存在` |
| 重复删除本人已经删除的卡片或资料 | 幂等返回 `deleted=true` |
| 批量 ID 超过 100 个、为空或含非正整数 | 返回请求校验错误，不执行任何批量操作 |
| 批量 ID 包含不存在或不属于当前用户的记录 | 其余命中项仍返回成功，`deletedCount` 小于 `requestedCount` |
| 手动生成已移出复习中心的资料 | `该资料已从复习中心移除` |
| 同一资料正在由其他请求生成 | `该资料的复习卡片正在生成，请稍后刷新` |
| 资料尚未完成索引 | `学习资料尚未完成索引` |
| 无可用 evidence | 分类记录为 `FAILED`，不生成无来源卡片 |
| 百炼模型未配置、超时或返回非法 JSON | 自动使用本地关键知识点提炼，并保留原 evidence |
| FSRS 状态损坏 | 使用当前卡片创建时间重建初始状态并记录受控日志，不回显内部状态 |

## 前端影响

- 新增 `/reviews` 复习中心，按上传资料展示每日 group；每个 group 内展示多张独立小卡片、答案揭示、四档评分、来源 evidence 和下一次复习时间。
- 侧栏新增“复习中心”；顶部通知按钮展示到期数量并跳转到复习中心。
- 页面打开时调用一次 `POST /api/reviews/sync`，之后定时刷新 `overview`；文件上传后会在 RAG 状态进入 `READY/PARTIAL` 时按 `materialId` 调用生成接口，避免上传响应早于 evidence 入库而漏生成卡片，也避免历史候选抢占本次上传。资料同步失败不阻断其他页面。
- 卡片右上角提供图标删除操作；资料 group 标题和右侧资料列表提供“移出复习中心”操作。两种操作都先显示明确确认对话框，资料确认文案必须说明原始 RAG 文件不会删除且移除后不会重新生成。
- 今日卡片支持复选框多选并批量删除，资料列表支持复选框多选并批量移出；批量操作按钮仅在有选中项时出现，并显示选中数量与对应影响范围。
- 删除成功后前端立即移除对应卡片或 group，并分别刷新 `overview`、`due-groups` 和 `materials`；刷新失败不能把已经成功的删除误报为删除失败。
- 浏览器通知只在用户主动授权后启用；后端持久化到期时间是唯一事实来源，前端不能自行计算 FSRS 间隔。
- 浏览器通知会等待用户设置时区中的 `reminderTime`，同一自然日最多发送一次；浏览器关闭后的系统级通知仍需后续接入 Web Push 或邮件基础设施。
