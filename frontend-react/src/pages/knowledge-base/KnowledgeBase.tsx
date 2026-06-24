import { ChevronDown, Database, FileText, PlayCircle, Search, Send, SlidersHorizontal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { runRagQueryTask } from '../../api/rag';
import type { RagProgress, RagQueryResult } from '../../api/types';
import { MarkdownText } from '../../components/MarkdownText';
import { RagQueryProgress } from '../../components/RagQueryProgress';
import { markRagQueryProgressFailed } from '../../services/ragQueryProgress';
import { buildMaterialPreviewLink, buildPreviewHrefRewriter, extractEvidenceAnchor, normalizeComparableSource } from '../../utils/evidencePreview';
import {
  BLOCK_TYPE_OPTIONS,
  DEFAULT_RAG_ADVANCED_SEARCH,
  DOCUMENT_TYPE_OPTIONS,
  EVIDENCE_CHANNEL_OPTIONS,
  SOURCE_OPTIONS,
  buildRagQueryPayload,
  clampNumber,
  formatRagFilterSummary,
  type RagAdvancedSearchState
} from '../../utils/ragAdvancedSearch';
import { buildVideoEvidenceLink } from '../../utils/videoEvidence';

// 知识库页负责提交 RAG 问题并展示回答和证据。
export function KnowledgeBase() {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<RagQueryResult | null>(null);
  const [error, setError] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [advancedSearch, setAdvancedSearch] = useState<RagAdvancedSearchState>(() => ({ ...DEFAULT_RAG_ADVANCED_SEARCH, topK: 6 }));
  const [querying, setQuerying] = useState(false);
  const [queryProgressEvents, setQueryProgressEvents] = useState<RagProgress[]>([]);
  const queryAbortRef = useRef<AbortController | null>(null);

  useEffect(() => () => queryAbortRef.current?.abort(), []);

  // 提交问题并刷新 RAG 检索结果。
  async function submit() {
    setError('');
    if (!question.trim()) {
      setError('请输入检索问题');
      return;
    }
    let controller: AbortController | null = null;
    try {
      setResult(null);
      setQuerying(true);
      setQueryProgressEvents([]);
      queryAbortRef.current?.abort();
      controller = new AbortController();
      queryAbortRef.current = controller;
      const response = await runRagQueryTask(
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
      setResult(response);
      setQueryProgressEvents(response.progressEvents || []);
    } catch (err) {
      if (err instanceof Error && err.message === 'RAG 检索已取消') {
        return;
      }
      setError(err instanceof Error ? err.message : '检索失败');
      setQueryProgressEvents((events) => markRagQueryProgressFailed(events));
    } finally {
      if (queryAbortRef.current === controller) {
        queryAbortRef.current = null;
        setQuerying(false);
      }
    }
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>个人知识库</h2>
          <p>RAG 检索、证据锚点与引用结果</p>
        </div>
        <div className="status-pill indexed"><Database size={15} />混合检索</div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3><Search size={20} />知识库智能检索</h3>
        </div>
        <div className="rag-input-row tall">
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
          <button className="send-button" onClick={submit} aria-label="发送问题">
            <Send size={18} />
          </button>
        </div>
        <AdvancedSearchPanel
          open={advancedOpen}
          state={advancedSearch}
          onToggle={() => setAdvancedOpen((value) => !value)}
          onChange={setAdvancedSearch}
        />
        {error && <p className="form-message danger">{error}</p>}
      </section>

      {(querying || queryProgressEvents.length > 0) && (
        <RagQueryProgress events={queryProgressEvents} running={querying} />
      )}

      {result && (
        <section className="two-column evidence-layout">
          <article className="panel">
            <div className="panel-title">
              <h3>回答</h3>
              <span className={`status-pill ${result.answerStatus === 'REFUSED' ? 'failed' : 'indexed'}`}>
                {formatAnswerStatus(result)}
              </span>
              <span className="status-pill">{result.evidences.length} 条证据</span>
            </div>
            {result.answerStatus === 'REFUSED' && (
              <p className="form-message danger">{result.refusalMessage || '证据不足，已拒答'}</p>
            )}
            <MarkdownText className="answer-copy" content={result.answer} rewriteHref={buildPreviewHrefRewriter(result.evidences)} />
            <div className="query-tags">
              {result.expandedQueries.map((query) => <span key={query}>{query}</span>)}
            </div>
            {result.diagnostics && (
              <div className="query-tags diagnostics-tags">
                <span>回答：{String(result.diagnostics.answerProvider || '未知')}</span>
                <span>模型：{String(result.diagnostics.answerModel || '未返回')}</span>
                <span>重排：{String(result.diagnostics.rerankProvider || '未知')}</span>
              </div>
            )}
          </article>

          <article className="panel">
            <div className="panel-title">
              <h3><FileText size={20} />证据引用</h3>
            </div>
            <div className="evidence-list">
              {result.evidences.length === 0 ? <div className="empty-state compact">暂无可支持本次回答的证据</div> : null}
              {result.evidences.map((item) => {
                const videoEvidenceLink = buildVideoEvidenceLink(item);
                const location = formatEvidenceLocation(item);
                const locationLink = buildEvidenceLocationLink(item);
                return (
                  <div className="evidence-card" key={item.evidenceId}>
                    <div>
                      <strong>{item.title}</strong>
                      <span>
                        {item.documentType} · {locationLink ? (
                          <a className="evidence-location-link" href={locationLink} target="_blank" rel="noreferrer">
                            {location}
                          </a>
                        ) : location} · {item.retrievalSource || '融合检索'} · {item.parseEngine || '解析器未知'} · 分数 {item.score.toFixed(4)}
                      </span>
                    </div>
                    <p>{item.snippet}</p>
                    {videoEvidenceLink && (
                      <div className="evidence-card-actions">
                        <Link className="ghost-action evidence-play-link" to={videoEvidenceLink}>
                          <PlayCircle size={16} />
                          播放定位
                        </Link>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </article>
        </section>
      )}
    </div>
  );
}

// 格式化回答准入状态，兼容旧响应缺少 answerStatus 的情况。
function formatAnswerStatus(result: RagQueryResult) {
  if (result.answerStatus === 'REFUSED') {
    return result.confidence == null ? '已拒答' : `已拒答 · 置信度 ${result.confidence.toFixed(2)}`;
  }
  if (result.answerStatus === 'ANSWERED') {
    return result.confidence == null ? '已回答' : `已回答 · 置信度 ${result.confidence.toFixed(2)}`;
  }
  return result.evidences.length > 0 ? '已回答' : '证据不足';
}

// 高级检索控件只生成结构化 metadataFilter，不展示用户权限字段。
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
    <div className="advanced-search-panel">
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
            <input type="number" min={1} max={20} value={state.topK} onChange={(event) => update({ topK: clampNumber(Number(event.target.value), 1, 20) })} />
          </label>
          <label>
            <span>候选倍率</span>
            <input type="number" min={2} max={10} value={state.candidateMultiplier} onChange={(event) => update({ candidateMultiplier: clampNumber(Number(event.target.value), 2, 10) })} />
          </label>
        </div>
      ) : null}
    </div>
  );
}

// 根据页码、幻灯片、表格区域或章节名生成证据位置。
function formatEvidenceLocation(item: RagQueryResult['evidences'][number]) {
  if (item.startTime) return item.endTime ? `${item.startTime} - ${item.endTime}` : item.startTime;
  if (item.pageIndex) return `第 ${item.pageIndex} 页`;
  if (item.slideIndex) return `第 ${item.slideIndex} 页幻灯片`;
  if (item.sheetName) return `${item.sheetName}${item.cellRange ? ` ${item.cellRange}` : ''}`;
  return cleanEvidenceLocation(item.sectionTitle || item.sectionName);
}

// 清理解析来源里的 Markdown 链接，只在证据卡片展示可读位置文本。
function cleanEvidenceLocation(value?: string | null) {
  const text = (value || '').trim();
  if (!text) return '全文';
  return text
    .replace(/!\[([^\]]*)]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/[*_`]+/g, '')
    .replace(/\s+/g, ' ')
    .trim() || '全文';
}

// 使用 evidence 来源文件作为章节跳转目标，避免跳到当前 React 根页面的无效 hash。
function buildEvidenceLocationLink(item: RagQueryResult['evidences'][number]) {
  const previewLink = buildMaterialPreviewLink(item);
  if (previewLink) return previewLink;
  const source = normalizeComparableSource(item.sourcePath || item.source);
  if (!source) return '';
  const anchor = extractEvidenceAnchor(item.sectionTitle || item.sectionName);
  if (!anchor) return source;
  return `${source.split('#', 1)[0]}#${anchor}`;
}
