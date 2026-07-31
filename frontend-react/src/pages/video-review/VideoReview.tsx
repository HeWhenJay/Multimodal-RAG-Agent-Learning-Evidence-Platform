import { ArrowLeft, Clock, FileVideo2, PlayCircle, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { browserHttpSource, describeBrowserSource } from '../../utils/sourceSafety';

interface VideoUrlCandidates {
  videoUrl: string | null;
  playbackUrl: string | null;
  sourcePath: string | null;
  source: string | null;
}

// 视频证据页负责承接 RAG evidence 的时间戳定位和不可播放来源解释。
export function VideoReview() {
  const [searchParams, setSearchParams] = useSearchParams();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [pausedAtEnd, setPausedAtEnd] = useState(false);
  const title = searchParams.get('title') || '视频证据';
  const documentId = searchParams.get('documentId') || '';
  const startTime = searchParams.get('startTime') || '00:00:00';
  const endTime = searchParams.get('endTime') || '';
  const sourcePath = searchParams.get('sourcePath') || '';
  const source = searchParams.get('source') || '';
  const playbackUrl = searchParams.get('playbackUrl') || '';
  const targetSeconds = timestampToSeconds(startTime);
  const endSeconds = endTime ? timestampToSeconds(endTime) : Number.NaN;
  const videoUrl = useMemo(
    () => resolveVideoUrl({
      videoUrl: searchParams.get('videoUrl'),
      playbackUrl: searchParams.get('playbackUrl'),
      sourcePath: searchParams.get('sourcePath'),
      source: searchParams.get('source')
    }),
    [searchParams]
  );
  const sourceLabel = useMemo(
    () => describeBrowserSource(videoUrl, sourcePath, source, playbackUrl),
    [playbackUrl, source, sourcePath, videoUrl]
  );

  // 历史 evidence 可能带有 worker 临时路径，进入页面后立即从地址栏移除。
  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let changed = false;
    ['sourcePath', 'source', 'playbackUrl', 'videoUrl'].forEach((key) => {
      const value = next.get(key);
      if (value && !browserHttpSource(value)) {
        next.delete(key);
        changed = true;
      }
    });
    if (changed) setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    setPausedAtEnd(false);
  }, [videoUrl, startTime, endTime]);

  // 播放器加载完成后自动定位到 evidence 命中的起始秒点。
  function seekToEvidence(video: HTMLVideoElement | null) {
    if (!video || !Number.isFinite(targetSeconds)) return;
    video.currentTime = Math.max(0, targetSeconds);
    video.play().catch(() => undefined);
  }

  // 到达 evidence 结束时间后暂停，避免用户误以为引用覆盖整段视频。
  function handleTimeUpdate(video: HTMLVideoElement) {
    if (!Number.isFinite(endSeconds) || pausedAtEnd || video.currentTime < endSeconds) return;
    video.pause();
    setPausedAtEnd(true);
  }

  return (
    <div className="video-review-shell">
      <section className="preview-topbar video-review-topbar">
        <div>
          <span><FileVideo2 size={16} />RAG 视频证据</span>
          <h1>{title}</h1>
          <p>
            {documentId ? `资料 ${documentId} · ` : ''}
            定位 {startTime}{endTime ? ` - ${endTime}` : ''}
          </p>
        </div>
        <button className="preview-source-label" type="button" onClick={() => window.close()}>
          <ArrowLeft size={15} />
          关闭页面
        </button>
      </section>

      <section className="panel video-jump-panel">
        <div>
          <strong><Clock size={16} />当前定位</strong>
          <p>点击证据链接后已进入独立页面，播放器会优先跳到 {startTime}。</p>
          {sourceLabel ? <small>{sourceLabel}</small> : null}
        </div>
        <button className="ghost-action" onClick={() => seekToEvidence(videoRef.current)} disabled={!videoUrl} type="button">
          <PlayCircle size={16} />
          播放定位
        </button>
      </section>

      {videoUrl ? (
        <section className="panel video-player-panel">
          <video
            ref={videoRef}
            className="video-player"
            src={videoUrl}
            controls
            preload="metadata"
            onLoadedMetadata={(event) => seekToEvidence(event.currentTarget)}
            onTimeUpdate={(event) => handleTimeUpdate(event.currentTarget)}
          />
          {pausedAtEnd ? (
            <div className="video-end-hint">
              <span>已到达 evidence 结束时间 {endTime}</span>
              <button type="button" onClick={() => {
                setPausedAtEnd(false);
                videoRef.current?.play().catch(() => undefined);
              }}>
                <RotateCcw size={15} />
                继续播放
              </button>
            </div>
          ) : null}
        </section>
      ) : (
        <section className="panel video-fallback-panel">
          <strong>无法直接播放视频源</strong>
          <p>当前 evidence 已保留时间定位，但索引中没有浏览器可访问的视频地址。</p>
          <p>请确认 OSS 公网地址配置后重建该资料索引；历史内部路径已自动隐藏。</p>
          <span className="video-source-status">{sourceLabel || '未返回公开播放地址'}</span>
        </section>
      )}
    </div>
  );
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
  return Boolean(browserHttpSource(value));
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
  const [hoursText, minutesText, secondsText] = parts.slice(-3);
  const hours = Number.parseInt(hoursText, 10) || 0;
  const minutes = Number.parseInt(minutesText, 10) || 0;
  const seconds = Number.parseFloat(secondsText) || 0;
  return hours * 3600 + minutes * 60 + seconds;
}
