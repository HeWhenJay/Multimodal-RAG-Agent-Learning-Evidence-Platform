import { FileUp, Loader2, Plus, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchMaterials, indexText, uploadMaterial } from '../api/rag';
import type { LearningMaterial } from '../api/types';

// 学习资料页负责文本索引、文件上传和资料状态展示。
export function LearningMaterials() {
  const [materials, setMaterials] = useState<LearningMaterial[]>([]);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [highPrecision, setHighPrecision] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  // 刷新最近学习资料列表。
  async function refresh() {
    const data = await fetchMaterials();
    setMaterials(data);
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  // 提交文本资料并等待索引结果。
  async function submitText() {
    if (!title.trim() || !content.trim()) {
      setMessage('请输入标题和内容');
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      await indexText({ title, documentType: 'markdown', source: 'manual', content });
      setMessage('文本资料已入库');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '索引失败');
    } finally {
      setBusy(false);
    }
  }

  // 提交文件资料并按当前解析精度选项入库。
  async function submitFile(file: File | null) {
    if (!file) return;
    setBusy(true);
    setMessage('');
    try {
      await uploadMaterial(file, highPrecision);
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
          <p>Markdown、PDF、Word、PPT、字幕与转写文本入口</p>
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
            <h3><FileUp size={20} />多格式文件解析</h3>
          </div>
          <label className="file-drop">
            <FileUp size={30} />
            <strong>PDF / DOC / DOCX / PPT / PPTX / MD / XLSX / TXT / SRT / VTT / 图片</strong>
            <span>原生结构解析优先，复杂版式再补跑 PDF + MinerU / OCR</span>
            <input type="file" accept=".pdf,.doc,.docx,.ppt,.pptx,.md,.markdown,.xls,.xlsx,.txt,.srt,.vtt,.png,.jpg,.jpeg,.webp" onChange={(event) => submitFile(event.target.files?.[0] || null)} />
          </label>
          <label className="toggle-row">
            <input type="checkbox" checked={highPrecision} onChange={(event) => setHighPrecision(event.target.checked)} />
            <span>高精度解析</span>
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
                {(item.originalFilePath || item.originalFilename) && <p>{item.originalFilePath || item.originalFilename}</p>}
              </div>
              <div className="material-meta">
                <span className={`status-pill ${item.status === 'READY' ? 'indexed' : ''}`}>{formatStatus(item.status)}</span>
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

// 将资料类型转换为中文展示文本。
function formatDocumentType(type: string) {
  const normalized = type.toLowerCase();
  if (normalized === 'markdown') return 'Markdown';
  if (normalized === 'text') return '文本';
  return type.toUpperCase();
}

// 将资料来源转换为中文展示文本。
function formatSource(source: string) {
  if (source === 'manual') return '手动录入';
  return source;
}

// 将资料解析状态转换为中文展示文本。
function formatStatus(status: string) {
  if (status === 'READY') return '已入库';
  if (status === 'PARTIAL') return '部分完成';
  if (status === 'PENDING') return '等待解析';
  if (status === 'PARSING') return '解析中';
  if (status === 'REINDEXING') return '重建索引';
  if (status === 'FAILED') return '解析失败';
  return status;
}
