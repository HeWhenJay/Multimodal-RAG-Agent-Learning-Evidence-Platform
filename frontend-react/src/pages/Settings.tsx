import { ServerCog, SlidersHorizontal } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { fetchSystemSettings } from '../api/pageData';
import type { SystemSetting } from '../api/types';

// 系统设置页展示本地服务边界和当前检索参数。
export function Settings() {
  const [settings, setSettings] = useState<SystemSetting[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchSystemSettings()
      .then(setSettings)
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : '系统设置数据加载失败'));
  }, []);

  const grouped = useMemo(() => ({
    service: settings.filter((item) => item.group === '服务边界'),
    retrieval: settings.filter((item) => item.group === '检索参数')
  }), [settings]);

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
          {grouped.service.map((item) => (
            <div className="setting-row" key={item.key}><span>{item.label}</span><strong>{item.value}</strong></div>
          ))}
          {grouped.service.length === 0 ? <div className="empty-state">暂无服务边界配置</div> : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3><SlidersHorizontal size={20} />检索参数</h3>
          </div>
          {grouped.retrieval.map((item) => (
            <div className="setting-row" key={item.key}><span>{item.label}</span><strong>{item.value}</strong></div>
          ))}
          {grouped.retrieval.length === 0 ? <div className="empty-state">暂无检索参数配置</div> : null}
        </article>
      </section>
      {error ? <p className="form-message danger">{error}</p> : null}
    </div>
  );
}
