import { FileSearch, FileUp, Filter, Images, Loader2, Plus, RefreshCw, RotateCcw, ScanLine, Wrench } from 'lucide-react';
import { useCallback, useEffect, useState, type DragEvent } from 'react';
import { fetchMaterials, indexText, reindexMaterial } from '../../api/rag';
import type { LearningMaterial, RagProgress } from '../../api/types';
import { MATERIAL_FILE_ACCEPT, MATERIAL_UPLOADED_EVENT, useMaterialUpload } from '../../hooks/useMaterialUpload';
import { mergeMaterialProgress, upsertMaterialWithProgress } from '../../services/materialProgress';

// 学习资料页负责文本索引、文件上传和资料状态展示。
export function LearningMaterials() {
  const [materials, setMaterials] = useState<LearningMaterial[]>([]);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [highPrecision, setHighPrecision] = useState(false);
  const [draggingFile, setDraggingFile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const { uploading, uploadMessage, uploadFile } = useMaterialUpload({
    highPrecision,
    onUploaded: (material) => setMaterials((previous) => upsertMaterialWithProgress(previous, material))
  });
  const actionBusy = busy || uploading;

  // 刷新最近学习资料列表。
  const refresh = useCallback(async () => {
    const data = await fetchMaterials();
    setMaterials((previous) => mergeMaterialProgress(previous, data));
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
    setDraggingFile(false);
    void uploadFile(file).catch(() => undefined);
  }

  // 允许从系统文件管理器拖入资料文件。
  function handleFileDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    if (!actionBusy) {
      setDraggingFile(true);
    }
  }

  // 离开上传区域时恢复普通展示状态。
  function handleFileDragLeave(event: DragEvent<HTMLLabelElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setDraggingFile(false);
    }
  }

  // 拖放文件后复用统一上传流程，保持高精度选项和进度提示一致。
  function handleFileDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0] || null;
    submitFile(file);
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
          <label
            className={`file-drop ${uploading ? 'is-busy' : ''} ${draggingFile ? 'is-dragging' : ''}`}
            onDragOver={handleFileDragOver}
            onDragLeave={handleFileDragLeave}
            onDrop={handleFileDrop}
          >
            {uploading ? <Loader2 className="spin" size={30} /> : <FileUp size={30} />}
            <strong>{draggingFile ? '松开鼠标开始上传' : 'PDF / DOC / PPT / XLSX / TXT / SRT / VTT / 图片 / 视频'}</strong>
            <span>拖入文件或点击选择，视频会继续抽取字幕和关键帧 evidence</span>
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
                    <div className="material-progress-metrics">
                      {item.latestProgress.stageLabel ? <span>{item.latestProgress.stageLabel}</span> : null}
                      {item.latestProgress.currentStep && item.latestProgress.totalSteps ? (
                        <span>流程 {item.latestProgress.currentStep}/{item.latestProgress.totalSteps}</span>
                      ) : null}
                      {item.latestProgress.currentChunk && item.latestProgress.totalChunks ? (
                        <span>切块 {item.latestProgress.currentChunk}/{item.latestProgress.totalChunks}</span>
                      ) : null}
                      {item.latestProgress.chunkId ? <span>{item.latestProgress.chunkId}</span> : null}
                    </div>
                    {item.latestProgress.detail && <p>{item.latestProgress.detail}</p>}
                    <VideoProgressPanel item={item} />
                    {progressTimeline(item).length > 0 && (
                      <ol className="material-progress-timeline" aria-label="RAG 最近处理流程">
                        {progressTimeline(item).map((progress, index) => (
                          <li key={`${progress.stageCode}-${progress.chunkId || index}-${progress.createdAt || index}`}>
                            <span className={progress.status === 'FAILED' ? 'failed' : progress.status === 'COMPLETED' ? 'completed' : ''} />
                            <div>
                              <strong>{timelineTitle(progress)}</strong>
                              <small>{timelineMeta(progress)}</small>
                            </div>
                          </li>
                        ))}
                      </ol>
                    )}
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

