import { Database, FileText, Search, Send } from 'lucide-react';
import { useState } from 'react';
import { queryRag } from '../api/rag';
import type { RagQueryResult } from '../api/types';

export function KnowledgeBase() {
  const [question, setQuestion] = useState('BM25 和向量检索如何融合？');
  const [result, setResult] = useState<RagQueryResult | null>(null);
  const [error, setError] = useState('');

  async function submit() {
    setError('');
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
                      {item.documentType} · {formatEvidenceLocation(item)} · {item.retrievalSource || 'fusion'} · {item.parseEngine || 'parser'} · 分数 {item.score.toFixed(4)}
                    </span>
                  </div>
                  <p>{item.snippet}</p>
                </div>
              ))}
            </div>
          </article>
        </section>
      )}
    </div>
  );
}

function formatEvidenceLocation(item: RagQueryResult['evidences'][number]) {
  if (item.pageIndex) return `第 ${item.pageIndex} 页`;
  if (item.slideIndex) return `第 ${item.slideIndex} 页幻灯片`;
  if (item.sheetName) return `${item.sheetName}${item.cellRange ? ` ${item.cellRange}` : ''}`;
  return item.sectionTitle || item.sectionName;
}
