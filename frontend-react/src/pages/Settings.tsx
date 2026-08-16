import { Database, Loader2, RefreshCw, ServerCog, SlidersHorizontal } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchDshLocalSyncStatus,
  syncDshLocalKnowledge,
  type DshLocalSyncResult,
  type DshLocalSyncStatus
} from '../api/dshLocalSync';
import { fetchSystemSettings } from '../api/pageData';
import type { SystemSetting } from '../api/types';

// 系统设置页展示本地服务边界、检索参数和项目侧 DSH 个人同步适配器。
export function Settings() {
  const [settings, setSettings] = useState<SystemSetting[]>([]);
  const [error, setError] = useState('');
  const [syncStatus, setSyncStatus] = useState<DshLocalSyncStatus | null>(null);
  const [syncResult, setSyncResult] = useState<DshLocalSyncResult | null>(null);
  const [syncError, setSyncError] = useState('');
  const [syncing, setSyncing] = useState(false);

  // 刷新状态不会读取资料正文，也不会从浏览器提交本地文件路径。
  const loadSyncStatus = useCallback(async () => {
    try {
      setSyncStatus(await fetchDshLocalSyncStatus());
      setSyncError('');
    } catch (loadError) {
      setSyncError(loadError instanceof Error ? loadError.message : 'DSH 本地知识库状态加载失败');
    }
  }, []);

  useEffect(() => {
    fetchSystemSettings()
      .then(setSettings)
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : '系统设置数据加载失败'));
    void loadSyncStatus();
  }, [loadSyncStatus]);

  const grouped = useMemo(() => ({
    service: settings.filter((item) => item.group === '服务边界'),
    retrieval: settings.filter((item) => item.group === '检索参数')
  }), [settings]);

  // 单击后由项目服务端按登录用户和固定 store 全量幂等同步。
  const handleSync = async () => {
    setSyncing(true);
    setSyncError('');
    try {
      const result = await syncDshLocalKnowledge();
      setSyncResult(result);
      await loadSyncStatus();
    } catch (syncFailure) {
      setSyncError(syncFailure instanceof Error ? syncFailure.message : 'DSH 本地知识库同步失败');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>系统设置</h2>
          <p>RAG 服务地址、解析策略与检索参数</p>
        </div>
      </section>

      <section className="dsh-sync-panel panel" aria-labelledby="dsh-sync-title">
        <div className="panel-title dsh-sync-heading">
          <div>
            <h3 id="dsh-sync-title"><Database size={20} />DSH 本地知识库同步</h3>
            <p>由当前项目主动读取本机插件资料；插件与项目仍独立发布。</p>
          </div>
          <button
            className="primary-action"
            type="button"
            onClick={() => void handleSync()}
            disabled={syncing || !syncStatus?.configured || !syncStatus?.readable}
          >
            {syncing ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            {syncing ? '同步中' : '从 DSH 同步'}
          </button>
        </div>

        <div className="dsh-sync-metrics" aria-live="polite">
          <div><span>插件资料</span><strong>{syncStatus?.documentCount ?? '—'}</strong></div>
          <div><span>已同步</span><strong>{syncStatus?.syncedDocumentCount ?? '—'}</strong></div>
          <div><span>待同步</span><strong>{syncStatus?.pendingDocumentCount ?? '—'}</strong></div>
          <div><span>最后同步</span><strong>{formatSyncTime(syncStatus?.lastSyncedAt)}</strong></div>
        </div>

        <div className={`dsh-sync-state ${syncStatus?.readable ? 'is-ready' : 'is-muted'}`}>
          <span aria-hidden="true" />
          {syncStatus?.message || '正在读取 DSH 本地知识库状态'}
        </div>
        {syncResult ? (
          <p className={`form-message ${syncResult.failedCount ? 'warning' : ''}`}>
            本次扫描 {syncResult.scannedCount} 份：新增 {syncResult.createdCount}、更新 {syncResult.updatedCount}、未变化 {syncResult.skippedCount}、失败 {syncResult.failedCount}。索引完成后由项目复习服务独立生成摘要、分类和卡片。
          </p>
        ) : null}
        {syncError ? <p className="form-message danger">{syncError}</p> : null}
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

// 使用当前浏览器区域格式展示服务端 ISO 时间，不显示无意义占位日期。
function formatSyncTime(value?: string | null): string {
  if (!value) {
    return '尚未同步';
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { hour12: false });
}
