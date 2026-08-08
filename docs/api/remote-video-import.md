# 公公开视频链接接入 API

更新日期：2026-08-07

## 能力范围

当前支持 Bilibili 与抖音两种接入路线：

| 平台 | 支持链接 | 获取方式 | RAG 内容 |
| --- | --- | --- | --- |
| Bilibili | `bilibili.com/video/{BV/av}` 完整 HTTPS 链接 | worker 用匿名 `yt-dlp` 下载临时视频 | 字幕/ASR、关键帧 OCR、视频片段摘要 |
| 抖音 | `douyin.com/video/{aweme_id}`、`iesdouyin.com/share/video/{aweme_id}`、带 `modal_id` 的作品页、`v.douyin.com` 视频短链 | worker 调用 SocialDataX Streamable HTTP MCP | 视频语音转写文本，不包含画面 OCR/视觉理解 |

示例：

```text
https://www.bilibili.com/video/BV1xx411c7mD
https://www.douyin.com/video/6961737553342991651
https://v.douyin.com/iRNBho6u/
```

抖音路线不在本项目下载视频，不读取浏览器 Cookie，也不执行验证码、挑战签名、登录态或 DRM 绕过。MCP 服务负责解析作品链接并生成语音转写，本项目只接收转写结果并进入现有 RAG 索引。

