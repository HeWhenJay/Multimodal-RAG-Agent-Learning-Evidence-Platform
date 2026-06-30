import {
  CalendarDays,
  Anchor,
  Bot,
  ChevronDown,
  CloudUpload,
  Database,
  FileText,
  LibraryBig,
  Loader2,
  Search,
  Send,
  SlidersHorizontal,
} from 'lucide-react';
import { DayPicker, type DateRange } from '@daypicker/react';
import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import { fetchDashboardData } from '../api/pageData';
import { fetchRagQueryHistory, runRagQueryTask } from '../api/rag';
import type { DashboardData, LearningMaterial, RagEvidence, RagProgress, RagQueryHistory } from '../api/types';
import { MarkdownText } from '../components/MarkdownText';
import { RagQueryProgress } from '../components/RagQueryProgress';
import { MATERIAL_FILE_ACCEPT, MATERIAL_UPLOADED_EVENT, useMaterialUpload } from '../hooks/useMaterialUpload';
import { mergeMaterialProgress, upsertMaterialWithProgress } from '../services/materialProgress';
import { markRagQueryProgressFailed } from '../services/ragQueryProgress';
import { buildPreviewHrefRewriter } from '../utils/evidencePreview';
import {
  BLOCK_TYPE_OPTIONS,
  DEFAULT_RAG_ADVANCED_SEARCH,
  DOCUMENT_TYPE_OPTIONS,
  EVIDENCE_CHANNEL_OPTIONS,
  SOURCE_OPTIONS,
  buildRagQueryPayload,
  clampNumber as clampRagNumber,
  formatRagFilterSummary,
  type RagAdvancedSearchState
} from '../utils/ragAdvancedSearch';

const RECENT_LIMIT_MIN = 1;
const RECENT_LIMIT_MAX = 50;
const PROCESSING_MATERIAL_STATUSES = new Set(['PENDING', 'PARSING', 'REINDEXING', 'UPLOADING', 'PROCESSING', 'RUNNING']);
const TERMINAL_PROGRESS_STATUSES = new Set(['COMPLETED', 'FAILED']);
const CALENDAR_FORMATTERS = {
  formatCaption: formatCalendarCaption,
  formatWeekdayName: formatCalendarWeekdayName
};

