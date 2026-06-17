import { FileUp, Loader2, Plus, RefreshCw, RotateCcw, Wrench } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { fetchMaterials, indexText, reindexMaterial } from '../../api/rag';
import type { LearningMaterial } from '../../api/types';
import { MATERIAL_FILE_ACCEPT, MATERIAL_UPLOADED_EVENT, useMaterialUpload } from '../../hooks/useMaterialUpload';

// 学习资料页负责文本索引、文件上传和资料状态展示。
export function LearningMaterials() {
  const [materials, setMaterials] = useState<LearningMaterial[]>([]);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [highPrecision, setHighPrecision] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const { uploading, uploadMessage, uploadFile } = useMaterialUpload({ highPrecision });
  const actionBusy = busy || uploading;

  // 刷新最近学习资料列表。
  const refresh = useCallback(async () => {
    const data = await fetchMaterials();
    setMaterials(data);
  }, []);

  useEffect(() => {
    void refresh().catch(() => undefined);
    const handleUploaded = () => {
      void refresh().catch(() => undefined);
    };
    window.addEventListener(MATERIAL_UPLOADED_EVENT, handleUploaded);
    return () => window.removeEventListener(MATERIAL_UPLOADED_EVENT, handleUploaded);
  }, [refresh]);

  useEffect(() => {
    if (!materials.some((item) => isProcessingStatus(item.status))) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [materials, refresh]);

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
  function submitFile(file: File | null) {
    setMessage('');
    void uploadFile(file).catch(() => undefined);
  }

  // 重新读取原始文件，支持普通重建和高精度补跑。
  async function submitReindex(item: LearningMaterial, highPrecisionRepair: boolean) {
    setBusy(true);
    setMessage('');
    try {
      await reindexMaterial(item.id, highPrecisionRepair);
      setMessage(highPrecisionRepair ? '已触发高精度补跑' : '已触发索引重建');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '重建索引失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>学习资料</h2>
          <p>Markdown、PDF、Word、PPT、字幕、转写文本与课程视频入口</p>
        </div>
        <button className="ghost-action" onClick={() => void refresh()} disabled={actionBusy}>
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
          <button className="full-action" onClick={submitText} disabled={actionBusy}>
            {busy ? <Loader2 className="spin" size={17} /> : <Plus size={17} />}
            建立索引
          </button>
          {message && <p className="form-message">{message}</p>}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3><FileUp size={20} />多格式文件解析</h3>
          </div>
          <label className={`file-drop ${uploading ? 'is-busy' : ''}`}>
            {uploading ? <Loader2 className="spin" size={30} /> : <FileUp size={30} />}
            <strong>PDF / DOC / PPT / XLSX / TXT / SRT / VTT / 图片 / 视频</strong>
            <span>文件先进入配置的对象存储，视频会继续抽取字幕和关键帧 evidence</span>
            <input
              type="file"
              accept={MATERIAL_FILE_ACCEPT}
              disabled={actionBusy}
              onChange={(event) => {
                const file = event.target.files?.[0] || null;
                event.target.value = '';
                submitFile(file);
              }}
            />
          </label>
          {uploadMessage ? <p className="form-message">{uploadMessage}</p> : null}
          <label className="toggle-row">
            <input type="checkbox" checked={highPrecision} disabled={actionBusy} onChange={(event) => setHighPrecision(event.target.checked)} />
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
                <span>{formatDocumentType(item.documentType)} · {formatSource(item.source)} · {formatStorage(item.storageType)} · {item.parser || '等待解析'}</span>
                <p>{item.documentSummary || '等待索引摘要'}</p>
                {(item.publicUrl || item.originalFilePath || item.originalFilename) && <p>{item.publicUrl || item.originalFilePath || item.originalFilename}</p>}
                {item.latestProgress && (
                  <div className="material-progress">
                    <div className="material-progress-head">
                      <span>{formatProgressTitle(item)}</span>
                      <strong>{progressPercent(item)}%</strong>
                    </div>
                    <div
                      className="material-progress-bar"
                      role="progressbar"
                      aria-label="RAG 处理进度"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={progressPercent(item)}
                    >
                      <span style={{ width: `${progressPercent(item)}%` }} />
                    </div>
                    {item.latestProgress.detail && <p>{item.latestProgress.detail}</p>}
                  </div>
                )}
              </div>
              <div className="material-meta">
                <span className={`status-pill ${item.status === 'READY' ? 'indexed' : ''}`}>{formatStatus(item.status)}</span>
                <strong>{item.chunkCount} 个切块</strong>
                {item.storageType !== 'manual' && (
                  <div className="material-actions">
                    <button className="icon-button tiny" onClick={() => submitReindex(item, false)} disabled={actionBusy} aria-label="重建索引" title="重建索引">
                      <RotateCcw size={15} />
                    </button>
                    <button className="icon-button tiny" onClick={() => submitReindex(item, true)} disabled={actionBusy} aria-label="高精度补跑" title="高精度补跑">
                      <Wrench size={15} />
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
          {materials.length === 0 && <div className="empty-state">暂无资料记录</div>}
        </div>
      </section>
    </div>
  );
}

// 判断资料是否仍处于后台解析或重建中。
function isProcessingStatus(status: string) {
  return ['PENDING', 'PARSING', 'REINDEXING'].includes(status);
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

// 将资料存储位置转换为中文展示文本。
function formatStorage(storageType?: string | null) {
  if (storageType === 'oss') return '阿里 OSS';
  if (storageType === 'manual') return '手动资料';
  return '本地存储';
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

// 生成资料行中的当前进度标题。
function formatProgressTitle(item: LearningMaterial) {
  const progress = item.latestProgress;
  if (!progress) return '等待 RAG 进度';
  const message = progress.message || progress.stageLabel || progress.stageCode;
  const chunkLabel = progress.currentChunk && progress.totalChunks
    ? `第 ${progress.currentChunk}/${progress.totalChunks} 块`
    : '';
  return `${message}${chunkLabel && !message.includes(chunkLabel) ? ` · ${chunkLabel}` : ''}`;
}

// 读取后端进度百分比，缺省时按状态兜底。
function progressPercent(item: LearningMaterial) {
  const value = item.latestProgress?.percent;
  if (typeof value === 'number') {
    return Math.max(0, Math.min(100, Math.round(value)));
  }
  if (item.status === 'READY' || item.status === 'PARTIAL') return 100;
  if (item.status === 'FAILED') return 0;
  return 12;
}
