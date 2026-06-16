import { FileUp, Loader2, Plus, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchMaterials, indexText, uploadMaterial } from '../api/rag';
import type { LearningMaterial } from '../api/types';

const sampleText = `## RAG 检索优化
BM25 适合关键词召回，向量检索适合语义召回。RAG-Fusion 使用 Multi-Query 和 RRF 将多路结果合并排序。

## 递归切块
递归切块会优先保留标题、段落和句子结构，并通过重叠窗口保留上下文。`;

export function LearningMaterials() {
  const [materials, setMaterials] = useState<LearningMaterial[]>([]);
  const [title, setTitle] = useState('RAG 检索优化笔记');
  const [content, setContent] = useState(sampleText);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  async function refresh() {
    const data = await fetchMaterials();
    setMaterials(data);
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  async function submitText() {
    setBusy(true);
    setMessage('');
    try {
      await indexText({ title, documentType: 'markdown', source: 'manual', content });
      setMessage('已索引文本资料');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '索引失败');
    } finally {
      setBusy(false);
    }
  }

  async function submitFile(file: File | null) {
    if (!file) return;
    setBusy(true);
    setMessage('');
    try {
      await uploadMaterial(file);
      setMessage('已上传并索引文件');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '上传失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>学习资料</h2>
          <p>Markdown、PDF、Word、PPT 与视频资料入口</p>
        </div>
        <button className="ghost-action" onClick={refresh}>
          <RefreshCw size={17} />
          刷新
        </button>
      </section>

      <section className="two-column">
        <article className="panel">
          <div className="panel-title">
            <h3><Plus size={20} />文本资料索引</h3>
          </div>
          <label className="field-label">标题</label>
          <input className="text-input" value={title} onChange={(event) => setTitle(event.target.value)} />
          <label className="field-label">内容</label>
          <textarea className="material-textarea" value={content} onChange={(event) => setContent(event.target.value)} />
          <button className="full-action" onClick={submitText} disabled={busy}>
            {busy ? <Loader2 className="spin" size={17} /> : <Plus size={17} />}
            建立索引
          </button>
          {message && <p className="form-message">{message}</p>}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3><FileUp size={20} />MinerU 文件识别</h3>
          </div>
          <label className="file-drop">
            <FileUp size={30} />
            <strong>PDF / DOCX / PPTX / MD</strong>
            <span>优先使用 MinerU，未配置时本地降级解析</span>
            <input type="file" onChange={(event) => submitFile(event.target.files?.[0] || null)} />
          </label>
        </article>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>近期资料</h3>
          <span className="status-pill">{materials.length} 条资料</span>
        </div>
        <div className="material-list">
          {materials.map((item) => (
            <div className="material-row" key={item.id}>
              <div>
                <strong>{item.title}</strong>
                <span>{formatDocumentType(item.documentType)} · {formatSource(item.source)} · {item.parser || '等待解析'}</span>
                <p>{item.documentSummary || '等待索引摘要'}</p>
              </div>
              <div className="material-meta">
                <span className={`status-pill ${item.status === 'INDEXED' ? 'indexed' : ''}`}>{formatStatus(item.status)}</span>
                <strong>{item.chunkCount} 个切块</strong>
              </div>
            </div>
          ))}
          {materials.length === 0 && <div className="empty-state">暂无资料记录</div>}
        </div>
      </section>
    </div>
  );
}

function formatDocumentType(type: string) {
  const normalized = type.toLowerCase();
  if (normalized === 'markdown') return 'Markdown';
  if (normalized === 'text') return '文本';
  return type.toUpperCase();
}

function formatSource(source: string) {
  if (source === 'manual') return '手动录入';
  return source;
}

function formatStatus(status: string) {
  if (status === 'INDEXED') return '已索引';
  if (status === 'INDEXING') return '索引中';
  if (status === 'FAILED') return '索引失败';
  return status;
}
