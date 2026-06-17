import { Database, FileText, PlayCircle, Search, Send } from 'lucide-react';
import { useState } from 'react';
import { queryRag } from '../api/rag';
import type { RagQueryResult } from '../api/types';

// 知识库页负责提交 RAG 问题并展示回答和证据。
export function KnowledgeBase() {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<RagQueryResult | null>(null);
  const [error, setError] = useState('');

  // 提交问题并刷新 RAG 检索结果。
  async function submit() {
    setError('');
    if (!question.trim()) {
      setError('请输入检索问题');
      return;
    }
    try {
      setResult(await queryRag({ question, topK: 6 }));
    } catch (err) {
      setError(err instanceof Error ? err.message : '检索失败');
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
        {error && <p className="form-message danger">{error}</p>}
      </section>

      {result && (
        <section className="two-column evidence-layout">
          <article className="panel">
            <div className="panel-title">
              <h3>回答</h3>
              <span className="status-pill">{result.evidences.length} 条证据</span>
            </div>
            <p className="answer-copy">{result.answer}</p>
            <div className="query-tags">
              {result.expandedQueries.map((query) => <span key={query}>{query}</span>)}
            </div>
          </article>

          <article className="panel">
            <div className="panel-title">
              <h3><FileText size={20} />证据引用</h3>
            </div>
            <div className="evidence-list">
              {result.evidences.map((item) => (
                <div className="evidence-card" key={item.evidenceId}>
                  <div>
                    <strong>{item.title}</strong>
                    <span>
                      {item.documentType} · {formatEvidenceLocation(item)} · {item.retrievalSource || '融合检索'} · {item.parseEngine || '解析器未知'} · 分数 {item.score.toFixed(4)}
                    </span>
                  </div>
                  <p>{item.snippet}</p>
                  {item.playbackUrl && (
                    <div className="evidence-card-actions">
                      <a className="ghost-action evidence-play-link" href={item.playbackUrl}>
                        <PlayCircle size={16} />
                        从这里播放
                      </a>
                    </div>
                  )}
                </div>
              ))}
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
