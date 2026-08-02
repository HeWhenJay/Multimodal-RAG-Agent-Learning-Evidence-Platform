import {
  CircleCheck,
  CircleOff,
  Clock3,
  Copy,
  Link2,
  ListChecks,
  Loader2,
  RotateCcw,
  TriangleAlert
} from 'lucide-react';
import { useId, useMemo, useState, type FormEvent } from 'react';
import {
  importRemoteVideos,
  type RemoteVideoBatchItemStatus,
  type RemoteVideoBatchResponse
} from '../api/rag';
import type { LearningMaterial } from '../api/types';
import { MATERIAL_UPLOADED_EVENT } from '../hooks/useMaterialUpload';
import '../styles/RemoteVideoImport.css';

interface RemoteVideoImportProps {
  highPrecision?: boolean;
  disabled?: boolean;
}

type FeedbackKind = 'success' | 'warning' | 'error' | '';
type RemoteVideoUrlPreviewStatus = 'READY' | 'DUPLICATE' | 'UNSUPPORTED';

interface RemoteVideoUrlPreviewItem {
  lineNumber: number;
  url: string;
  canonicalUrl: string | null;
  status: RemoteVideoUrlPreviewStatus;
}

interface RemoteVideoUrlPreview {
  candidateCount: number;
  readyCount: number;
  duplicateCount: number;
  unsupportedCount: number;
  items: RemoteVideoUrlPreviewItem[];
}

interface NormalizedRemoteVideoUrl {
  canonicalUrl: string | null;
  message: string;
}