抖音接入依赖 [SocialDataX 抖音 MCP](https://github.com/DevinChen2014/douyin-mcp)：

- Endpoint：`https://mcp.52choujiang.com/douyin/mcp`
- Transport：`streamable-http`
- 鉴权：`Authorization: Bearer <SOCIALDATAX_API_KEY>`
- 使用工具：`douyin_get_video_detail_by_url`、`douyin_submit_video_speech_text_by_video_url`、`douyin_get_video_speech_text_job`

真实密钥只能通过进程/用户环境变量或未提交的 `application.local.yml` 注入，不得写入仓库。

## 公开接口

### 创建单条任务

```http
POST /api/rag/materials/url
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "url": "https://www.douyin.com/video/6961737553342991651?previous_page=web_code_link",
  "highPrecision": false,
  "confirmedAuthorized": true
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `url` | 是 | Bilibili 完整作品页，或受支持的抖音作品页/视频短链 |
| `highPrecision` | 否 | Bilibili 是否启用高精度视频解析；抖音语音转写路线不执行画面解析 |
| `confirmedAuthorized` | 是 | 用户确认有权为学习目的处理该内容 |

接口只完成校验、建档和耐久任务入队，返回 `Result<RagMaterialResponse>`。资料通常先处于 `PENDING`：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 31,
    "title": "抖音视频 6961737553342991651",
    "userId": "7",
    "documentType": "mp4",
    "source": "douyin",
    "status": "PENDING",
    "storageType": "remote",
    "publicUrl": "https://www.douyin.com/video/6961737553342991651",
    "chunkCount": 0
  }
}
```

worker 获得真实标题后会更新资料标题。相同用户重复提交同一规范化 URL 时复用活动或已完成资料；`FAILED` 资料会复用原记录并创建更高 `requestVersion` 的任务。

### 批量创建任务

```http
POST /api/rag/materials/url/batch
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "text": "【Bilibili 课程】https://www.bilibili.com/video/BV1xx411c7mD?vd_source=tracking\n7.23 复制打开抖音 https://v.douyin.com/iRNBho6u/",
  "highPrecision": false,
  "confirmedAuthorized": true
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `text` | 是 | 多行 URL 或平台分享文案，最多 1,000,000 个字符 |
| `highPrecision` | 否 | 为本批次 Bilibili 新任务启用高精度解析 |
| `confirmedAuthorized` | 是 | 用户统一确认有权处理本批次内容 |

服务端从分享文案中提取 HTTP(S) URL，再逐条执行平台白名单和规范化：

- Bilibili 删除追踪参数，只保留合法 `p` 分 P。
- 抖音完整作品页统一为 `https://www.douyin.com/video/{aweme_id}`。
- 抖音短链删除 query/fragment，但 API 进程不跟随重定向；固定 MCP 服务负责解析。
- 批次内相同规范化 URL 只入队一次。
- 单条失败不阻断其他链接。

逐条 `status` 为 `QUEUED`、`REUSED`、`DUPLICATE` 或 `REJECTED`。批量接口没有“每次最多 2 条”的业务限制，超出 worker 槽位的任务保留在耐久队列中。

## 处理流程

### Bilibili 多模态路线

```text
校验并规范化 URL
  -> 同事务创建 learning_material 与 INDEX_REMOTE_VIDEO
  -> worker 匿名下载到受控临时目录
  -> 校验直播、DRM、访问级别、时长和累计字节
  -> 字幕/ASR + 关键帧 OCR + 视频片段摘要
  -> 递归切块 + BM25/向量索引 + staging 提升
  -> 更新 READY/PARTIAL/FAILED
  -> 删除临时视频和字幕
```

### 抖音语音转写路线

```text
校验 HTTPS、域名、路径并规范化 URL
  -> 同事务创建 learning_material 与 INDEX_REMOTE_VIDEO
  -> worker 使用 SOCIALDATAX_API_KEY 建立 MCP 会话
  -> douyin_get_video_detail_by_url 获取标题和 aweme_id
  -> douyin_submit_video_speech_text_by_video_url 提交转写
  -> 未完成时按 job_id 调用 douyin_get_video_speech_text_job
  -> 分段结果转换为 SRT；纯文本结果按文本解析
  -> 标注 sourcePlatform/douyin、awemeId、原始 URL、subtitle evidence
  -> 现有递归切块 + BM25/向量索引 + staging 提升
  -> 更新 READY/FAILED
```

MCP 的 `structuredContent` 和 TextContent JSON 两种响应均可解析。限流、网络、5xx 和超时进入既有耐久重试；缺密钥、鉴权失败、资源失效、无语音或不支持转写属于永久失败。第三方原始错误正文和密钥不会进入日志、Kafka、资料结果或前端。

## 安全约束

- 所有平台 URL 必须使用 HTTPS，禁止用户名密码和非 443 端口。
- 只允许精确白名单域名，不接受后缀伪造域名、IP 地址或任意网络目标。
- Bilibili 只允许完整 `/video/BV...` 或 `/video/av...`；`b23.tv` 仍要求先展开。
- 抖音只允许视频作品页、受控 `modal_id` 作品页和 `v.douyin.com` 短链；图文 `note`、用户页和任意路径不接入语音转写。
- 本项目不跟随抖音短链，不下载抖音媒体，不接收 Cookie/账号口令。
- `SOCIALDATAX_API_KEY` 只允许发送到固定 `https://mcp.52choujiang.com/douyin/mcp`；endpoint 配置若改为其他主机、路径或 HTTP 会拒绝启动该客户端。
- MCP 客户端默认绕过系统 `HTTP_PROXY`/`HTTPS_PROXY`，直接连接固定 endpoint，避免把 SocialDataX 密钥交给通用代理；部署网络若禁止直连，需要先提供受控的网络出口。
- worker 在 MCP 轮询期间继续续租；失去执行权后停止后续轮询、staging 写入和终态发布。
- evidence 保留规范化作品页、作品 ID、转写供应商、字幕片段和可用时间段。

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SOCIALDATAX_API_KEY` | 空 | 抖音 MCP Bearer 密钥；抖音接入必填 |
| `RAG_DOUYIN_MCP_ENABLED` | `true` | 是否启用抖音 MCP 路线 |
| `RAG_DOUYIN_MCP_ENDPOINT` | 官方 endpoint | 仅接受固定 SocialDataX HTTPS 地址 |
| `RAG_DOUYIN_MCP_CONNECTION_TIMEOUT_SECONDS` | `30` | MCP 建连超时，范围 1-120 秒 |
| `RAG_DOUYIN_MCP_TOOL_TIMEOUT_SECONDS` | `60` | 单次 MCP 工具调用超时，范围 5-180 秒 |
| `RAG_DOUYIN_TRANSCRIPT_POLL_INTERVAL_SECONDS` | `5` | 转写任务轮询间隔，范围 0.1-60 秒 |
| `RAG_DOUYIN_TRANSCRIPT_MAX_WAIT_SECONDS` | `900` | 单次 worker 转写总等待时限，范围 30-3600 秒 |
| `RAG_REMOTE_VIDEO_MAX_BYTES` | `536870912` | Bilibili 临时视频最大字节数 |
| `RAG_REMOTE_VIDEO_MAX_DURATION_SECONDS` | `14400` | Bilibili 最大时长 |
| `RAG_REMOTE_VIDEO_TASK_TIMEOUT_SECONDS` | `18000` | Bilibili 下载和后处理总墙钟时限 |
| `RAG_KAFKA_HANDLER_CONCURRENCY` | `4` | Kafka 索引长任务并发上限 |
| `RAG_TASK_WORKER_CONCURRENCY` | `2` | 本地耐久 worker 并发槽位 |
| `RAG_INDEX_EXECUTION_LEASE_SECONDS` | `180` | 索引执行租约时长 |

本地 PowerShell 示例：

```powershell
$env:SOCIALDATAX_API_KEY='<从 SocialDataX 获取的密钥>'
conda run -n learning-evidence-rag python -B ai-python/run.py
```

## 错误契约

| 场景 | 中文错误或终态 |
| --- | --- |
| 未确认内容处理权 | `请先确认你有权处理该视频内容` |
| 未提取到 URL | `未识别到 HTTP(S) 视频链接` |
| HTTP、非默认端口、非白名单域名 | `当前仅支持 Bilibili 或抖音 HTTPS 公公开视频链接` |
| Bilibili 短链接 | `请粘贴展开后的 Bilibili 完整视频链接` |
| 抖音非视频作品 | `当前抖音接入仅支持视频作品页或 v.douyin.com 视频短链接` |
| 未配置抖音密钥 | `未配置 SOCIALDATAX_API_KEY，暂时无法接入抖音视频` |
| 抖音 MCP 鉴权失败 | `抖音 MCP 鉴权失败，请检查 SOCIALDATAX_API_KEY` |
| 抖音限流/网络/5xx | 受控提示后进入耐久重试 |
| 抖音无语音或资源失效 | 资料进入 `FAILED`，不创建空索引 |
| Bilibili 下载、时长、DRM 或组件失败 | 沿用既有受控错误和重试分类 |

## 前端行为

- 工作台和学习资料页复用同一批量链接入口。
- 本地预检规则与后端一致，显示可接入、重复和不支持数量。
- 平台标识同时展示 Bilibili 和“抖音语音 RAG”。
- 页面明确说明抖音当前不包含画面 OCR，避免把文本 RAG 误认为完整多模态解析。
- 提交后复用现有资料进度轮询和 `MATERIAL_UPLOADED_EVENT`，不增加第二套状态机。

## Java/Python 边界

公开控制面仍由 Python FastAPI 承载。若恢复 Java 业务壳，Java 只透传请求、用户身份与统一 `Result`，不调用 MCP、不下载视频，也不实现转写、切块或检索逻辑。MCP、RAG 和 evidence 处理继续由 Python worker 负责。
