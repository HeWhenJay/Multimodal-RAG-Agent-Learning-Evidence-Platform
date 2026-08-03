import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  FileText,
  FileVideo2,
  FolderOpen,
  FolderX,
  Loader2
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { assignReviewMaterialsToFolder, fetchReviewCard, fetchReviewFolder, gradeReviewCard, type ReviewCard, type ReviewFolderDetail as ReviewFolderDetailData } from '../../api/reviews';
import { MarkdownText } from '../../components/MarkdownText';
import { buildEvidenceOpenHref } from '../../utils/evidenceLinks';
import '../../styles/ReviewCenter.css';

type ReviewRating = 1 | 2 | 3 | 4;
const RATING_OPTIONS: Array<{ rating: ReviewRating; label: string; detail: string }> = [
  { rating: 1, label: '忘记', detail: '再次安排' },
  { rating: 2, label: '困难', detail: '短间隔' },
  { rating: 3, label: '记得', detail: '正常间隔' },
  { rating: 4, label: '轻松', detail: '延长间隔' }
];

// 文件夹详情按文档展示全部活动卡片，答案仍由用户主动揭示。
export function ReviewFolderDetail() {
  const { folderId } = useParams();
  const resolvedFolderId = Number(folderId);
  const [detail, setDetail] = useState<ReviewFolderDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedMaterials, setExpandedMaterials] = useState<Record<number, boolean>>({});
  const [revealedCards, setRevealedCards] = useState<Record<number, ReviewCard>>({});
  const [revealLoadingId, setRevealLoadingId] = useState<number | null>(null);
  const [hintCardIds, setHintCardIds] = useState<Record<number, boolean>>({});
  const [evidenceCardIds, setEvidenceCardIds] = useState<Record<number, boolean>>({});
  const [movingMaterialId, setMovingMaterialId] = useState<number | null>(null);
  const [gradingId, setGradingId] = useState<number | null>(null);
  const [gradeMessage, setGradeMessage] = useState('');

  useEffect(() => {
    let active = true;
    if (!Number.isInteger(resolvedFolderId) || resolvedFolderId <= 0) {
      setError('复习文件夹不存在');
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    void fetchReviewFolder(resolvedFolderId)
      .then((result) => {
        if (!active) return;
        setDetail(result);
        setExpandedMaterials(Object.fromEntries(result.materials.map((material, index) => [material.materialId, index === 0])));
      })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : '复习文件夹读取失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [resolvedFolderId]);

  async function revealCard(card: ReviewCard) {
    if (revealedCards[card.id]?.answer || revealLoadingId === card.id) return;
    setRevealLoadingId(card.id);
    setError('');
    try {
      const revealed = await fetchReviewCard(card.id);
      setRevealedCards((previous) => ({ ...previous, [card.id]: revealed }));
    } catch (revealError) {
      setError(revealError instanceof Error ? revealError.message : '答案读取失败');
    } finally {
      setRevealLoadingId(null);
    }
  }

  async function removeFromFolder(materialId: number, title: string) {
    if (movingMaterialId !== null) return;
    setMovingMaterialId(materialId);
    setError('');
    try {
      await assignReviewMaterialsToFolder([materialId], null);
      const refreshed = await fetchReviewFolder(resolvedFolderId);
      setDetail(refreshed);
      setExpandedMaterials((previous) => {
        const next = { ...previous };
        delete next[materialId];
        return next;
      });
    } catch (moveError) {
      setError(moveError instanceof Error ? moveError.message : `资料“${title}”移出文件夹失败`);
    } finally {
      setMovingMaterialId(null);
    }
  }

  async function gradeCard(card: ReviewCard, rating: ReviewRating) {
    if (gradingId !== null) return;
    setGradingId(card.id);
    setError('');
    setGradeMessage('');
    try {
      const result = await gradeReviewCard(card.id, { rating });
      setRevealedCards((previous) => ({ ...previous, [card.id]: result.card }));
      const refreshed = await fetchReviewFolder(resolvedFolderId);
      setDetail(refreshed);
      setGradeMessage(`${RATING_OPTIONS.find((item) => item.rating === rating)?.label || '结果'}已记录`);
    } catch (gradeError) {
      setError(gradeError instanceof Error ? gradeError.message : '复习评分失败');
    } finally {
      setGradingId(null);
    }
  }

  return (
    <div className="review-center-page review-folder-detail-page">
      <header className="review-page-header review-folder-detail-header">
        <div>
          <Link className="review-folder-back" to="/reviews"><ArrowLeft size={15} />返回复习中心</Link>
          <div className="page-eyebrow"><FolderOpen size={14} />复习文件夹</div>
          <h2>{detail?.folder.name || '文件夹详情'}</h2>
          <p>{detail ? `${detail.folder.materialCount} 份文档 · ${detail.folder.cardCount} 张卡片 · ${detail.folder.dueCardCount} 张到期` : '按文档查看复习卡片'}</p>
        </div>
      </header>

      {error ? <div className="review-alert danger">{error}</div> : null}
      {gradeMessage ? <div className="review-alert success"><Check size={17} />{gradeMessage}</div> : null}
      {loading ? <div className="review-loading"><Loader2 className="spin" size={22} /><span>正在读取文件夹</span></div> : null}
      {!loading && detail && !detail.materials.length ? <div className="review-folder-detail-empty"><FolderOpen size={28} /><h3>这个文件夹还是空的</h3><p>返回复习中心，在“资料归档”中选择文档移入这里。</p><Link className="outline-action" to="/reviews"><ArrowLeft size={15} />返回归档资料</Link></div> : null}

      {!loading && detail?.materials.length ? <div className="review-folder-document-list">{detail.materials.map((material, index) => {
        const expanded = Boolean(expandedMaterials[material.materialId]);
        return (
          <section className="review-folder-document" key={material.materialId}>
            <div className="review-folder-document-toolbar">
              <button className="review-folder-document-header" type="button" aria-expanded={expanded} onClick={() => setExpandedMaterials((previous) => ({ ...previous, [material.materialId]: !expanded }))}>
                <span className="review-folder-document-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="material-type-icon">{isVideoType(material.documentType) ? <FileVideo2 size={17} /> : <FileText size={17} />}</span>
                <span className="review-folder-document-copy"><strong>{material.title}</strong><small>{formatDocumentType(material.documentType)} · {material.cardCount} 张复习卡片</small></span>
                {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
              </button>
              <button className="outline-action small review-folder-remove" type="button" onClick={() => void removeFromFolder(material.materialId, material.title)} disabled={movingMaterialId !== null}><FolderX size={14} />{movingMaterialId === material.materialId ? '移出中' : '移出文件夹'}</button>
            </div>
            {expanded ? (
              <div className="review-folder-document-body">
                <article className="review-material-summary-card" aria-label={`${material.title}资料总结`}>
                  <header className="review-material-summary-heading"><span className="review-material-summary-icon"><BookOpen size={16} /></span><div><h5>资料总结</h5><span>先建立文档脉络，再逐条回忆知识点</span></div></header>
                  <MarkdownText content={material.summary || '暂无资料总结'} />
                </article>
                <div className="review-card-grid review-folder-card-grid">
                  {material.cards.map((card) => {
                    const revealed = revealedCards[card.id];
                    const showHint = Boolean(hintCardIds[card.id]);
                    const showEvidence = Boolean(evidenceCardIds[card.id]);
                    return (
                      <article className={`review-question-card${revealed?.answer ? ' is-revealed' : ''}`} key={card.id}>
                        <div className="review-card-meta"><span>知识点 {card.reviewCount > 0 ? `· 已复习 ${card.reviewCount} 次` : '· 首次复习'}</span><time>{formatDueDate(card.dueAt)}</time></div>
                        <h5>{card.question}</h5>
                        {!revealed?.answer ? <div className="review-card-collapsed-actions"><button className="text-action" type="button" onClick={() => void revealCard(card)} disabled={revealLoadingId === card.id}>{revealLoadingId === card.id ? <Loader2 className="spin" size={15} /> : <Eye size={15} />}{revealLoadingId === card.id ? '读取中' : '查看答案'}</button><button className="icon-text-action" type="button" onClick={() => setHintCardIds((previous) => ({ ...previous, [card.id]: !previous[card.id] }))}><EyeOff size={15} />{showHint ? '收起提示' : '看提示'}</button></div> : <><div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={revealed.answer} /></div><div className="review-reveal-actions"><button className="outline-action small" type="button" onClick={() => setEvidenceCardIds((previous) => ({ ...previous, [card.id]: !previous[card.id] }))} disabled={!revealed.evidenceRefs?.length}><ArrowUpRight size={15} />{showEvidence ? '收起 RAG 原文' : '查看 RAG 原文'}</button><button className="icon-text-action" type="button" onClick={() => setRevealedCards((previous) => omitKey(previous, card.id))}><EyeOff size={15} />收起答案</button></div><div className="review-rating-block"><span>回忆结果</span><div className="review-rating-options">{RATING_OPTIONS.map((option) => <button key={option.rating} type="button" className={`rating-button rating-${option.rating}`} onClick={() => void gradeCard(card, option.rating)} disabled={gradingId !== null}>{gradingId === card.id ? <Loader2 className="spin" size={15} /> : <Check size={15} />}<span><strong>{option.label}</strong><small>{option.detail}</small></span></button>)}</div></div></>}
                        {showHint && !revealed?.answer ? <div className="review-hint"><span>提示</span>{card.hint || '回忆该问题对应的定义、机制或关键步骤'}</div> : null}
                        {showEvidence && revealed?.evidenceRefs?.length ? <div className="review-folder-evidence-list">{revealed.evidenceRefs.map((evidence) => { const href = buildEvidenceOpenHref(evidence); return <article key={evidence.evidenceId}><strong>{evidence.sectionTitle || evidence.sectionName || '原文片段'}</strong><MarkdownText content={evidence.snippet || '暂无片段'} />{href ? <a className="source-jump-link" href={href} target="_blank" rel="noreferrer"><ArrowUpRight size={14} />{isVideoType(evidence.documentType) ? '从此处播放' : '定位原文'}</a> : null}</article>; })}</div> : null}
                      </article>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </section>
        );
      })}</div> : null}
    </div>
  );
}

function omitKey<T>(value: Record<number, T>, key: number): Record<number, T> {
  const next = { ...value };
  delete next[key];
  return next;
}

function formatDueDate(value?: string | null) {
  if (!value) return '待复习';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '待复习';
  const due = date.getTime() <= Date.now();
  return `${due ? '已到期 · ' : '下次 · '}${date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}`;
}

function formatDocumentType(value?: string | null) {
  const normalized = (value || '').toLowerCase();
  if (isVideoType(normalized)) return '视频';
  if (normalized === 'pdf') return 'PDF';
  if (normalized === 'markdown' || normalized === 'md') return '笔记';
  if (normalized === 'doc' || normalized === 'docx') return '文档';
  return value ? value.toUpperCase() : '资料';
}

function isVideoType(value?: string | null) {
  return /^(mp4|mov|m4v|webm|mkv|avi)$/i.test(value || '') || Boolean(value && /video/i.test(value));
}
