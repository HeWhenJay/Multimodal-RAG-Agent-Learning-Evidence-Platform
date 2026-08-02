import { CircleCheck, CircleOff, Link2, Loader2 } from 'lucide-react';
import { useId, useState, type FormEvent } from 'react';
import { importRemoteVideo } from '../api/rag';
import type { LearningMaterial } from '../api/types';
import { MATERIAL_UPLOADED_EVENT } from '../hooks/useMaterialUpload';
import '../styles/RemoteVideoImport.css';

interface RemoteVideoImportProps {
  highPrecision?: boolean;
  disabled?: boolean;
}

type FeedbackKind = 'success' | 'error' | '';

const BILIBILI_HOSTS = new Set(['bilibili.com', 'www.bilibili.com', 'm.bilibili.com']);
const DOUYIN_HOSTS = new Set(['douyin.com', 'www.douyin.com', 'v.douyin.com', 'iesdouyin.com']);
const BILIBILI_VIDEO_PATH = /^\/video\/(?:BV[0-9A-Za-z]{8,20}|av[0-9]+)\/?$/i;

// 公共视频链接入口仅接收无需登录即可访问的 Bilibili 完整链接。
export function RemoteVideoImport({ highPrecision = false, disabled = false }: RemoteVideoImportProps) {
  const inputId = useId();
  const helpId = useId();
  const feedbackId = useId();
  const [url, setUrl] = useState('');
  const [confirmedAuthorized, setConfirmedAuthorized] = useState(false);
  const [importing, setImporting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [feedbackKind, setFeedbackKind] = useState<FeedbackKind>('');
  const unavailable = disabled || importing;

  // 校验链接和授权后创建耐久任务，成功后复用现有上传事件刷新各处资料状态。
  async function submitRemoteVideo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedUrl = url.trim();
    const validationMessage = validateRemoteVideoUrl(normalizedUrl);
    if (validationMessage) {
      setFeedbackKind('error');
      setFeedback(validationMessage);
      return;
    }
    if (!confirmedAuthorized) {
      setFeedbackKind('error');
      setFeedback('请先确认你有权处理该视频内容');
      return;
    }

    setImporting(true);
    setFeedback('');
    setFeedbackKind('');
    try {
      const material = await importRemoteVideo({
        url: normalizedUrl,
        highPrecision,
        confirmedAuthorized: true
      });
      setUrl('');
      setConfirmedAuthorized(false);
      setFeedbackKind('success');
      setFeedback(`已创建“${material.title}”的解析任务`);
      publishMaterialImported(material);
    } catch (error) {
      setFeedbackKind('error');
      setFeedback(error instanceof Error ? error.message : '公开视频链接接入失败');
    } finally {
      setImporting(false);
    }
  }

  return (
    <section className="remote-video-import" aria-labelledby={`${inputId}-title`}>
      <div className="remote-video-import-head">
        <div className="remote-video-import-title">
          <Link2 size={18} aria-hidden="true" />
          <strong id={`${inputId}-title`}>公开视频链接</strong>
        </div>
        <div className="remote-video-platforms" aria-label="链接平台支持情况">
          <span className="is-supported"><CircleCheck size={14} aria-hidden="true" />Bilibili</span>
          <span className="is-unavailable"><CircleOff size={14} aria-hidden="true" />抖音暂不支持</span>
        </div>
      </div>

      <form className="remote-video-form" onSubmit={submitRemoteVideo} noValidate>
        <label className="remote-video-label" htmlFor={inputId}>Bilibili 完整视频链接</label>
        <div className="remote-video-input-row">
          <div className="remote-video-input-shell">
            <Link2 size={17} aria-hidden="true" />
            <input
              id={inputId}
              type="url"
              inputMode="url"
              autoComplete="url"
              maxLength={2048}
              placeholder="https://www.bilibili.com/video/BV..."
              value={url}
              disabled={unavailable}
              aria-describedby={`${helpId}${feedback ? ` ${feedbackId}` : ''}`}
              aria-invalid={feedbackKind === 'error'}
              onChange={(event) => {
                setUrl(event.target.value);
                if (feedback) {
                  setFeedback('');
                  setFeedbackKind('');
                }
              }}
            />
          </div>
          <button className="remote-video-submit" type="submit" disabled={unavailable}>
            {importing ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <Link2 size={17} aria-hidden="true" />}
            {importing ? '正在接入' : '接入 RAG'}
          </button>
        </div>
        <p className="remote-video-help" id={helpId}>仅支持无需登录即可播放的 bilibili.com/video/BV... 或 av... 完整链接</p>
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
          <span>我确认有权为学习目的处理该公开视频</span>
        </label>
      </form>

      {feedback ? (
        <p
          className={`remote-video-feedback is-${feedbackKind}`}
          id={feedbackId}
          role={feedbackKind === 'error' ? 'alert' : 'status'}
        >
          {feedbackKind === 'success' ? <CircleCheck size={15} aria-hidden="true" /> : <CircleOff size={15} aria-hidden="true" />}
          <span>{feedback}</span>
        </p>
      ) : null}
    </section>
  );
}

// 前端提前提供明确平台提示，后端仍会执行同等或更严格的白名单校验。
function validateRemoteVideoUrl(value: string) {
  if (!value) {
    return '请输入 Bilibili 完整视频链接';
  }

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return '链接格式不正确，请粘贴完整的 Bilibili 视频链接';
  }

  const host = parsed.hostname.toLowerCase().replace(/\.$/, '');
  if (DOUYIN_HOSTS.has(host) || host.endsWith('.douyin.com')) {
    return '抖音链接暂不支持：系统不会读取 Cookie 或绕过平台访问限制';
  }
  if (host === 'b23.tv' || host.endsWith('.b23.tv')) {
    return '请先展开 b23.tv 短链接，再粘贴 Bilibili 完整视频链接';
  }
  if (
    parsed.protocol !== 'https:'
    || !BILIBILI_HOSTS.has(host)
    || parsed.username
    || parsed.password
    || (parsed.port && parsed.port !== '443')
    || !BILIBILI_VIDEO_PATH.test(parsed.pathname)
  ) {
    return '当前仅支持 https://www.bilibili.com/video/BV... 或 av... 完整公开视频链接';
  }
  return '';
}

// 广播链接资料已创建，让工作台、资料页和复习概览复用既有刷新链路。
function publishMaterialImported(material: LearningMaterial) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent<LearningMaterial>(MATERIAL_UPLOADED_EVENT, { detail: material }));
  }
}
