import { CheckCircle2, Clock, PlayCircle, Video } from 'lucide-react';

const slices = [
  { title: 'Java 并发编程核心原理解析', topic: 'JVM Memory Model', time: '01:23:10 - 01:25:42', status: 'ASR/OCR 已入库' },
  { title: '分布式系统架构设计 (B站录播)', topic: 'CAP 定理与实践', time: '00:45:22 - 00:48:15', status: '关键帧已就绪' },
  { title: '云原生架构实战', topic: 'Kubernetes 调度', time: '00:18:02 - 00:22:36', status: '已关联 RAG' }
];

export function VideoReview() {
  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>视频复习</h2>
          <p>ASR、OCR、关键帧与时间戳证据</p>
        </div>
      </section>

      <section className="video-grid">
        {slices.map((item) => (
          <article className="panel video-card" key={item.title}>
            <div className="video-thumb">
              <PlayCircle size={42} />
              <span>{item.time.split(' - ')[0]}</span>
            </div>
            <div className="video-body">
              <h3>{item.title}</h3>
              <p>知识片段：{item.topic}</p>
              <div className="video-meta">
                <span><Clock size={15} />{item.time}</span>
                <span><CheckCircle2 size={15} />{item.status}</span>
              </div>
              <button className="ghost-action">
                <Video size={16} />
                从这里播放
              </button>
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
