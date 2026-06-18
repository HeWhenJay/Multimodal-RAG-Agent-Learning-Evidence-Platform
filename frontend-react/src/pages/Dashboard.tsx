import {
  CalendarDays,
  Anchor,
  BarChart3,
  Bot,
  CheckCircle2,
  CloudUpload,
  Database,
  FileText,
  Flag,
  LibraryBig,
  Loader2,
  PlayCircle,
  Search,
  Send,
  TriangleAlert,
  Video
} from 'lucide-react';
import { DayPicker, type DateRange } from '@daypicker/react';
import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type DragEvent } from 'react';
import { fetchDashboardData } from '../api/pageData';
import { queryRag } from '../api/rag';
import type { DashboardData, LearningMaterial, RagEvidence } from '../api/types';
import { MATERIAL_FILE_ACCEPT, MATERIAL_UPLOADED_EVENT, useMaterialUpload } from '../hooks/useMaterialUpload';

const RECENT_LIMIT_MIN = 1;
const RECENT_LIMIT_MAX = 50;
const CALENDAR_FORMATTERS = {
  formatCaption: formatCalendarCaption,
  formatWeekdayName: formatCalendarWeekdayName
};

// 工作台首页展示 RAG 概览、检索入口和证据对齐摘要。
export function Dashboard() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [evidences, setEvidences] = useState<RagEvidence[]>([]);
  const [error, setError] = useState('');
  const [appliedRecentRange, setAppliedRecentRange] = useState<DateRange>(() => defaultRecentRange());
  const [draftRecentRange, setDraftRecentRange] = useState<DateRange>(() => defaultRecentRange());
  const [datePickerOpen, setDatePickerOpen] = useState(false);
  const [recentLimit, setRecentLimit] = useState(5);
  const { uploading, uploadMessage, uploadFile } = useMaterialUpload();
  const rangeBounds = useMemo(() => recentRangeBounds(), []);

  // 拉取工作台聚合数据。
  const loadDashboard = useCallback(async () => {
    try {
      const normalizedRange = completeRecentRange(appliedRecentRange, rangeBounds);
      const data = await fetchDashboardData({
        startDate: formatDateParam(normalizedRange.from),
        endDate: formatDateParam(normalizedRange.to),
        recentLimit
      });
      setDashboard(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '工作台数据加载失败');
    }
  }, [appliedRecentRange, rangeBounds, recentLimit]);

  useEffect(() => {
    void loadDashboard();
    window.addEventListener(MATERIAL_UPLOADED_EVENT, loadDashboard);
    return () => window.removeEventListener(MATERIAL_UPLOADED_EVENT, loadDashboard);
  }, [loadDashboard]);

  useEffect(() => {
    if (!dashboard?.recentMaterials?.some((item) => isProcessingStatus(item.status))) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void loadDashboard();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [dashboard?.recentMaterials, loadDashboard]);

  // 处理工作台文件选择上传。
  function handleUploadChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] || null;
    event.target.value = '';
    void uploadFile(file).catch(() => undefined);
  }

  // 处理拖拽到接入通道的文件上传。
  function handleUploadDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0] || null;
    void uploadFile(file).catch(() => undefined);
  }

  // 打开日期范围选择器时先复制当前已生效范围，避免半选状态直接触发查询。
  function openDatePicker() {
    setDraftRecentRange(appliedRecentRange);
    setDatePickerOpen(true);
  }

  // 用户点击确定后才应用日期范围并触发后端查询。
  function applyRecentRange() {
    setAppliedRecentRange(completeRecentRange(draftRecentRange, rangeBounds));
    setDatePickerOpen(false);
  }

  // 重置为最近 7 天并立即触发后端查询。
  function resetRecentRange() {
    const defaultRange = defaultRecentRange();
    setDraftRecentRange(defaultRange);
    setAppliedRecentRange(defaultRange);
    setDatePickerOpen(false);
  }

  // 执行一次 RAG 检索并刷新回答与证据列表。
  async function runQuery() {
    if (!question.trim()) {
      setError('请输入检索问题');
      return;
    }
    setError('');
    const result = await queryRag({ question, topK: 3 });
    setAnswer(result.answer);
    setEvidences(result.evidences);
  }

  const stats = [
    { label: '已入库材料', value: dashboard?.materialCount ?? 0, delta: dashboard?.materialDelta7Days ?? 0, note: '本周新增', icon: LibraryBig },
    { label: '视频片段', value: dashboard?.videoSliceCount ?? 0, delta: dashboard?.videoSliceDelta7Days ?? 0, note: '本周新增', icon: Video },
    { label: 'RAG 证据锚点', value: dashboard?.evidenceCount ?? 0, delta: dashboard?.evidenceCount ?? 0, note: '当前切块', icon: Anchor },
    { label: '待处理错误', value: dashboard?.openErrorCount ?? 0, delta: dashboard?.errorCount30Days ?? 0, note: '近 30 天错误', icon: Bot }
  ];

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>Agent 工作台</h2>
          <p>系统全局监控与多模态证据处理中心</p>
        </div>
        <div className="status-pill indexed">
          <Database size={15} />
          RAG 已就绪
        </div>
      </section>

      <section className="metric-grid">
        {stats.map((stat, index) => (
          <article className="metric-card" key={stat.label}>
            <div>
              <p>{stat.label}</p>
              <h3>{formatNumber(stat.value)}</h3>
              <span>
                <strong>{index < 2 ? `+${stat.delta}` : formatNumber(stat.delta)}</strong>
                {stat.note}
              </span>
            </div>
            <div className="metric-icon">
              <stat.icon size={24} />
            </div>
          </article>
        ))}
      </section>

      <section className="dashboard-grid">
        <article className="panel wide">
          <div className="panel-title">
            <h3>
              <Search size={20} />
              知识库智能检索 (RAG)
            </h3>
            <button className="chip-button">高级检索模式</button>
          </div>
          <div className="rag-input-row">
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
            <button className="send-button" onClick={runQuery} aria-label="发送问题">
              <Send size={18} />
            </button>
          </div>
          <div className="answer-box">
            <div className="answer-label">
              <Bot size={17} />
              RAG 回复
            </div>
            <p>{answer || '提交问题后展示基于数据库证据检索生成的回答。'}</p>
            <div className="citation-row">
              {evidences.length > 0 ? (
                evidences.slice(0, 3).map((item) => (
                  <span key={item.evidenceId}>
                    <FileText size={15} />
                    [{item.title} / {item.sectionTitle || item.sectionName}]
                  </span>
                ))
              ) : <span><FileText size={15} />暂无证据引用</span>}
            </div>
          </div>
          {error ? <p className="form-message danger">{error}</p> : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <CloudUpload size={20} />
              多模态数据接入通道
            </h3>
          </div>
          <label
            className={`upload-zone ${uploading ? 'is-busy' : ''}`}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleUploadDrop}
          >
            {uploading ? <Loader2 className="spin" size={28} /> : <CloudUpload size={28} />}
            <strong>{uploading ? '正在上传并索引' : '拖拽文件或点击上传'}</strong>
            <div className="format-row">
              {['PDF', 'DOCX', 'PPTX', 'MP4', 'MD'].map((format) => <span key={format}>{format}</span>)}
            </div>
            <input type="file" accept={MATERIAL_FILE_ACCEPT} disabled={uploading} onChange={handleUploadChange} />
          </label>
          {uploadMessage ? <p className="form-message">{uploadMessage}</p> : null}
          <div className="task-toolbar">
            <div className="task-heading">
              <h4>近期处理任务</h4>
              <small>{formatRecentTaskScope(dashboard)}</small>
            </div>
            <div className="task-filters" aria-label="近期处理任务筛选">
              <div className="task-date-range">
                <div className="task-date-field">
                  <span>从</span>
                  <button type="button" className="task-date-button" onClick={datePickerOpen ? () => setDatePickerOpen(false) : openDatePicker}>
                    <CalendarDays size={16} />
                    {formatDateLabel((datePickerOpen ? draftRecentRange : appliedRecentRange).from, '开始时间')}
                  </button>
                </div>
                <div className="task-date-field">
                  <span>到</span>
                  <button type="button" className="task-date-button" onClick={datePickerOpen ? () => setDatePickerOpen(false) : openDatePicker}>
                    <CalendarDays size={16} />
                    {formatDateLabel((datePickerOpen ? draftRecentRange : appliedRecentRange).to, '结束时间')}
                  </button>
                </div>
                {datePickerOpen ? (
                  <div className="task-calendar-popover" role="dialog" aria-label="选择近期处理任务日期范围">
                    <DayPicker
                      mode="range"
                      weekStartsOn={0}
                      selected={draftRecentRange}
                      onSelect={(range) => setDraftRecentRange(clampRecentRange(range, rangeBounds))}
                      disabled={[{ before: rangeBounds.minDate }, { after: rangeBounds.maxDate }]}
                      defaultMonth={draftRecentRange.to || rangeBounds.maxDate}
                      captionLayout="label"
                      formatters={CALENDAR_FORMATTERS}
                    />
                    <div className="task-calendar-footer">
                      <span>仅查询最近 7 天内任务</span>
                      <button type="button" onClick={resetRecentRange}>
                        重置
                      </button>
                      <button type="button" className="primary" onClick={applyRecentRange}>
                        确定
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
              <label>
                <span>条数</span>
                <input
                  aria-label="近期处理任务条数"
                  type="number"
                  min={RECENT_LIMIT_MIN}
                  max={RECENT_LIMIT_MAX}
                  value={recentLimit}
                  onChange={(event) => setRecentLimit(clampNumber(event.target.value, RECENT_LIMIT_MIN, RECENT_LIMIT_MAX))}
                />
              </label>
            </div>
          </div>
          {(dashboard?.recentMaterials || []).map((item) => (
            <div className="task-row" key={item.id}>
              <FileText size={20} />
              <span>
                <strong>{item.title}</strong>
                {item.latestProgress ? <small>{formatTaskProgress(item)}</small> : null}
                <small>{formatDocumentMeta(item)}</small>
              </span>
              <div className="task-status">
                <strong className={item.status === 'READY' ? '' : 'processing'}>{formatMaterialStatus(item.status)}</strong>
                <small>{item.chunkCount} 个切块</small>
              </div>
            </div>
          ))}
          {(dashboard?.recentMaterials || []).length === 0 ? <div className="empty-state">暂无资料处理任务</div> : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <BarChart3 size={20} />
              岗位适配分析
            </h3>
          </div>
          <label className="field-label">目标岗位描述 (JD) 输入</label>
          <textarea className="compact-textarea" value={dashboard?.latestJdAnalysis?.jobDescription || ''} readOnly placeholder="暂无 JD 分析记录" />
          <button className="full-action">
            <BarChart3 size={17} />
            查看适配分析
          </button>
          <h4>能力雷达匹配度</h4>
          <div className="stacked-bar" aria-label="能力匹配度">
            <span className="mastered" style={{ width: `${dashboard?.latestJdAnalysis?.masteredPercent || 0}%` }}>已掌握</span>
            <span className="partial" style={{ width: `${dashboard?.latestJdAnalysis?.partialPercent || 0}%` }}>待强化</span>
            <span className="gaps" style={{ width: `${dashboard?.latestJdAnalysis?.gapPercent || 0}%` }}>缺口</span>
          </div>
          <div className="plan-note">
            <Flag size={17} />
            <span>下一步学习计划：{dashboard?.latestJdAnalysis?.learningPlan?.[0]?.title || '暂无学习计划'}</span>
          </div>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <Video size={20} />
              视频知识切片回顾
            </h3>
          </div>
          {(dashboard?.recentVideoSlices || []).map((item) => (
            <div className="video-slice" key={item.id}>
              <div className="play-badge"><PlayCircle size={18} /></div>
              <div>
                <h4>{item.title}</h4>
                <span>知识命中</span>
                <p>知识片段：{item.topic}</p>
                <p>时间范围：{item.startTime} - {item.endTime}</p>
              </div>
            </div>
          ))}
          {(dashboard?.recentVideoSlices || []).length === 0 ? <div className="empty-state">暂无视频切片</div> : null}
        </article>

        <article className="panel wide">
          <div className="panel-title">
            <h3>
              <FileText size={20} />
              简历证据对齐 (JD 与简历)
            </h3>
            <span className="status-pill">复核模式</span>
          </div>
          <div className="evidence-stack">
            {(dashboard?.resumeAlignments || []).map((item) => (
              <div className="evidence-item" key={item.id}>
                <div className="evidence-field">
                  <span className="evidence-field-label">JD 要求</span>
                  <strong>{item.requirement}</strong>
                </div>
                <div className="evidence-field">
                  <span className="evidence-field-label">简历证据</span>
                  <p>{item.evidence}</p>
                </div>
                <div className="evidence-field">
                  <span className="evidence-field-label">状态</span>
                  <StatusIcon status={item.status} />
                </div>
              </div>
            ))}
            {(dashboard?.resumeAlignments || []).length === 0 ? <div className="empty-state">暂无简历证据对齐记录</div> : null}
          </div>
        </article>
      </section>
    </div>
  );
}

// 格式化统计数字展示。
function formatNumber(value: number) {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}k`;
  }
  return String(value);
}

// 将资料状态转换为中文展示。
function formatMaterialStatus(status: string) {
  if (status === 'READY') return '已入库';
  if (status === 'PARTIAL') return '部分完成';
  if (status === 'PARSING') return '解析中';
  if (status === 'PENDING') return '等待解析';
  if (status === 'REINDEXING') return '重建索引';
  if (status === 'FAILED') return '解析失败';
  return status;
}

// 展示资料类型、解析器和更新时间等任务元数据。
function formatDocumentMeta(item: LearningMaterial) {
  const parser = item.parser || '等待解析';
  const updatedAt = item.updatedAt ? `更新 ${formatDateTime(item.updatedAt)}` : '';
  return [item.documentType.toUpperCase(), parser, updatedAt].filter(Boolean).join(' · ');
}

// 展示后端实际生效的近期任务查询条件。
function formatRecentTaskScope(dashboard: DashboardData | null) {
  if (!dashboard?.recentTaskStartDate || !dashboard.recentTaskEndDate) {
    return '等待查询范围';
  }
  return `已按 ${dashboard.recentTaskStartDate} 至 ${dashboard.recentTaskEndDate} 查询 · ${dashboard.recentTaskLimit || 5} 条`;
}

// 格式化简短时间，方便任务列表扫描。
function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

// 默认查询最近 7 天内的任务。
function defaultRecentRange(): DateRange {
  const { minDate, maxDate } = recentRangeBounds();
  return { from: minDate, to: maxDate };
}

// 近期任务筛选只允许落在最近 7 天内。
function recentRangeBounds() {
  const maxDate = startOfLocalDay(new Date());
  const minDate = addDays(maxDate, -6);
  return { minDate, maxDate };
}

// 保留用户正在选择中的半完成日期范围，并限制在最近 7 天。
function clampRecentRange(range: DateRange | undefined, bounds: { minDate: Date; maxDate: Date }): DateRange {
  if (!range?.from) {
    return { from: bounds.minDate, to: bounds.maxDate };
  }
  const from = clampDate(range.from, bounds.minDate, bounds.maxDate);
  const to = range.to ? clampDate(range.to, bounds.minDate, bounds.maxDate) : undefined;
  if (to && from > to) {
    return { from: to, to: from };
  }
  return { from, to };
}

// 发起查询时把半完成日期范围补成完整范围。
function completeRecentRange(range: DateRange, bounds: { minDate: Date; maxDate: Date }) {
  const clamped = clampRecentRange(range, bounds);
  const from = clamped.from || bounds.minDate;
  const to = clamped.to || from;
  return from <= to ? { from, to } : { from: to, to: from };
}

// 日期查询参数使用本地 YYYY-MM-DD，避免 UTC 转换导致日期偏移。
function formatDateParam(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

// 日期输入框展示文案。
function formatDateLabel(date: Date | undefined, placeholder: string) {
  return date ? formatDateParam(date) : placeholder;
}

function formatCalendarCaption(date: Date) {
  return `${date.getFullYear()} 年 ${date.getMonth() + 1} 月`;
}

function formatCalendarWeekdayName(date: Date) {
  return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date: Date, days: number) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function clampDate(date: Date, minDate: Date, maxDate: Date) {
  const localDate = startOfLocalDay(date);
  if (localDate < minDate) return minDate;
  if (localDate > maxDate) return maxDate;
  return localDate;
}

// 将用户输入的近期任务条数约束在后端允许范围内。
function clampNumber(value: string, min: number, max: number) {
  const numberValue = Number(value);
  if (Number.isNaN(numberValue)) {
    return min;
  }
  return Math.max(min, Math.min(max, Math.trunc(numberValue)));
}

// 判断资料是否仍处于后台解析或重建中。
function isProcessingStatus(status: string) {
  return ['PENDING', 'PARSING', 'REINDEXING'].includes(status);
}

// 工作台任务行展示当前 RAG 阶段和切块计数。
function formatTaskProgress(item: LearningMaterial) {
  const progress = item.latestProgress;
  if (!progress) return '';
  const parts = [
    progress.message || progress.stageLabel || progress.stageCode,
    progress.currentChunk && progress.totalChunks ? `切块 ${progress.currentChunk}/${progress.totalChunks}` : '',
    typeof progress.percent === 'number' ? `${Math.round(progress.percent)}%` : ''
  ].filter(Boolean);
  return parts.join(' · ');
}

// 根据适配状态展示对应的中文状态标记。
function StatusIcon({ status }: { status: string }) {
  if (status === 'supported') {
    return <span className="evidence-status supported"><CheckCircle2 size={16} />证据充分</span>;
  }
  if (status === 'weak') {
    return <span className="evidence-status weak"><TriangleAlert size={16} />证据不足</span>;
  }
  return <span className="evidence-status missing">不建议写入</span>;
}