// 展示视频解析子阶段，让用户能看到抽帧、翻页检测、视觉去重和筛选过程。
function VideoProgressPanel({ item }: { item: LearningMaterial }) {
  const summary = videoProgressSummary(item);
  if (!summary) return null;
  const steps = [
    {
      key: 'extract',
      icon: <Images size={14} />,
      label: '抽取候选帧',
      value: summary.candidateCount !== null ? `${summary.candidateCount} 帧` : summary.extractEvent?.stageLabel || '等待',
      meta: compactParts([summary.scanMode, summary.sampleInterval, summary.maxCandidates]).join(' · '),
      done: Boolean(summary.candidateEvent || summary.slideDoneEvent || summary.ocrEvent)
    },
    {
      key: 'slide',
      icon: <ScanLine size={14} />,
      label: 'PPT 翻页检测',
      value: summary.pptFlipCount !== null ? `${summary.pptFlipCount} 次` : stageState(summary.slideDoneEvent || summary.slideStartEvent),
      meta: compactParts([summary.keepInterval, summary.minInterval]).join(' · '),
      done: Boolean(summary.slideDoneEvent)
    },
    {
      key: 'dedup',
      icon: <FileSearch size={14} />,
      label: '视觉去重',
      value: summary.visualDedupEnabled === 'false' ? '未启用' : summary.repeatVisualCount !== null ? `跳过 ${summary.repeatVisualCount} 帧` : stageState(summary.slideDoneEvent || summary.slideStartEvent),
      meta: summary.visualGroupCount ? `${summary.visualGroupCount} 个视觉组` : '',
      done: Boolean(summary.slideDoneEvent)
    },
    {
      key: 'select',
      icon: <Filter size={14} />,
      label: '最小间隔筛选',
      value: summary.selectedCount !== null ? `进入 OCR ${summary.selectedCount} 帧` : stageState(summary.slideDoneEvent),
      meta: compactParts([summary.maxOcrFrames, summary.ocrCandidateCount ? `候选 ${summary.ocrCandidateCount} 帧` : '']).join(' · '),
      done: Boolean(summary.slideDoneEvent)
    }
  ];

  return (
    <div className="video-progress-panel" aria-label="视频解析过程">
      <div className="video-progress-panel-head">
        <strong>视频解析过程</strong>
        <span>{summary.currentStage}</span>
      </div>
      <div className="video-progress-grid">
        {steps.map((step) => (
          <div className={`video-progress-step ${step.done ? 'done' : ''}`} key={step.key}>
            <span>{step.icon}</span>
            <div>
              <small>{step.label}</small>
              <strong>{step.value}</strong>
              {step.meta ? <em>{step.meta}</em> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
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

// 最近流程按时间正序展示，避免用户只能看到单个“解析中”状态。
function progressTimeline(item: LearningMaterial) {
  return (item.progressEvents || [])
    .slice(0, 6)
    .reverse();
}

// 生成单条流程节点标题。
function timelineTitle(progress: RagProgress) {
  return progress.message || progress.stageLabel || progress.stageCode;
}

// 生成单条流程节点附加信息。
function timelineMeta(progress: RagProgress) {
  const parts = [
    progress.stageCode,
    progress.currentStep && progress.totalSteps ? `流程 ${progress.currentStep}/${progress.totalSteps}` : '',
    progress.currentChunk && progress.totalChunks ? `切块 ${progress.currentChunk}/${progress.totalChunks}` : '',
    typeof progress.percent === 'number' ? `${Math.round(progress.percent)}%` : ''
  ].filter(Boolean);
  return parts.join(' · ');
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

// 汇总视频子阶段进度，优先使用后端保留的关键事件。
function videoProgressSummary(item: LearningMaterial) {
  const events = item.progressEvents || [];
  const videoEvents = events.filter((event) => event.stageCode?.startsWith('parse.video'));
  if (!videoEvents.length && !isVideoDocument(item.documentType)) {
    return null;
  }
  const extractEvent = latestStage(events, 'parse.video.frame.extract');
  const candidateEvent = latestStage(events, 'parse.video.frame.candidates');
  const slideEvents = events.filter((event) => event.stageCode === 'parse.video.slide_detect');
  const slideDoneEvent = slideEvents.find((event) => event.message?.includes('检测完成'));
  const slideStartEvent = slideDoneEvent ? slideEvents.find((event) => event !== slideDoneEvent) : slideEvents[0];
  const slideCurrentEvent = slideDoneEvent || slideStartEvent;
  const ocrEvent = latestStage(events, 'parse.video.ocr');
  const mergedDetail = [
    extractEvent?.detail,
    candidateEvent?.detail,
    slideStartEvent?.detail,
    slideDoneEvent?.detail
  ].filter(Boolean).join('; ');
  const slideMessage = slideCurrentEvent?.message || '';
  const candidateCount = firstNumber(detailValue(mergedDetail, 'candidateCount'), matchNumber(slideMessage, /候选帧\s*(\d+)\s*个/));
  const selectedCount = firstNumber(detailValue(mergedDetail, 'selectedCount'), matchNumber(slideMessage, /最终进入 OCR\s*(\d+)/));
  const pptFlipCount = firstNumber(detailValue(mergedDetail, 'pptFlipCount'), matchNumber(slideMessage, /翻页命中\s*(\d+)\s*个/));
  const repeatVisualCount = firstNumber(detailValue(mergedDetail, 'repeatVisualCount'), matchNumber(slideMessage, /视觉重复跳过\s*(\d+)\s*个/));

  return {
    extractEvent,
    candidateEvent,
    slideStartEvent,
    slideDoneEvent,
    ocrEvent,
    currentStage: ocrEvent?.message || slideCurrentEvent?.message || candidateEvent?.message || extractEvent?.message || '等待视频解析进度',
    scanMode: labelValue('扫描', detailText(mergedDetail, 'scanMode')),
    sampleInterval: secondsLabel('采样间隔', detailNumber(mergedDetail, 'sampleIntervalSeconds')),
    keepInterval: secondsLabel('兜底间隔', detailNumber(mergedDetail, 'keepIntervalSeconds')),
    minInterval: secondsLabel('最小间隔', detailNumber(mergedDetail, 'minIntervalSeconds')),
    maxCandidates: labelValue('候选上限', detailText(mergedDetail, 'maxCandidates')),
    maxOcrFrames: labelValue('OCR 上限', detailText(mergedDetail, 'maxOcrFrames')),
    visualDedupEnabled: detailText(mergedDetail, 'visualDedupEnabled'),
    candidateCount,
    selectedCount,
    pptFlipCount,
    repeatVisualCount,
    ocrCandidateCount: detailValue(mergedDetail, 'ocrCandidateCount'),
    visualGroupCount: detailValue(mergedDetail, 'visualGroupCount')
  };
}

function isVideoDocument(type: string) {
  return ['mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi'].includes(type.toLowerCase());
}

function latestStage(events: RagProgress[], stageCode: string) {
  return events.find((event) => event.stageCode === stageCode);
}

function stageState(progress?: RagProgress) {
  if (!progress) return '等待';
  if (progress.status === 'FAILED') return '失败';
  if (progress.status === 'COMPLETED') return '完成';
  return '进行中';
}

function detailText(detail: string, key: string) {
  const match = detail.match(new RegExp(`${escapeRegExp(key)}=([^;]+)`));
  return match?.[1]?.trim() || '';
}

function detailNumber(detail: string, key: string) {
  const value = detailText(detail, key);
  const match = value.match(/\d+/);
  return match ? Number(match[0]) : null;
}

function detailValue(detail: string, key: string) {
  const value = detailNumber(detail, key);
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function matchNumber(text: string, pattern: RegExp) {
  const match = text.match(pattern);
  return match ? Number(match[1]) : null;
}

function firstNumber(...values: Array<number | null>) {
  return values.find((value) => typeof value === 'number' && Number.isFinite(value)) ?? null;
}

function labelValue(label: string, value?: string | null) {
  return value ? `${label} ${value}` : '';
}

function secondsLabel(label: string, value: number | null) {
  return typeof value === 'number' ? `${label} ${value}s` : '';
}

function compactParts(parts: Array<string | null | undefined>) {
  return parts.filter((part): part is string => Boolean(part));
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
