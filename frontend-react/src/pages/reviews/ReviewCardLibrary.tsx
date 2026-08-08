import { ArrowLeft, BookOpen, Check, ChevronDown, ChevronUp, FileText, FileVideo2, LibraryBig, Loader2, Pencil, RefreshCw, Search, Sparkles } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { REVIEW_CONTENT_UPDATED_EVENT, REVIEW_OVERVIEW_UPDATED_EVENT, fetchReviewCardLibrary, type ReviewCard, type ReviewCardLibrary as ReviewCardLibraryData } from '../../api/reviews';
import { MarkdownText } from '../../components/MarkdownText';
import '../../styles/ReviewCenter.css';
import { ReviewCardEditDialog } from './ReviewCardEditDialog';
import { ReviewCardRewriteDialog } from './ReviewCardRewriteDialog';

type LibraryFilter = 'ALL' | 'REVIEWED' | 'UNREVIEWED';

// 卡片库展示所有活动卡片，不受今日到期和已评分隐藏规则影响，也不提供评分入口。
export function ReviewCardLibrary() {
  const [library, setLibrary] = useState<ReviewCardLibraryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<LibraryFilter>('ALL');
  const [expandedMaterialIds, setExpandedMaterialIds] = useState<Record<number, boolean>>({});
  const [editTarget, setEditTarget] = useState<ReviewCard | null>(null);
  const [rewriteTarget, setRewriteTarget] = useState<ReviewCard | null>(null);

  async function loadLibrary(showLoading = true) {
    if (showLoading) setLoading(true);
    setError('');
    try {
      const result = await fetchReviewCardLibrary();
      setLibrary(result);
      setExpandedMaterialIds((previous) => {
        if (Object.keys(previous).length) return previous;
        return Object.fromEntries(result.materials.map((material, index) => [material.materialId, index === 0]));
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '卡片库读取失败');
    } finally {
      if (showLoading) setLoading(false);
    }
  }

  useEffect(() => {
    void loadLibrary();
  }, []);

  useEffect(() => {
    const refresh = () => void loadLibrary(false);
    window.addEventListener(REVIEW_CONTENT_UPDATED_EVENT, refresh);
    return () => window.removeEventListener(REVIEW_CONTENT_UPDATED_EVENT, refresh);
  }, []);

  const filteredMaterials = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return (library?.materials || []).map((material) => {
      const cards = material.cards.filter((card) => {
        if (filter === 'REVIEWED' && card.reviewCount <= 0) return false;
        if (filter === 'UNREVIEWED' && card.reviewCount > 0) return false;
        if (!normalizedQuery) return true;
        return [material.title, material.folderName || '', card.question, card.answer || '', card.hint || '']
          .join(' ')
          .toLocaleLowerCase()
          .includes(normalizedQuery);
      });
      return { ...material, cards };
    }).filter((material) => material.cards.length > 0);
  }, [filter, library, query]);

  function applyUpdatedCard(updated: ReviewCard) {
    setLibrary((previous) => previous ? {
      ...previous,
      materials: previous.materials.map((material) => material.materialId === updated.materialId ? {
        ...material,
        cards: material.cards.map((card) => card.id === updated.id ? updated : card),
        reviewedCardCount: material.cards.reduce((count, card) => count + ((card.id === updated.id ? updated : card).reviewCount > 0 ? 1 : 0), 0)
      } : material)
    } : previous);
    setMessage('卡片内容已保存，复习进度保持不变');
    window.dispatchEvent(new Event(REVIEW_CONTENT_UPDATED_EVENT));
    window.dispatchEvent(new Event(REVIEW_OVERVIEW_UPDATED_EVENT));
  }

  return (
    <div className="review-center-page review-card-library-page">
      <header className="review-page-header review-library-header">
        <div>
          <Link className="review-folder-back" to="/reviews"><ArrowLeft size={15} />返回复习中心</Link>
          <div className="page-eyebrow"><LibraryBig size={14} />全量卡片库</div>
          <h2>所有文档的所有卡片</h2>
          <p>已复习卡片也会保留在这里，仅供查看和修改，不提供评分。</p>
        </div>
        <button className="outline-action" type="button" onClick={() => void loadLibrary()} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={16} />刷新卡片库</button>
      </header>

      {error ? <div className="review-alert danger">{error}</div> : null}
      {message ? <div className="review-alert success"><Check size={16} />{message}</div> : null}

      <section className="review-stat-strip review-library-stat-strip" aria-label="卡片库统计">
        <div className="review-stat primary"><span>文档</span><strong>{library?.totalMaterialCount ?? '--'}</strong><small>当前仍在复习中心的文档</small></div>
        <div className="review-stat"><span>全部卡片</span><strong>{library?.totalCardCount ?? '--'}</strong><small>包括未来到期和已复习卡片</small></div>
        <div className="review-stat"><span>已复习卡片</span><strong>{library?.reviewedCardCount ?? '--'}</strong><small>至少完成过一次评分</small></div>
      </section>

      <section className="review-library-toolbar" aria-label="卡片库筛选">
        <label className="review-library-search"><Search size={16} /><input value={query} placeholder="搜索文档、问题、答案或提示" onChange={(event) => setQuery(event.target.value)} /></label>
        <div className="review-library-filter" role="group" aria-label="复习状态筛选">
          {([['ALL', '全部'], ['REVIEWED', '已复习'], ['UNREVIEWED', '未复习']] as Array<[LibraryFilter, string]>).map(([value, label]) => <button key={value} type="button" className={filter === value ? 'is-active' : ''} onClick={() => setFilter(value)}>{label}</button>)}
        </div>
        <span>{filteredMaterials.reduce((count, material) => count + material.cards.length, 0)} 张匹配卡片</span>
      </section>

      {loading ? <div className="review-loading"><Loader2 className="spin" size={22} /><span>正在读取全部卡片</span></div> : null}
      {!loading && !filteredMaterials.length ? <div className="review-folder-detail-empty"><LibraryBig size={28} /><h3>没有符合条件的卡片</h3><p>可以调整搜索词或筛选条件，也可以回到复习中心创建卡片。</p></div> : null}
      {!loading && filteredMaterials.length ? <div className="review-library-material-list">{filteredMaterials.map((material) => {
        const expanded = Boolean(expandedMaterialIds[material.materialId]);
        return (
          <section className="review-library-material" key={material.materialId}>
            <button className="review-library-material-header" type="button" onClick={() => setExpandedMaterialIds((previous) => ({ ...previous, [material.materialId]: !expanded }))} aria-expanded={expanded}>
              <span className="material-type-icon">{isVideoType(material.documentType) ? <FileVideo2 size={17} /> : <FileText size={17} />}</span>
              <span><strong>{material.title}</strong><small>{material.folderName ? `文件夹：${material.folderName} · ` : '未归档 · '}{material.cards.length} 张卡片 · {material.reviewedCardCount} 张已复习</small></span>
              {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
            </button>
            {expanded ? <div className="review-library-material-body">
              {material.summary ? <article className="review-material-summary-card"><header className="review-material-summary-heading"><span className="review-material-summary-icon"><BookOpen size={16} /></span><div><h5>资料总结</h5><span>当前文档的复习脉络</span></div></header><MarkdownText content={material.summary} /></article> : null}
              <div className="review-card-grid review-library-card-grid">{material.cards.map((card) => <article className="review-question-card is-revealed review-library-card" key={card.id}>
                <div className="review-card-meta"><div className="review-card-meta-leading"><span>{card.reviewCount > 0 ? `已复习 ${card.reviewCount} 次` : '尚未复习'}</span>{card.sourceType === 'MANUAL' ? <em className="review-card-source-tag is-manual">手动卡片</em> : null}{card.isUserEdited ? <em className="review-card-source-tag">已编辑</em> : null}</div><div className="review-card-meta-actions"><button className="icon-button tiny" type="button" title="编辑卡片" aria-label={`编辑卡片：${card.question}`} onClick={() => setEditTarget(card)}><Pencil size={14} /></button><button className="icon-button tiny review-ai-action" type="button" title="让 LLM 改写" aria-label={`让 LLM 改写卡片：${card.question}`} onClick={() => setRewriteTarget(card)}><Sparkles size={14} /></button></div></div>
                <MarkdownText content={card.question} className="review-card-question-markdown" />
                <div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={card.answer || '暂无答案'} /></div>
                {card.hint ? <div className="review-hint"><span>提示</span><MarkdownText content={card.hint} /></div> : null}
              </article>)}</div>
            </div> : null}
          </section>
        );
      })}</div> : null}

      <ReviewCardEditDialog target={editTarget} onClose={() => setEditTarget(null)} onSaved={applyUpdatedCard} />
      <ReviewCardRewriteDialog target={rewriteTarget} onClose={() => setRewriteTarget(null)} onSaved={applyUpdatedCard} />
    </div>
  );
}

function isVideoType(value: string) {
  return ['mp4', 'mov', 'avi', 'mkv', 'webm', 'video'].includes((value || '').toLowerCase());
}
