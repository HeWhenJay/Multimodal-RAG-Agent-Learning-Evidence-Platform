import { Database, FileText, PlayCircle, Search, Send } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { runRagQueryTask } from '../../api/rag';
import type { RagProgress, RagQueryResult } from '../../api/types';
import { MarkdownText } from '../../components/MarkdownText';
import { RagQueryProgress } from '../../components/RagQueryProgress';
import { markRagQueryProgressFailed } from '../../services/ragQueryProgress';
import { buildVideoEvidenceLink } from '../../utils/videoEvidence';

// 知识库页负责提交 RAG 问题并展示回答和证据。
export function KnowledgeBase() {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<RagQueryResult | null>(null);
  const [error, setError] = useState('');
  const [documentType, setDocumentType] = useState('');
  const [mediaType, setMediaType] = useState('');
  const [evidenceChannel, setEvidenceChannel] = useState('');
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
      const metadataFilter: Record<string, string> = {};
      if (documentType) metadataFilter.documentType = documentType;
      if (mediaType) metadataFilter.mediaType = mediaType;
      if (evidenceChannel) metadataFilter.evidenceChannel = evidenceChannel;
      setResult(null);
      setQuerying(true);
      setQueryProgressEvents([]);
      queryAbortRef.current?.abort();
      controller = new AbortController();
      queryAbortRef.current = controller;
      const response = await runRagQueryTask(
        { question, topK: 6, metadataFilter },
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
        <div className="query-filter-row">
          <select value={documentType} onChange={(event) => setDocumentType(event.target.value)} aria-label="资料类型过滤">
            <option value="">全部资料类型</option>
            <option value="pdf">PDF</option>
            <option value="pptx">PPTX</option>
            <option value="markdown">Markdown</option>
            <option value="srt">字幕 SRT</option>
            <option value="vtt">字幕 VTT</option>
            <option value="mp4">视频 MP4</option>
            <option value="webm">视频 WEBM</option>
          </select>
          <select value={mediaType} onChange={(event) => setMediaType(event.target.value)} aria-label="媒体类型过滤">
            <option value="">全部媒体类型</option>
            <option value="video">视频</option>
          </select>
          <select value={evidenceChannel} onChange={(event) => setEvidenceChannel(event.target.value)} aria-label="证据通道过滤">
            <option value="">全部证据通道</option>
            <option value="subtitle">字幕 / ASR</option>
            <option value="frame_ocr">关键帧 OCR</option>
            <option value="video_metadata">视频元数据</option>
          </select>
        </div>
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
              <span className="status-pill">{result.evidences.length} 条证据</span>
            </div>
            <MarkdownText className="answer-copy" content={result.answer} />
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
              {result.evidences.map((item) => {
                const videoEvidenceLink = buildVideoEvidenceLink(item);
                return (
                  <div className="evidence-card" key={item.evidenceId}>
                    <div>
                      <strong>{item.title}</strong>
                      <span>
                        {item.documentType} · {formatEvidenceLocation(item)} · {item.retrievalSource || '融合检索'} · {item.parseEngine || '解析器未知'} · 分数 {item.score.toFixed(4)}
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

// 根据页码、幻灯片、表格区域或章节名生成证据位置。
function formatEvidenceLocation(item: RagQueryResult['evidences'][number]) {
  if (item.startTime) return item.endTime ? `${item.startTime} - ${item.endTime}` : item.startTime;
  if (item.pageIndex) return `第 ${item.pageIndex} 页`;
  if (item.slideIndex) return `第 ${item.slideIndex} 页幻灯片`;
  if (item.sheetName) return `${item.sheetName}${item.cellRange ? ` ${item.cellRange}` : ''}`;
  return item.sectionTitle || item.sectionName;
}
