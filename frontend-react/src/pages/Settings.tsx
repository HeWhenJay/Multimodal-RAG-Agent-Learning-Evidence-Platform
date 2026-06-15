import { ServerCog, SlidersHorizontal } from 'lucide-react';

export function Settings() {
  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>系统设置</h2>
          <p>RAG 服务地址、解析策略与检索参数</p>
        </div>
      </section>

      <section className="two-column">
        <article className="panel">
          <div className="panel-title">
            <h3><ServerCog size={20} />服务边界</h3>
          </div>
          <div className="setting-row"><span>Frontend</span><strong>http://127.0.0.1:5178</strong></div>
          <div className="setting-row"><span>Java API</span><strong>http://127.0.0.1:8080</strong></div>
          <div className="setting-row"><span>Python RAG</span><strong>http://127.0.0.1:8090</strong></div>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3><SlidersHorizontal size={20} />检索参数</h3>
          </div>
          <div className="setting-row"><span>Chunk Size</span><strong>700</strong></div>
          <div className="setting-row"><span>Overlap</span><strong>90</strong></div>
          <div className="setting-row"><span>Fusion</span><strong>RRF k=60</strong></div>
          <div className="setting-row"><span>Parser</span><strong>MinerU + fallback</strong></div>
        </article>
      </section>
    </div>
  );
}

