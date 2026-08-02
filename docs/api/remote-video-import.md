# 公公开视频链接接入 API

更新日期：2026-08-02

## 可行性与范围

当前版本只支持 Bilibili 完整公开视频链接，例如：

```text
https://www.bilibili.com/video/BV1xx411c7mD
```

不支持抖音、Bilibili 短链接、直播、登录可见、会员、付费、番剧、DRM 或地区受限内容。服务端不读取浏览器 Cookie，不接受用户提交 Cookie，也不执行验证码、挑战签名或 DRM 绕过。

调研依据：

- [yt-dlp 支持站点](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) 当前列出 BiliBili 与 Douyin。
- [Bilibili 提取器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) 支持公开视频播放信息；部分格式明确需要登录或会员。
- [Douyin 提取器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py) 明确要求新鲜 Cookie，挑战签名仍为待实现项，因此不作为生产能力开放。
- [抖音开放平台视频管理](https://open.douyin.com/platform/resource/docs/openapi/video-management/video-list/) 面向已授权账号的视频数据，不提供任意分享链接的公开视频下载能力。

## 公开接口

### 创建链接导入任务

```http
POST /api/rag/materials/url
Authorization: Bearer <token>
Content-Type: application/json
```

请求：

```json
{
  "url": "https://www.bilibili.com/video/BV1xx411c7mD?p=1",
  "highPrecision": false,
  "confirmedAuthorized": true
}
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `url` | 是 | 只允许 `https://www.bilibili.com/video/{BV/av}` 完整链接；仅保留合法分 P 参数 |
| `highPrecision` | 否 | 是否启用现有高精度视频解析 |
| `confirmedAuthorized` | 是 | 用户确认有权为学习目的处理该内容 |

成功响应沿用 `Result<RagMaterialResponse>`。接口返回时资料通常为 `PENDING`，下载、ASR、关键帧 OCR、递归切块、索引和复习卡片生成均在后台继续：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 31,
    "title": "Bilibili 视频 BV1xx411c7mD",
    "userId": "7",
    "documentType": "mp4",
    "source": "bilibili",
    "status": "PENDING",
    "storageType": "remote",
    "publicUrl": "https://www.bilibili.com/video/BV1xx411c7mD?p=1",
    "chunkCount": 0
  }
}
```

同一用户重复提交相同规范化 URL 时，如果已有活动或已完成资料，直接返回既有资料；已有 `FAILED` 资料时复用原资料并创建更高 `requestVersion` 的重试任务。数据库通过部分唯一索引保证同一用户不会重复创建相同远程资料。

## 异步生命周期

```text
校验 Bilibili URL 与用户授权确认
  -> 同事务创建 learning_material 与 INDEX_REMOTE_VIDEO 耐久任务
  -> worker 获取任务并下载到受控临时目录
  -> 校验非直播、DRM、访问级别、元数据时长、真实媒体时长、累计下载字节和 Bilibili extractor
  -> 复用现有视频 ASR、关键帧 OCR、递归切块与索引
  -> 提升 staging 索引并更新资料 READY/PARTIAL/FAILED
  -> 删除临时视频和字幕文件