const PREVIEW_ITEM_LIMIT = 8;
const RESULT_ITEM_LIMIT = 200;
const BILIBILI_HOSTS = new Set(['bilibili.com', 'www.bilibili.com', 'm.bilibili.com']);
const DOUYIN_HOSTS = new Set(['douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com']);
const BILIBILI_VIDEO_ID = /^(?:BV[0-9A-Za-z]{8,20}|av[0-9]+)$/i;
const HTTP_URL_PATTERN = /https?:\/\/[A-Za-z0-9._~:/?#[\]@!$&()*+,;=%-]+/gi;
const TRAILING_PUNCTUATION = new Set('.,;:!?，。！？、；：)]}）】》」』}>'.split(''));

// 公共视频链接入口支持批量粘贴，耗时解析由服务端队列在后台并发处理。
export function RemoteVideoImport({ highPrecision = false, disabled = false }: RemoteVideoImportProps) {
  const inputId = useId();
  const helpId = useId();
  const feedbackId = useId();
  const resultTitleId = useId();
  const [text, setText] = useState('');
  const [confirmedAuthorized, setConfirmedAuthorized] = useState(false);
  const [importing, setImporting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [feedbackKind, setFeedbackKind] = useState<FeedbackKind>('');
  const [batchResult, setBatchResult] = useState<RemoteVideoBatchResponse | null>(null);
  const preview = useMemo(() => previewRemoteVideoUrls(text), [text]);
  const unavailable = disabled || importing;

  // 批量请求只负责写入队列；每条返回的资料继续广播既有事件以刷新页面状态。
  async function submitRemoteVideos(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const sourceText = text.trim();
    if (!sourceText) {
      showFeedback('error', '请粘贴一个或多个 Bilibili 完整视频链接');
      return;
    }
    if (preview.candidateCount === 0) {
      showFeedback('error', '未识别到 HTTP(S) 视频链接，请检查粘贴内容');
      return;
    }
    if (!confirmedAuthorized) {
      showFeedback('error', '请先确认你有权处理这些视频内容');
      return;
    }

    setImporting(true);
    setFeedback('');
    setFeedbackKind('');
    try {
      const result = await importRemoteVideos({
        text: sourceText,
        highPrecision,
        confirmedAuthorized: true
      });
      setBatchResult(result);
      setText('');
      setConfirmedAuthorized(false);
      const acceptedMaterial = result.items.find((item) => item.material)?.material;
      if (acceptedMaterial) publishMaterialImported(acceptedMaterial);

      const acceptedCount = result.queuedCount + result.reusedCount;
      const kind: FeedbackKind = result.rejectedCount > 0 || acceptedCount === 0 ? 'warning' : 'success';
      showFeedback(
        kind,
        `批次已处理：排队 ${result.queuedCount} 条、复用 ${result.reusedCount} 条、重复 ${result.duplicateCount} 条、未接入 ${result.rejectedCount} 条`
      );
    } catch (error) {
      setBatchResult(null);
      showFeedback('error', error instanceof Error ? error.message : '公开视频链接批量接入失败');
    } finally {
      setImporting(false);
    }
  }

  function showFeedback(kind: FeedbackKind, message: string) {
    setFeedbackKind(kind);
    setFeedback(message);
  }

  function clearPreviousResult() {
    if (feedback) {
      setFeedback('');
      setFeedbackKind('');
    }
    if (batchResult) setBatchResult(null);
  }

  const describedBy = [
    helpId,
    feedback ? feedbackId : ''
  ].filter(Boolean).join(' ');

  return (
    <section className="remote-video-import" aria-labelledby={`${inputId}-title`}>
      <div className="remote-video-import-head">
        <div className="remote-video-import-title">
          <Link2 size={18} aria-hidden="true" />
          <strong id={`${inputId}-title`}>批量公开视频链接</strong>
        </div>
        <div className="remote-video-platforms" aria-label="链接平台支持情况">
          <span className="is-supported"><CircleCheck size={14} aria-hidden="true" />Bilibili</span>
          <span className="is-unavailable"><CircleOff size={14} aria-hidden="true" />抖音暂不支持</span>
        </div>
      </div>

      <form className="remote-video-form" onSubmit={submitRemoteVideos} noValidate>
        <label className="remote-video-label" htmlFor={inputId}>链接或平台分享文案</label>
        <div className="remote-video-input-row">
          <div className="remote-video-input-shell">
            <Link2 size={17} aria-hidden="true" />
            <textarea
              id={inputId}
              rows={5}
              inputMode="url"
              autoComplete="off"
              maxLength={1_000_000}
              spellCheck={false}
              placeholder={'一行一个 URL，也可直接粘贴平台分享文案\n【视频标题】https://www.bilibili.com/video/BV...?p=2&vd_source=...'}
              value={text}
              disabled={unavailable}
              aria-describedby={describedBy}
              aria-invalid={feedbackKind === 'error' && preview.candidateCount === 0}
              onChange={(event) => {
                setText(event.target.value);
                clearPreviousResult();
              }}
            />
          </div>
          <button className="remote-video-submit" type="submit" disabled={unavailable || !text.trim()}>
            {importing ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <ListChecks size={17} aria-hidden="true" />}
            {importing ? '正在加入队列' : '批量加入队列'}
          </button>
        </div>
        <p className="remote-video-help" id={helpId}>
          自动提取文案中的 HTTP(S) 链接并移除追踪参数；不限制链接条数，超出处理槽位的任务会排队等待。
        </p>

        {text.trim() ? (
          <div className="remote-video-preview">
            <div className="remote-video-preview-head">
              <strong>本地预检</strong>
              <span>最终结果以后端校验为准</span>
            </div>
            <div className="remote-video-preview-stats" aria-label="链接预检统计" role="status" aria-live="polite" aria-atomic="true">
              <span>识别 <strong>{preview.candidateCount}</strong></span>
              <span className="is-ready">可接入 <strong>{preview.readyCount}</strong></span>
              <span className="is-duplicate">重复 <strong>{preview.duplicateCount}</strong></span>
              <span className="is-unsupported">不支持 <strong>{preview.unsupportedCount}</strong></span>
            </div>
            {preview.candidateCount > 0 ? (
              <ol className="remote-video-preview-list">
                {preview.items.slice(0, PREVIEW_ITEM_LIMIT).map((item, index) => (
                  <li className={`is-${item.status.toLowerCase()}`} key={`${item.lineNumber}-${item.url}-${index}`}>
                    <span className="remote-video-preview-line">第 {item.lineNumber} 行</span>
                    <span className="remote-video-preview-url" title={item.canonicalUrl || item.url}>
                      {item.canonicalUrl || item.url}
                    </span>
                    <span className="remote-video-preview-state">{previewStatusLabel(item.status)}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="remote-video-preview-empty">尚未识别到 URL，可直接粘贴含中文标题的完整分享文案。</p>
            )}
            {preview.items.length > PREVIEW_ITEM_LIMIT ? (
              <p className="remote-video-preview-more">
                预览仅展示前 {PREVIEW_ITEM_LIMIT} 条，其余 {preview.items.length - PREVIEW_ITEM_LIMIT} 条仍会正常提交。
              </p>
            ) : null}
          </div>
        ) : null}

        <label className="remote-video-authorization">
          <input
            type="checkbox"
            checked={confirmedAuthorized}
            disabled={unavailable}
            onChange={(event) => {
              setConfirmedAuthorized(event.target.checked);
              if (feedback) {
                setFeedback('');
                setFeedbackKind('');
              }
            }}
          />
          <span>我确认有权为学习目的处理以上公开视频</span>
        </label>
      </form>

      {feedback ? (
        <p
          className={`remote-video-feedback is-${feedbackKind}`}
          id={feedbackId}
          role={feedbackKind === 'error' ? 'alert' : 'status'}
        >
          <FeedbackIcon kind={feedbackKind} />
          <span>{feedback}</span>
        </p>
      ) : null}

      {batchResult ? (
        <section className="remote-video-results" aria-labelledby={resultTitleId}>
          <div className="remote-video-results-head">
            <strong id={resultTitleId}>逐条处理结果</strong>
            <span>共识别 {batchResult.candidateCount} 条</span>
          </div>
          <div className="remote-video-result-stats" aria-label="批量处理结果统计">
            <span className="is-queued">排队 {batchResult.queuedCount}</span>
            <span className="is-reused">复用 {batchResult.reusedCount}</span>
            <span className="is-duplicate">重复 {batchResult.duplicateCount}</span>
            <span className="is-rejected">未接入 {batchResult.rejectedCount}</span>
          </div>
          <ol className="remote-video-result-list" tabIndex={0} aria-label="每条链接的处理结果">
            {batchResult.items.slice(0, RESULT_ITEM_LIMIT).map((item, index) => (
              <li className={`remote-video-result-item is-${item.status.toLowerCase()}`} key={`${item.lineNumber}-${item.url}-${index}`}>
                <div className="remote-video-result-main">
                  <BatchStatusIcon status={item.status} />
                  <div>
                    <div className="remote-video-result-meta">
                      <span>第 {item.lineNumber} 行</span>
                      <span className="remote-video-status-code">{item.status}</span>
                    </div>
                    <strong>{item.material?.title || item.message}</strong>
                    {item.material ? <p>{item.message}</p> : null}
                    <code title={item.canonicalUrl || item.url}>{item.canonicalUrl || item.url}</code>
                  </div>
                </div>
              </li>
            ))}
          </ol>
          {batchResult.items.length > RESULT_ITEM_LIMIT ? (
            <p className="remote-video-result-more">
              已提交全部 {batchResult.items.length} 条；为保持页面流畅，逐条结果仅展示前 {RESULT_ITEM_LIMIT} 条。
            </p>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}

function previewStatusLabel(status: RemoteVideoUrlPreviewStatus) {
  if (status === 'READY') return '可接入';
  if (status === 'DUPLICATE') return '重复';
  return '不支持';
}

// 从多行文本或平台分享文案中提取链接，并按规范化地址识别批次内重复项。
function previewRemoteVideoUrls(text: string): RemoteVideoUrlPreview {
  const items: RemoteVideoUrlPreviewItem[] = [];
  const seenCanonicalUrls = new Set<string>();
  let currentLineNumber = 1;
  let scannedOffset = 0;

  for (const match of text.matchAll(HTTP_URL_PATTERN)) {
    const matchOffset = match.index ?? 0;
    for (let index = scannedOffset; index < matchOffset; index += 1) {
      if (text.charCodeAt(index) === 10) currentLineNumber += 1;
    }
    scannedOffset = matchOffset + match[0].length;

    const url = trimUrlCandidate(match[0]);
    if (!url) continue;

    const normalized = normalizeRemoteVideoUrl(url);
    if (!normalized.canonicalUrl) {
      items.push({
        lineNumber: currentLineNumber,
        url,
        canonicalUrl: null,
        status: 'UNSUPPORTED'
      });
      continue;
    }

    if (seenCanonicalUrls.has(normalized.canonicalUrl)) {
      items.push({
        lineNumber: currentLineNumber,
        url,
        canonicalUrl: normalized.canonicalUrl,
        status: 'DUPLICATE'
      });
      continue;
    }

    seenCanonicalUrls.add(normalized.canonicalUrl);
    items.push({
      lineNumber: currentLineNumber,
      url,
      canonicalUrl: normalized.canonicalUrl,
      status: 'READY'
    });
  }

  return {
    candidateCount: items.length,
    readyCount: items.filter((item) => item.status === 'READY').length,
    duplicateCount: items.filter((item) => item.status === 'DUPLICATE').length,
    unsupportedCount: items.filter((item) => item.status === 'UNSUPPORTED').length,
    items
  };
}

// 与服务端规则保持一致：固定 Bilibili 主站地址，仅保留合法的分 P 参数。
function normalizeRemoteVideoUrl(value: string): NormalizedRemoteVideoUrl {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return unsupportedRemoteVideoUrl('链接格式不正确');
  }

  const host = parsed.hostname.toLowerCase().replace(/\.$/, '');
  if (DOUYIN_HOSTS.has(host) || host.endsWith('.douyin.com')) {
    return unsupportedRemoteVideoUrl('抖音链接暂不支持');
  }
  if (host === 'b23.tv' || host.endsWith('.b23.tv')) {
    return unsupportedRemoteVideoUrl('请先展开 b23.tv 短链接');
  }
  if (
    parsed.protocol !== 'https:'
    || !BILIBILI_HOSTS.has(host)
    || parsed.username
    || parsed.password
    || (parsed.port && parsed.port !== '443')
  ) {
    return unsupportedRemoteVideoUrl('当前仅支持 Bilibili 完整公开视频链接');
  }

  const pathParts = parsed.pathname.split('/').filter(Boolean);
  if (pathParts.length !== 2 || pathParts[0].toLowerCase() !== 'video' || !BILIBILI_VIDEO_ID.test(pathParts[1])) {
    return unsupportedRemoteVideoUrl('当前仅支持 bilibili.com/video/BV... 或 av... 完整链接');
  }

  const pageResult = normalizePageNumber(parsed.searchParams.getAll('p'));
  if (pageResult.message) {
    return unsupportedRemoteVideoUrl(pageResult.message);
  }

  const videoId = pathParts[1].slice(0, 2).toLowerCase() === 'bv'
    ? `BV${pathParts[1].slice(2)}`
    : `av${pathParts[1].slice(2)}`;
  const canonicalUrl = new URL(`https://www.bilibili.com/video/${videoId}`);
  if (pageResult.page && pageResult.page > 1) {
    canonicalUrl.searchParams.set('p', String(pageResult.page));
  }
  return { canonicalUrl: canonicalUrl.toString(), message: '' };
}

// 空的 p 参数等同于未提供；第一个非空 p 必须是 1 到 999 的整数。
function normalizePageNumber(values: string[]): { page: number | null; message: string } {
  const firstValue = values.find((value) => value !== '');
  if (firstValue === undefined) {
    return { page: null, message: '' };
  }
  const normalizedValue = firstValue.trim();
  if (!/^\+?\d+$/.test(normalizedValue)) {
    return { page: null, message: 'Bilibili 分 P 参数必须是正整数' };
  }
  const page = Number(normalizedValue);
  if (!Number.isSafeInteger(page) || page < 1 || page > 999) {
    return { page: null, message: 'Bilibili 分 P 参数必须是正整数' };
  }
  return { page, message: '' };
}

// 清理分享文案、中文标点或 Markdown 包裹产生的 URL 尾部符号。
function trimUrlCandidate(value: string) {
  let candidate = value.trim();
  while (candidate && TRAILING_PUNCTUATION.has(candidate.charAt(candidate.length - 1))) {
    candidate = candidate.slice(0, -1);
  }
  return candidate;
}

function unsupportedRemoteVideoUrl(message: string): NormalizedRemoteVideoUrl {
  return { canonicalUrl: null, message };
}

function FeedbackIcon({ kind }: { kind: FeedbackKind }) {
  if (kind === 'success') return <CircleCheck size={15} aria-hidden="true" />;
  if (kind === 'warning') return <TriangleAlert size={15} aria-hidden="true" />;
  return <CircleOff size={15} aria-hidden="true" />;
}

function BatchStatusIcon({ status }: { status: RemoteVideoBatchItemStatus }) {
  if (status === 'QUEUED') return <Clock3 size={17} aria-hidden="true" />;
  if (status === 'REUSED') return <RotateCcw size={17} aria-hidden="true" />;
  if (status === 'DUPLICATE') return <Copy size={17} aria-hidden="true" />;
  return <CircleOff size={17} aria-hidden="true" />;
}

// 一个批次只广播一次，避免大量链接触发重复列表查询和复习同步。
function publishMaterialImported(material: LearningMaterial) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent<LearningMaterial>(MATERIAL_UPLOADED_EVENT, { detail: material }));
  }
}