// 工作台首页展示 RAG 概览、检索入口和证据对齐摘要。
export function Dashboard() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [answerStatus, setAnswerStatus] = useState<string>('ANSWERED');
  const [refusalMessage, setRefusalMessage] = useState('');
  const [answerConfidence, setAnswerConfidence] = useState<number | null>(null);
  const [evidences, setEvidences] = useState<RagEvidence[]>([]);
  const [querying, setQuerying] = useState(false);
  const [advancedSearchOpen, setAdvancedSearchOpen] = useState(false);
  const [advancedSearch, setAdvancedSearch] = useState<RagAdvancedSearchState>(() => ({ ...DEFAULT_RAG_ADVANCED_SEARCH, topK: 3 }));
  const [queryProgressEvents, setQueryProgressEvents] = useState<RagProgress[]>([]);
  const queryAbortRef = useRef<AbortController | null>(null);
  const [queryHistory, setQueryHistory] = useState<RagQueryHistory[]>([]);
  const [queryHistoryLoading, setQueryHistoryLoading] = useState(false);
  const [queryHistoryError, setQueryHistoryError] = useState('');
  const [appliedQueryHistoryRange, setAppliedQueryHistoryRange] = useState<DateRange>(() => defaultRecentRange());
  const [draftQueryHistoryRange, setDraftQueryHistoryRange] = useState<DateRange>(() => defaultRecentRange());
  const [queryHistoryDatePickerOpen, setQueryHistoryDatePickerOpen] = useState(false);
  const [queryHistoryLimit, setQueryHistoryLimit] = useState(5);
  const [error, setError] = useState('');
  const [appliedRecentRange, setAppliedRecentRange] = useState<DateRange>(() => defaultRecentRange());
  const [draftRecentRange, setDraftRecentRange] = useState<DateRange>(() => defaultRecentRange());
  const [datePickerOpen, setDatePickerOpen] = useState(false);
  const [recentLimit, setRecentLimit] = useState(5);
  const [highPrecisionUpload, setHighPrecisionUpload] = useState(false);
  const { uploading, uploadMessage, uploadFile } = useMaterialUpload({
    highPrecision: highPrecisionUpload,
    onUploaded: (material) => {
      setDashboard((previous) => previous
        ? {
            ...previous,
            recentMaterials: upsertMaterialWithProgress(previous.recentMaterials || [], material).slice(0, recentLimit)
          }
        : previous);
    }
  });
  const rangeBounds = useMemo(() => recentRangeBounds(), []);
  const backgroundProgressMessage = useMemo(
    () => formatChannelProcessingMessage(dashboard?.recentMaterials || []),
    [dashboard?.recentMaterials]
  );
  const channelMessage = uploadMessage || backgroundProgressMessage;
  const channelBusy = uploading || Boolean(backgroundProgressMessage);

  // 拉取最近 RAG 询问历史，用于回答区回填。
  const loadQueryHistory = useCallback(async () => {
    try {
      setQueryHistoryLoading(true);
      setQueryHistoryError('');
      const normalizedRange = completeRecentRange(appliedQueryHistoryRange, rangeBounds);
      const items = await fetchRagQueryHistory({
        startDate: formatDateParam(normalizedRange.from),
        endDate: formatDateParam(normalizedRange.to),
        limit: queryHistoryLimit
      });
      setQueryHistory(items);
    } catch (historyError) {
      setQueryHistoryError(historyError instanceof Error ? historyError.message : 'RAG 询问历史加载失败');
    } finally {
      setQueryHistoryLoading(false);
    }
  }, [appliedQueryHistoryRange, queryHistoryLimit, rangeBounds]);

  // 拉取工作台聚合数据。
  const loadDashboard = useCallback(async () => {
    try {
      const normalizedRange = completeRecentRange(appliedRecentRange, rangeBounds);
      const data = await fetchDashboardData({
        startDate: formatDateParam(normalizedRange.from),
        endDate: formatDateParam(normalizedRange.to),
        recentLimit
      });
      setDashboard((previous) => ({
        ...data,
        recentMaterials: mergeMaterialProgress(previous?.recentMaterials || [], data.recentMaterials || [])
      }));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '工作台数据加载失败');
    }
  }, [appliedRecentRange, rangeBounds, recentLimit]);

  useEffect(() => {
    void loadDashboard();
    window.addEventListener(MATERIAL_UPLOADED_EVENT, loadDashboard);
    return () => {
      queryAbortRef.current?.abort();
      window.removeEventListener(MATERIAL_UPLOADED_EVENT, loadDashboard);
    };
  }, [loadDashboard]);

  useEffect(() => {
    void loadQueryHistory();
  }, [loadQueryHistory]);

  useEffect(() => {
    if (!dashboard?.recentMaterials?.some(isProcessingMaterial)) {
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

  // 打开 RAG 询问历史日期范围选择器。
  function openQueryHistoryDatePicker() {
    setDraftQueryHistoryRange(appliedQueryHistoryRange);
    setQueryHistoryDatePickerOpen(true);
  }

  // 应用 RAG 询问历史日期筛选。
  function applyQueryHistoryRange() {
    setAppliedQueryHistoryRange(completeRecentRange(draftQueryHistoryRange, rangeBounds));
    setQueryHistoryDatePickerOpen(false);
  }

  // 重置 RAG 询问历史为最近 7 天。
  function resetQueryHistoryRange() {
    const defaultRange = defaultRecentRange();
    setDraftQueryHistoryRange(defaultRange);
    setAppliedQueryHistoryRange(defaultRange);
    setQueryHistoryDatePickerOpen(false);
  }

  // 点击历史询问后回填问题、回答、证据和阶段事件。
  function applyQueryHistory(item: RagQueryHistory) {
    setQuestion(item.question);
    setAnswer(item.answer || item.errorMessage || '该次询问暂未生成回答。');
    setAnswerStatus(item.answerStatus || (item.evidences?.length ? 'ANSWERED' : 'REFUSED'));
    setRefusalMessage(item.refusalMessage || '');
    setAnswerConfidence(item.confidence ?? null);
    setEvidences(item.evidences || []);
    setQueryProgressEvents(item.progressEvents || []);
    setError(item.status === 'COMPLETED' ? '' : (item.errorMessage || '该次询问尚未完成'));
  }

  // 执行一次 RAG 检索并刷新回答与证据列表。
  async function runQuery() {
    if (!question.trim()) {
      setError('请输入检索问题');
      return;
    }
    let controller: AbortController | null = null;
    try {
      setError('');
      setAnswer('');
      setAnswerStatus('ANSWERED');
      setRefusalMessage('');
      setAnswerConfidence(null);
      setEvidences([]);
      setQuerying(true);
      setQueryProgressEvents([]);
      queryAbortRef.current?.abort();
      controller = new AbortController();
      queryAbortRef.current = controller;
      const result = await runRagQueryTask(
        buildRagQueryPayload(question, advancedSearch),
        (events) => {
          if (queryAbortRef.current === controller) {
            setQueryProgressEvents(events);
          }
        },
        { signal: controller.signal }
      );
      if (queryAbortRef.current !== controller) {
        return;
      }
      setAnswer(result.answer);
      setAnswerStatus(result.answerStatus || (result.evidences.length > 0 ? 'ANSWERED' : 'REFUSED'));
      setRefusalMessage(result.refusalMessage || '');
      setAnswerConfidence(result.confidence ?? null);
      setEvidences(result.evidences);
      setQueryProgressEvents(result.progressEvents || []);
      void loadQueryHistory();
    } catch (queryError) {
      if (queryError instanceof Error && queryError.message === 'RAG 检索已取消') {
        return;
      }
      setError(queryError instanceof Error ? queryError.message : '检索失败');
      setQueryProgressEvents((events) => markRagQueryProgressFailed(events));
    } finally {
      if (queryAbortRef.current === controller) {
        queryAbortRef.current = null;
        setQuerying(false);
      }
    }
  }

  const stats = [
    { label: '已入库材料', value: dashboard?.materialCount ?? 0, delta: dashboard?.materialDelta7Days ?? 0, note: '本周新增', icon: LibraryBig },
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
            <button
              type="button"
              className={`chip-button ${advancedSearchOpen ? 'is-active' : ''}`}
              onClick={() => setAdvancedSearchOpen((value) => !value)}
              aria-pressed={advancedSearchOpen}
            >
              <SlidersHorizontal size={15} />
              高级检索模式
            </button>
          </div>
          <div className="rag-input-row">
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
            <button className="send-button" onClick={runQuery} disabled={querying} aria-label="发送问题">
              {querying ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
            </button>
          </div>
          <AdvancedSearchPanel
            open={advancedSearchOpen}
            state={advancedSearch}
            onToggle={() => setAdvancedSearchOpen((value) => !value)}
            onChange={setAdvancedSearch}
          />
          {(querying || queryProgressEvents.length > 0) ? (
            <RagQueryProgress events={queryProgressEvents} running={querying} />
          ) : null}
          <div className="answer-box">
            <div className="answer-label">
              <Bot size={17} />
              RAG 回复
              <span className={`status-pill ${answerStatus === 'REFUSED' ? 'failed' : 'indexed'}`}>
                {formatAnswerStatusLabel(answerStatus, answerConfidence, evidences.length)}
              </span>
            </div>
            {answerStatus === 'REFUSED' && refusalMessage ? <p className="form-message danger">{refusalMessage}</p> : null}
            <MarkdownText
              content={answer || '提交问题后展示基于数据库证据检索生成的回答。'}
              rewriteHref={buildPreviewHrefRewriter(evidences)}
            />
            <div className="citation-row">
              {evidences.length > 0 ? (
                evidences.slice(0, 3).map((item) => (
                  <span key={item.evidenceId}>
                    <FileText size={15} />
                    [{item.title} / {item.sectionTitle || item.sectionName}]
                  </span>
                ))
              ) : <span><FileText size={15} />{answerStatus === 'REFUSED' ? '证据不足，已拒答' : '暂无证据引用'}</span>}
            </div>
          </div>
          <div className="query-history-panel">
            <div className="task-toolbar">
              <div className="task-heading">
                <h4>近期询问记录</h4>
                <small>{formatQueryHistoryScope(appliedQueryHistoryRange, queryHistoryLimit, queryHistoryLoading)}</small>
              </div>
              <div className="task-filters" aria-label="近期询问记录筛选">
                <div className="task-date-range">
                  <div className="task-date-field">
                    <span>从</span>
                    <button type="button" className="task-date-button" onClick={queryHistoryDatePickerOpen ? () => setQueryHistoryDatePickerOpen(false) : openQueryHistoryDatePicker}>
                      <CalendarDays size={16} />
                      {formatDateLabel((queryHistoryDatePickerOpen ? draftQueryHistoryRange : appliedQueryHistoryRange).from, '开始时间')}
                    </button>
                  </div>
                  <div className="task-date-field">
                    <span>到</span>
                    <button type="button" className="task-date-button" onClick={queryHistoryDatePickerOpen ? () => setQueryHistoryDatePickerOpen(false) : openQueryHistoryDatePicker}>
                      <CalendarDays size={16} />
                      {formatDateLabel((queryHistoryDatePickerOpen ? draftQueryHistoryRange : appliedQueryHistoryRange).to, '结束时间')}
                    </button>
                  </div>
                  {queryHistoryDatePickerOpen ? (
                    <div className="task-calendar-popover" role="dialog" aria-label="选择近期询问记录日期范围">
                      <DayPicker
                        mode="range"
                        weekStartsOn={0}
                        selected={draftQueryHistoryRange}
                        onSelect={(range) => setDraftQueryHistoryRange(clampRecentRange(range, rangeBounds))}
                        disabled={[{ before: rangeBounds.minDate }, { after: rangeBounds.maxDate }]}
                        defaultMonth={draftQueryHistoryRange.to || rangeBounds.maxDate}
                        captionLayout="label"
                        formatters={CALENDAR_FORMATTERS}
                      />
                      <div className="task-calendar-footer">
                        <span>仅查询最近 7 天内询问</span>
                        <button type="button" onClick={resetQueryHistoryRange}>
                          重置
                        </button>
                        <button type="button" className="primary" onClick={applyQueryHistoryRange}>
                          确定
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
                <label>
                  <span>条数</span>
                  <input
                    aria-label="近期询问记录条数"
                    type="number"
                    min={RECENT_LIMIT_MIN}
                    max={RECENT_LIMIT_MAX}
                    value={queryHistoryLimit}
                    onChange={(event) => setQueryHistoryLimit(clampNumber(event.target.value, RECENT_LIMIT_MIN, RECENT_LIMIT_MAX))}
                  />
                </label>
              </div>
            </div>
            <div className="query-history-list">
              {queryHistory.map((item) => (
                <button type="button" className="query-history-item" key={item.id} onClick={() => applyQueryHistory(item)}>
                  <span>
                    <strong>{item.question}</strong>
                    <small>{formatQueryHistoryMeta(item)}</small>
                  </span>
                  <em className={item.status === 'COMPLETED' ? 'done' : 'pending'}>{formatQueryHistoryStatus(item)}</em>
                </button>
              ))}
              {!queryHistoryLoading && queryHistory.length === 0 ? <div className="empty-state compact">暂无近期询问记录</div> : null}
              {queryHistoryLoading ? <div className="empty-state compact">正在加载询问记录...</div> : null}
            </div>
            {queryHistoryError ? <p className="form-message danger">{queryHistoryError}</p> : null}
          </div>
          {error ? <p className="form-message danger">{error}</p> : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <CloudUpload size={20} />
              多模态数据接入通道
            </h3>
            <button
              type="button"
              className={`chip-button ${highPrecisionUpload ? 'is-active' : ''}`}
              disabled={uploading}
              aria-pressed={highPrecisionUpload}
              onClick={() => setHighPrecisionUpload((enabled) => !enabled)}
            >
              高精度处理
            </button>
          </div>
          <label
            className={`upload-zone ${channelBusy ? 'is-busy' : ''}`}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleUploadDrop}
          >
            {channelBusy ? <Loader2 className="spin" size={28} /> : <CloudUpload size={28} />}
            <strong>{channelBusy ? '正在处理多模态资料' : '拖拽文件或点击上传'}</strong>
            <div className="format-row">
              {['PDF', 'DOCX', 'PPTX', 'MP4', 'MD'].map((format) => <span key={format}>{format}</span>)}
            </div>
            <input type="file" accept={MATERIAL_FILE_ACCEPT} disabled={uploading} onChange={handleUploadChange} />
          </label>
          {channelMessage ? <p className="form-message">{channelMessage}</p> : null}
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
                <strong className={displayMaterialStatus(item) === 'READY' ? '' : 'processing'}>{formatMaterialStatus(displayMaterialStatus(item))}</strong>
                <small>{item.chunkCount} 个切块</small>
              </div>
            </div>
          ))}
          {(dashboard?.recentMaterials || []).length === 0 ? <div className="empty-state">暂无资料处理任务</div> : null}
        </article>



      </section>
    </div>
  );
}

// 工作台快速检索的高级过滤面板，和知识库页使用同一套 payload 构建逻辑。
function AdvancedSearchPanel({
  open,
  state,
  onToggle,
  onChange
}: {
  open: boolean;
  state: RagAdvancedSearchState;
  onToggle: () => void;
  onChange: (state: RagAdvancedSearchState) => void;
}) {
  const update = (patch: Partial<RagAdvancedSearchState>) => onChange({ ...state, ...patch });
  return (
    <div className="advanced-search-panel compact">
      <button
        type="button"
        className={`advanced-search-toggle ${open ? 'is-open' : ''}`}
        onClick={onToggle}
        aria-expanded={open}
      >
        <span><SlidersHorizontal size={16} />高级检索</span>
        <ChevronDown size={16} />
      </button>
      <p className="advanced-search-summary">{formatRagFilterSummary(state)}</p>
      {open ? (
        <div className="advanced-search-grid">
          <label>
            <span>资料类型</span>
            <select value={state.documentType} onChange={(event) => update({ documentType: event.target.value })}>
              {DOCUMENT_TYPE_OPTIONS.map((item) => <option key={item.value || 'all'} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>
            <span>来源</span>
            <select value={state.source} onChange={(event) => update({ source: event.target.value })}>
              {SOURCE_OPTIONS.map((item) => <option key={item.value || 'all'} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>
            <span>证据通道</span>
            <select value={state.evidenceChannel} onChange={(event) => update({ evidenceChannel: event.target.value })}>
              {EVIDENCE_CHANNEL_OPTIONS.map((item) => <option key={item.value || 'all'} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>
            <span>块类型</span>
            <select value={state.blockType} onChange={(event) => update({ blockType: event.target.value })}>
              {BLOCK_TYPE_OPTIONS.map((item) => <option key={item.value || 'all'} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>
            <span>章节关键词</span>
            <input value={state.sectionKeyword} onChange={(event) => update({ sectionKeyword: event.target.value })} placeholder="例如 RAG-Fusion" />
          </label>
          <label>
            <span>topK</span>
            <input type="number" min={1} max={20} value={state.topK} onChange={(event) => update({ topK: clampRagNumber(Number(event.target.value), 1, 20) })} />
          </label>
          <label>
            <span>候选倍率</span>
            <input type="number" min={2} max={10} value={state.candidateMultiplier} onChange={(event) => update({ candidateMultiplier: clampRagNumber(Number(event.target.value), 2, 10) })} />
          </label>
        </div>
      ) : null}
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
  if (status === 'UPLOADING') return '上传中';
  if (status === 'PROCESSING') return '处理中';
  if (status === 'RUNNING') return '运行中';
  if (status === 'FAILED') return '解析失败';
  return status;
}

// 当前进度仍在运行时，以进度事件覆盖滞后的资料主状态。
function displayMaterialStatus(item: LearningMaterial) {
  return isProcessingMaterial(item) ? 'PARSING' : item.status;
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

// 展示 RAG 询问历史当前筛选条件。
function formatQueryHistoryScope(range: DateRange, limit: number, loading: boolean) {
  const normalizedRange = completeRecentRange(range, recentRangeBounds());
  const prefix = loading ? '正在查询' : '已按';
  return `${prefix} ${formatDateParam(normalizedRange.from)} 至 ${formatDateParam(normalizedRange.to)} 查询 · ${limit} 条`;
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

// 展示历史询问的状态、证据数和发起时间。
function formatQueryHistoryMeta(item: RagQueryHistory) {
  const createdAt = item.createdAt ? formatDateTime(item.createdAt) : '时间未知';
  const evidenceText = `${item.evidenceCount || item.evidences?.length || 0} 条证据`;
  const guardText = item.answerStatus === 'REFUSED'
    ? (item.refusalMessage || item.refusalReason || '证据不足')
    : '';
  const durationText = item.durationMs ? `耗时 ${formatDuration(item.durationMs)}` : '';
  return [createdAt, evidenceText, guardText, durationText].filter(Boolean).join(' · ');
}

// 将查询历史状态转换为中文展示。
function formatQueryHistoryStatus(item: RagQueryHistory) {
  const status = item.status;
  if (status === 'COMPLETED' && item.answerStatus === 'REFUSED') return '已拒答';
  if (status === 'COMPLETED') return '已完成';
  if (status === 'FAILED') return '失败';
  if (status === 'EXPIRED') return '已过期';
  if (status === 'RUNNING') return '检索中';
  return status || '未知';
}

// 展示回答准入状态，兼容旧响应缺少 answerStatus 的情况。
function formatAnswerStatusLabel(status: string, confidence: number | null, evidenceCount: number) {
  const resolved = status || (evidenceCount > 0 ? 'ANSWERED' : 'REFUSED');
  const prefix = resolved === 'REFUSED' ? '已拒答' : '已回答';
  return confidence == null ? prefix : `${prefix} · 置信度 ${confidence.toFixed(2)}`;
}

// 格式化查询耗时。
function formatDuration(value: number) {
  if (value < 1000) {
    return `${value}ms`;
  }
  return `${(value / 1000).toFixed(1)}s`;
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

// 判断资料是否仍处于后台处理，优先结合最新进度事件而非只看资料主状态。
function isProcessingMaterial(item: LearningMaterial) {
  if (PROCESSING_MATERIAL_STATUSES.has(normalizeStatus(item.status))) {
    return true;
  }
  const progress = item.latestProgress;
  if (!progress) {
    return false;
  }
  if (progress.currentChunk && progress.totalChunks && progress.currentChunk < progress.totalChunks) {
    return true;
  }
  if (typeof progress.percent === 'number' && progress.percent > 0 && progress.percent < 100) {
    return true;
  }
  if (normalizeStatus(progress.status) === 'RUNNING') {
    return true;
  }
  if (progress.status && TERMINAL_PROGRESS_STATUSES.has(normalizeStatus(progress.status))) {
    return false;
  }
  return false;
}

// 统一后端状态大小写，兼容不同接口返回的状态枚举。
function normalizeStatus(status: string | null | undefined) {
  return (status || '').trim().toUpperCase();
}

// 从近期任务恢复接入通道的后台处理提示，支持页面刷新后的进度展示。
function formatChannelProcessingMessage(items: LearningMaterial[]) {
  const processingItems = items.filter(isProcessingMaterial);
  const current = processingItems[0];
  if (!current) {
    return '';
  }

  const progressText = formatTaskProgress(current);
  const statusText = progressText || formatMaterialStatus(current.status);
  const extraCount = processingItems.length > 1 ? `，另有 ${processingItems.length - 1} 个任务处理中` : '';
  return `正在处理《${current.title}》：${statusText}${extraCount}`;
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