```

远程视频不永久复制到本地上传目录或 OSS。资料只保存规范化 Bilibili 页面 URL；重建索引时重新创建 `INDEX_REMOTE_VIDEO` 任务。

## 安全与资源约束

- URL 必须使用 HTTPS，禁止用户名密码、非默认端口、IP 地址、任意域名和开放重定向输入。
- 只允许 `bilibili.com`、`www.bilibili.com`、`m.bilibili.com` 的 `/video/BV...` 或 `/video/av...` 路径；`b23.tv` 短链接要求用户先展开。
- 默认最大 `512 MiB`、最长 `4 小时`、网络超时 `20 秒`、下载重试 `2` 次、单次远程资源获取总墙钟时限 `5 小时`，单链接单视频，不处理播放列表、直播和未知时长视频。DASH 音视频分流按任务累计字节，下载后再次检查媒体文件与整个临时目录占用。
- 默认每用户同时处理 `2` 个、近 24 小时最多创建 `10` 个任务；全局同时活动任务上限为 `32`，超限在建档前拒绝。
- 不将第三方响应正文、临时路径、Cookie 或签名 URL 写入日志、Kafka、evidence 或前端。
- 元数据读取、媒体下载、DASH 合并和字幕等后处理共用同一个绝对截止时间；watchdog 在到期时取消 downloader，下载与后处理 hook 也会拒绝继续执行，FFmpeg 子进程使用任务剩余时间作为超时并在到期后终止。超时使用受控中文错误进入既有耐久重试。
- 下载失败时临时目录必须清理；网络瞬时错误和平台 `exceeded the rate limit. Try again later` 等限流提示进入既有耐久重试，权限/会员/删除/格式不支持直接失败。
- FFmpeg 或解析器异常只保留受控中文摘要；包含 `rag-remote-video-*` 的临时目录、第三方命令输出和原始异常文本不得进入 `parseQuality`、Kafka 消息或任务 `result_json`。
- 本地耐久 worker 只按空闲执行槽领取任务，并在远程任务执行期间持续续租；失去租约后停止 staging 写入和终态发布。Kafka poll 与长任务线程解耦，同一分区保持单消息串行提交，数据库执行令牌阻止重复索引，并为 progress/result/DLQ 保留独立控制线程。
- 进程异常遗留的 `rag-remote-video-*` 目录默认在 48 小时后清理，TTL 始终至少比允许的视频时长多 12 小时。
- 生产下载 worker 必须使用独立低权限账号和隔离网络；出站策略需阻断 loopback、RFC1918、link-local、云 metadata 与内部服务网段。应用 URL 白名单不能替代部署层 egress 策略。

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RAG_REMOTE_VIDEO_MAX_BYTES` | `536870912` | 单个远程视频最大字节数 |
| `RAG_REMOTE_VIDEO_MAX_DURATION_SECONDS` | `14400` | 最大时长 |
| `RAG_REMOTE_VIDEO_SOCKET_TIMEOUT_SECONDS` | `20` | 单次网络超时 |
| `RAG_REMOTE_VIDEO_RETRIES` | `2` | yt-dlp 下载重试次数 |
| `RAG_REMOTE_VIDEO_TASK_TIMEOUT_SECONDS` | `18000` | 元数据、下载和 yt-dlp 后处理共用的单次任务总墙钟时限；长任务在线程池执行并由租约/看门狗约束，不要求小于 Kafka `max.poll.interval.ms` |
| `RAG_REMOTE_VIDEO_TEMP_ROOT` | 系统临时目录 | 受控临时下载目录 |
| `RAG_REMOTE_VIDEO_TEMP_TTL_SECONDS` | `172800` | 崩溃遗留临时目录清理 TTL |
| `RAG_REMOTE_VIDEO_USER_DAILY_LIMIT` | `10` | 单用户近 24 小时任务上限 |
| `RAG_REMOTE_VIDEO_USER_ACTIVE_LIMIT` | `2` | 单用户活动任务上限 |
| `RAG_REMOTE_VIDEO_GLOBAL_ACTIVE_LIMIT` | `32` | 全局活动任务上限 |
| `RAG_KAFKA_HANDLER_CONCURRENCY` | `4` | 单个 Kafka worker 的索引长任务并发上限 |
| `RAG_KAFKA_CONTROL_CONCURRENCY` | `1` | 为 progress/result/promote/DLQ 保留的控制消息线程数 |
| `RAG_INDEX_EXECUTION_LEASE_SECONDS` | `180` | Kafka 索引执行令牌租约时长，worker 按不高于三分之一租期续租 |

## 错误契约

| 场景 | 中文错误或终态 |
| --- | --- |
| 未确认内容处理权 | `请先确认你有权处理该视频内容` |
| 抖音链接 | `抖音链接暂不支持：平台要求动态 Cookie 和挑战签名，本系统不绕过访问限制` |
| Bilibili 短链接 | `请粘贴展开后的 Bilibili 完整视频链接` |
| 任意其他 URL / HTTP / 非默认端口 | `当前仅支持 Bilibili 完整公开视频链接` |
| 直播、超时长、超大小 | 资料进入 `FAILED`，错误信息使用受控中文摘要 |
| 单次远程资源获取超过总墙钟时限 | 提示 `Bilibili 视频处理超过任务总时限`，按瞬时故障进入耐久重试，耗尽重试后进入 `FAILED` |
| 平台提示请求频率超限 | 提示 `Bilibili 视频下载暂时失败`，进入耐久重试 |
| DRM、未知时长或真实媒体时长无法校验 | 资料进入 `FAILED`，不进入 ASR/OCR |
| 登录、会员、付费、删除或地区限制 | 资料进入 `FAILED`，提示 `Bilibili 视频无法公开访问` |
| yt-dlp 或 FFmpeg 未安装 | 资料进入 `FAILED`，提示服务端远程视频组件不可用 |

## 前端影响

- 工作台“多模态数据接入通道”和“学习资料”页新增公开视频链接输入。
- 提交成功后复用现有资料进度轮询和 `MATERIAL_UPLOADED_EVENT`，不新增第二套状态机。
- 页面明确展示 Bilibili 可用、抖音暂不支持；不提供 Cookie 上传或登录态导入入口。
- 视频 evidence 使用规范化 Bilibili 页面 URL，并通过 `t` 参数跳到命中的秒点；不会把平台网页 URL 交给站内 HTML5 播放器。

当前仓库的公开控制面由 Python FastAPI 承载。若后续恢复 Java 业务壳，Java 只需按原样代理该接口、从认证会话注入用户身份，并保持 `Result<T>` 契约；远程解析和下载仍归 Python worker。
