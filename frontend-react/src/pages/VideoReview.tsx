import { CheckCircle2, Clock, PlayCircle, Video } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchVideoSlices } from '../api/pageData';
import type { VideoSlice } from '../api/types';

// 视频复习页展示视频切片、时间戳和证据入库状态。
export function VideoReview() {
  const [slices, setSlices] = useState<VideoSlice[]>([]);
  const [error, setError] = useState('');
  const [searchParams] = useSearchParams();
  const targetTitle = searchParams.get('title');
  const targetDocumentId = searchParams.get('documentId');
  const targetStartTime = searchParams.get('startTime');
  const targetEndTime = searchParams.get('endTime');
  const targetSourcePath = searchParams.get('sourcePath');

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
          <button className="ghost-action">
            <PlayCircle size={16} />
            播放定位
          </button>
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
