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
  const targetVideoUrl = resolveVideoUrl(searchParams.get('videoUrl'), targetSourcePath);
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
          <h2>视频复习</h2>
          <p>ASR、OCR、关键帧与时间戳证据</p>
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

// 识别可直接播放的公开视频地址。
function resolveVideoUrl(videoUrl: string | null, sourcePath: string | null) {
  const candidate = videoUrl || sourcePath;
  if (!candidate || !/^https?:\/\//i.test(candidate)) return null;
  const lower = candidate.split('#', 1)[0].split('?', 1)[0].toLowerCase();
  return /\.(mp4|mov|m4v|webm|mkv|avi)$/.test(lower) ? candidate : null;
}

// 将 HH:MM:SS 或 MM:SS 时间转为秒。
function timestampToSeconds(value: string) {
  const parts = value.replace(',', '.').split('.', 1)[0].split(':').map((part) => Number.parseInt(part, 10) || 0);
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length >= 3) {
    const [hours, minutes, seconds] = parts.slice(-3);
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
