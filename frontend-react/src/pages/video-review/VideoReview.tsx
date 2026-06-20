import { CheckCircle2, Clock, PlayCircle, Video } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchVideoSlices } from '../../api/pageData';
import type { VideoSlice } from '../../api/types';

// 视频复习页展示视频切片、时间戳和证据入库状态。
export function VideoReview() {
  const [slices, setSlices] = useState<VideoSlice[]>([]);
  const [error, setError] = useState('');
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [searchParams] = useSearchParams();
  const targetTitle = searchParams.get('title');
  const targetDocumentId = searchParams.get('documentId');
  const targetStartTime = searchParams.get('startTime');
  const targetEndTime = searchParams.get('endTime');
  const targetSourcePath = searchParams.get('sourcePath');
  const targetPlaybackUrl = searchParams.get('playbackUrl');
  const targetVideoUrl = resolveVideoUrl({
    videoUrl: searchParams.get('videoUrl'),
    playbackUrl: targetPlaybackUrl,
    sourcePath: targetSourcePath,
    source: searchParams.get('source')
  });
  const targetSeconds = targetStartTime ? timestampToSeconds(targetStartTime) : 0;

  useEffect(() => {
    fetchVideoSlices()
      .then(setSlices)
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : '视频切片数据加载失败'));
  }, []);

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>{targetStartTime ? '视频证据播放' : '视频复习'}</h2>
          <p>{targetStartTime ? 'RAG evidence 时间戳定位与播放' : 'ASR、OCR、关键帧与时间戳证据'}</p>
        </div>
      </section>

      {targetStartTime && (
        <section className="panel video-jump-panel">
          <div>
            <h3>{targetTitle || 'RAG 命中视频证据'}</h3>
            <p>
              已定位到 {targetStartTime}{targetEndTime ? ` - ${targetEndTime}` : ''}，
              {targetDocumentId ? `资料 ${targetDocumentId}` : '来自字幕或转写文本 evidence'}。
            </p>
            {targetSourcePath ? <span>来源：{targetSourcePath}</span> : null}
          </div>
          <button className="ghost-action" onClick={() => seekVideo(videoRef.current, targetSeconds)} disabled={!targetVideoUrl}>
            <PlayCircle size={16} />
            播放定位
          </button>
        </section>
      )}

      {targetStartTime && !targetVideoUrl && (
        <section className="panel video-fallback-panel">
          <strong>无法直接播放视频源</strong>
          <p>当前 evidence 已定位到时间段，但来源不是浏览器可直接访问的视频 URL，请配置 ALIYUN_OSS_PUBLIC_BASE_URL 或补充签名 URL 服务。</p>
        </section>
      )}

      {targetVideoUrl && (
        <section className="panel video-player-panel">
          <video
            ref={videoRef}
            className="video-player"
            src={targetVideoUrl}
            controls
            preload="metadata"
            onLoadedMetadata={(event) => seekVideo(event.currentTarget, targetSeconds)}
          />
        </section>
      )}

      <section className="video-grid">
        {slices.map((item) => (
          <article className="panel video-card" key={item.id}>
            <div className="video-thumb">
              <PlayCircle size={42} />
              <span>{item.startTime}</span>
            </div>
            <div className="video-body">
              <h3>{item.title}</h3>
              <p>知识片段：{item.topic}</p>
              <div className="video-meta">
                <span><Clock size={15} />{item.startTime} - {item.endTime}</span>
                <span><CheckCircle2 size={15} />{item.status}</span>
              </div>
              <button className="ghost-action">
                <Video size={16} />
                从这里播放
              </button>
            </div>
          </article>
        ))}
        {slices.length === 0 ? <div className="empty-state">暂无视频切片记录</div> : null}
      </section>
      {error ? <p className="form-message danger">{error}</p> : null}
    </div>
  );
}

interface VideoUrlCandidates {
  videoUrl: string | null;
  playbackUrl: string | null;
  sourcePath: string | null;
  source: string | null;
}

// 识别可直接播放的公开视频地址，显式播放地址允许签名或转发接口。
function resolveVideoUrl({ videoUrl, playbackUrl, sourcePath, source }: VideoUrlCandidates) {
  const explicitUrl = firstHttpUrl(videoUrl, playbackUrl);
  if (explicitUrl) return stripFragment(explicitUrl);
  return firstConservativeVideoUrl(sourcePath, source);
}

function firstHttpUrl(...values: Array<string | null>) {
  return values.find((value) => value && isHttpUrl(value)) || null;
}

function firstConservativeVideoUrl(...values: Array<string | null>) {
  const candidate = values.find((value) => value && isConservativeVideoUrl(value));
  return candidate ? stripFragment(candidate) : null;
}

function stripFragment(value: string) {
  return value.split('#', 1)[0];
}

function isHttpUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function isConservativeVideoUrl(value: string) {
  if (!isHttpUrl(value)) return false;
  const path = stripFragment(value).split('?', 1)[0];
  return /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(path);
}

// 将 HH:MM:SS、MM:SS 和带毫秒的时间转为秒。
function timestampToSeconds(value: string) {
  const normalized = value.trim().replace(',', '.');
  if (!normalized) return 0;
  const parts = normalized.split(':');
  if (parts.length === 1) {
    const seconds = Number.parseFloat(parts[0]);
    return Number.isFinite(seconds) ? seconds : 0;
  }
  if (parts.length === 2) {
    const minutes = Number.parseInt(parts[0], 10) || 0;
    const seconds = Number.parseFloat(parts[1]) || 0;
    return minutes * 60 + seconds;
  }
  if (parts.length >= 3) {
    const [hoursText, minutesText, secondsText] = parts.slice(-3);
    const hours = Number.parseInt(hoursText, 10) || 0;
    const minutes = Number.parseInt(minutesText, 10) || 0;
    const seconds = Number.parseFloat(secondsText) || 0;
    return hours * 3600 + minutes * 60 + seconds;
  }
  return 0;
}

// 将播放器定位到 evidence 命中的秒点。
function seekVideo(video: HTMLVideoElement | null, seconds: number) {
  if (!video || Number.isNaN(seconds)) return;
  video.currentTime = Math.max(0, seconds);
  video.play().catch(() => undefined);
}
