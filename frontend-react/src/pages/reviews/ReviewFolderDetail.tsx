import {
  ArrowLeft,
  ArrowUpRight,
  AlertTriangle,
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
  GripVertical,
  Loader2,
  MessageCirclePlus,
  PenLine,
  Pencil,
  Sparkles,
  Trash2
} from 'lucide-react';
import { Fragment, useEffect, useRef, useState, type DragEvent as ReactDragEvent } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { REVIEW_CONTENT_UPDATED_EVENT, REVIEW_OVERVIEW_UPDATED_EVENT, assignReviewMaterialsToFolder, deleteReviewCard, fetchReviewCard, fetchReviewFolder, gradeReviewCard, updateReviewFolderMaterialOrder, type ReviewCard, type ReviewFolderDetail as ReviewFolderDetailData, type ReviewFolderMaterial, type ReviewMissingKnowledgeTask } from '../../api/reviews';
import { MarkdownText } from '../../components/MarkdownText';
import { buildEvidenceOpenHref } from '../../utils/evidenceLinks';
import '../../styles/ReviewCenter.css';
import { ReviewMissingKnowledgeDialog, type MissingKnowledgeTarget } from './ReviewMissingKnowledgeDialog';
import { ReviewManualCardDialog, type ManualCardTarget } from './ReviewManualCardDialog';
import { ReviewOrderPositionInput } from './ReviewOrderPositionInput';
import { useDragAutoScroll } from './useDragAutoScroll';
import { ReviewCardEditDialog } from './ReviewCardEditDialog';
import { ReviewCardRewriteDialog } from './ReviewCardRewriteDialog';
import { ReviewMaterialRewriteDialog, type MaterialRewriteTarget } from './ReviewMaterialRewriteDialog';

type ReviewRating = 1 | 2 | 3 | 4;
type DropPlacement = 'before' | 'after';
type ReviewDropPreview = { targetId: number; placement: DropPlacement };
const RATING_OPTIONS: Array<{ rating: ReviewRating; label: string; detail: string }> = [
  { rating: 1, label: '忘记', detail: '再次安排' },
  { rating: 2, label: '困难', detail: '短间隔' },
  { rating: 3, label: '记得', detail: '正常间隔' },
  { rating: 4, label: '轻松', detail: '延长间隔' }
];

// 文件夹详情按文档展示全部活动卡片，答案仍由用户主动揭示。
export function ReviewFolderDetail() {
  const { folderId } = useParams();
  const [searchParams] = useSearchParams();
  const { updateDragAutoScroll, stopDragAutoScroll } = useDragAutoScroll();
  const resolvedFolderId = Number(folderId);
  const requestedMaterialId = Number(searchParams.get('materialId'));
  const locatedMaterialId = Number.isInteger(requestedMaterialId) && requestedMaterialId > 0 ? requestedMaterialId : null;
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
  const [deleteTarget, setDeleteTarget] = useState<ReviewCard | null>(null);
  const [deletingCardId, setDeletingCardId] = useState<number | null>(null);
  const [gradeMessage, setGradeMessage] = useState('');
  const [orderMessage, setOrderMessage] = useState('');
  const [draggingMaterialId, setDraggingMaterialId] = useState<number | null>(null);
  const [dragPreview, setDragPreview] = useState<ReviewDropPreview | null>(null);
  const [orderSaving, setOrderSaving] = useState(false);
  const [missingKnowledgeTarget, setMissingKnowledgeTarget] = useState<MissingKnowledgeTarget | null>(null);
  const [missingKnowledgeTasks, setMissingKnowledgeTasks] = useState<Record<number, ReviewMissingKnowledgeTask>>({});
  const [manualCardTarget, setManualCardTarget] = useState<ManualCardTarget | null>(null);
  const [cardEditTarget, setCardEditTarget] = useState<ReviewCard | null>(null);
  const [cardRewriteTarget, setCardRewriteTarget] = useState<ReviewCard | null>(null);
  const [materialRewriteTarget, setMaterialRewriteTarget] = useState<MaterialRewriteTarget | null>(null);
  const [cardActionLoadingId, setCardActionLoadingId] = useState<number | null>(null);
  const materialsRef = useRef<ReviewFolderMaterial[]>([]);
  const reviewedCardIdsRef = useRef<Set<number>>(new Set());
  const dragSourceIdRef = useRef<number | null>(null);
  const dragSnapshotOrderRef = useRef<number[]>([]);
  const dragDropHandledRef = useRef(false);

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
        const visibleMaterials = filterVisibleFolderCards(result.materials, reviewedCardIdsRef.current);
        setError('');
        setDetail({ ...result, materials: visibleMaterials });
        materialsRef.current = visibleMaterials;
        setExpandedMaterials(Object.fromEntries(visibleMaterials.map((material, index) => [
          material.materialId,
          locatedMaterialId === material.materialId || (locatedMaterialId === null && index === 0)
        ])));
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
  }, [locatedMaterialId, resolvedFolderId]);

  useEffect(() => {
    if (!detail || locatedMaterialId === null) return undefined;
    if (!detail.materials.some((material) => material.materialId === locatedMaterialId)) {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      const target = document.getElementById(reviewFolderMaterialId(locatedMaterialId));
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target?.focus({ preventScroll: true });
    }, 120);
    return () => window.clearTimeout(timer);
  }, [detail, locatedMaterialId]);

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

  async function openCardEditor(card: ReviewCard, action: 'EDIT' | 'REWRITE') {
    if (cardActionLoadingId !== null) return;
    setCardActionLoadingId(card.id);
    setError('');
    try {
      const fullCard = revealedCards[card.id]?.answer ? revealedCards[card.id] : await fetchReviewCard(card.id);
      if (action === 'EDIT') setCardEditTarget(fullCard);
      else setCardRewriteTarget(fullCard);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : '卡片内容读取失败');
    } finally {
      setCardActionLoadingId(null);
    }
  }

  function applyUpdatedCard(updated: ReviewCard) {
    const hidden = hideReviewCardAnswer(updated);
    updateFolderMaterials((materials) => materials.map((material) => material.materialId === updated.materialId ? {
      ...material,
      cards: material.cards.map((card) => card.id === updated.id ? hidden : card)
    } : material));
    setRevealedCards((previous) => previous[updated.id] ? { ...previous, [updated.id]: updated } : previous);
    setGradeMessage('卡片内容已更新，原复习进度保持不变');
    window.dispatchEvent(new Event(REVIEW_CONTENT_UPDATED_EVENT));
  }

  // 资料级合并确认后重新读取文件夹，确保卡片数量、到期状态和摘要保持一致。
  async function applyMaterialRewrite(result: { material: { materialId: number; title: string; summary?: string | null }; cards: ReviewCard[] }) {
    setMaterialRewriteTarget(null);
    try {
      const refreshed = await fetchReviewFolder(resolvedFolderId);
      const visibleMaterials = filterVisibleFolderCards(refreshed.materials, reviewedCardIdsRef.current);
      setDetail({ ...refreshed, materials: visibleMaterials });
      materialsRef.current = visibleMaterials;
      setGradeMessage(`“${result.material.title}”已将原卡片合并为 1 张综合卡片`);
      window.dispatchEvent(new Event(REVIEW_CONTENT_UPDATED_EVENT));
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : '资料改写已应用，但文件夹刷新失败');
    }
  }

  async function removeFromFolder(materialId: number, title: string) {
    if (movingMaterialId !== null) return;
    setMovingMaterialId(materialId);
    setError('');
    try {
      await assignReviewMaterialsToFolder([materialId], null);
      const refreshed = await fetchReviewFolder(resolvedFolderId);
      const visibleMaterials = filterVisibleFolderCards(refreshed.materials, reviewedCardIdsRef.current);
      setDetail({ ...refreshed, materials: visibleMaterials });
      materialsRef.current = visibleMaterials;
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
      await gradeReviewCard(card.id, { rating });
      reviewedCardIdsRef.current.add(card.id);
      removeReviewedCardFromView(card);
      setRevealedCards((previous) => omitKey(previous, card.id));
      setHintCardIds((previous) => omitKey(previous, card.id));
      setEvidenceCardIds((previous) => omitKey(previous, card.id));
      setGradeMessage(`${RATING_OPTIONS.find((item) => item.rating === rating)?.label || '结果'}已记录`);
      window.dispatchEvent(new Event(REVIEW_OVERVIEW_UPDATED_EVENT));
      try {
        const refreshed = await fetchReviewFolder(resolvedFolderId);
        const visibleMaterials = filterVisibleFolderCards(refreshed.materials, reviewedCardIdsRef.current);
        setDetail({ ...refreshed, materials: visibleMaterials });
        materialsRef.current = visibleMaterials;
      } catch (refreshError) {
        setError(refreshError instanceof Error ? `复习已记录，但文件夹统计刷新失败：${refreshError.message}` : '复习已记录，但文件夹统计刷新失败');
      }
    } catch (gradeError) {
      setError(gradeError instanceof Error ? gradeError.message : '复习评分失败');
    } finally {
      setGradingId(null);
    }
  }

  // 请求删除文件夹内的单张卡片，实际删除前先展示确认对话框。
  function requestCardDeletion(card: ReviewCard) {
    if (deletingCardId !== null || gradingId !== null || orderSaving || draggingMaterialId !== null) return;
    setError('');
    setDeleteTarget(card);
  }

  // 删除成功后立即移除当前视图中的卡片，再刷新文件夹聚合统计。
  async function confirmCardDeletion() {
    const target = deleteTarget;
    if (!target || deletingCardId !== null) return;
    setDeletingCardId(target.id);
    setError('');
    try {
      await deleteReviewCard(target.id);
      removeDeletedCardFromView(target);
      setDeleteTarget(null);
      setGradeMessage('卡片已删除，后续同步不会恢复同一卡片');
      window.dispatchEvent(new Event(REVIEW_OVERVIEW_UPDATED_EVENT));
      try {
        const refreshed = await fetchReviewFolder(resolvedFolderId);
        const visibleMaterials = filterVisibleFolderCards(refreshed.materials, reviewedCardIdsRef.current);
        setDetail({ ...refreshed, materials: visibleMaterials });
        materialsRef.current = visibleMaterials;
      } catch (refreshError) {
        setError(refreshError instanceof Error ? `卡片已删除，但文件夹统计刷新失败：${refreshError.message}` : '卡片已删除，但文件夹统计刷新失败');
      }
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '卡片删除失败');
    } finally {
      setDeletingCardId(null);
    }
  }

  // 同步删除后的文档卡片数量、文件夹总数和到期数，避免等待网络刷新才反馈结果。
  function removeDeletedCardFromView(card: ReviewCard) {
    reviewedCardIdsRef.current.delete(card.id);
    const visibleMaterials = removeCardFromMaterials(materialsRef.current, card.id);
    materialsRef.current = visibleMaterials;
    setRevealedCards((previous) => omitKey(previous, card.id));
    setHintCardIds((previous) => omitKey(previous, card.id));
    setEvidenceCardIds((previous) => omitKey(previous, card.id));
    setDetail((previous) => previous ? {
      ...previous,
      folder: {
        ...previous.folder,
        cardCount: Math.max(0, previous.folder.cardCount - 1),
        dueCardCount: isDueCard(card) ? Math.max(0, previous.folder.dueCardCount - 1) : previous.folder.dueCardCount
      },
      materials: visibleMaterials
    } : previous);
  }

  // 补漏或手动建卡成功后，以服务端刷新结果为基础，再合并本次真实新增卡片，避免旧查询覆盖新卡。
  async function refreshAfterCardAppend(materialId: number, cards: ReviewCard[], message: string) {
    try {
      const refreshed = await fetchReviewFolder(resolvedFolderId);
      const mergedMaterials = mergeFolderCards(refreshed.materials, materialId, cards);
      const visibleMaterials = filterVisibleFolderCards(mergedMaterials, reviewedCardIdsRef.current);
      setDetail({ ...refreshed, materials: visibleMaterials });
      materialsRef.current = visibleMaterials;
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : '文件夹卡片刷新失败');
      const visibleMaterials = filterVisibleFolderCards(mergeFolderCards(materialsRef.current, materialId, cards), reviewedCardIdsRef.current);
      setDetail((previous) => previous ? { ...previous, materials: visibleMaterials } : previous);
      materialsRef.current = visibleMaterials;
    }
    setGradeMessage(message);
  }

  // 评分成功后立即隐藏本次已完成卡片，文件夹和文档的总卡片数仍保留真实持久化统计。
  function removeReviewedCardFromView(card: ReviewCard) {
    const visibleMaterials = filterVisibleFolderCards(materialsRef.current, reviewedCardIdsRef.current);
    materialsRef.current = visibleMaterials;
    setDetail((previous) => previous ? {
      ...previous,
      folder: isDueCard(card) ? {
        ...previous.folder,
        dueCardCount: Math.max(0, previous.folder.dueCardCount - 1)
      } : previous.folder,
      materials: visibleMaterials
    } : previous);
  }

  // 同步更新文件夹内文档引用，拖拽事件无需等待 React 状态批量提交。
  function updateFolderMaterials(update: ReviewFolderMaterial[] | ((current: ReviewFolderMaterial[]) => ReviewFolderMaterial[])) {
    const current = materialsRef.current;
    const next = typeof update === 'function' ? update(current) : update;
    materialsRef.current = next;
    setDetail((previous) => previous ? { ...previous, materials: next } : previous);
  }

  // 结束文件夹排序交互并清除拖拽提示状态。
  function finishFolderOrderInteraction() {
    stopDragAutoScroll();
    dragSourceIdRef.current = null;
    setDraggingMaterialId(null);
    setDragPreview(null);
    setOrderSaving(false);
  }

  // 拖拽排序失败时恢复本次交互开始前的稳定顺序。
  async function persistFolderOrder(previousOrder: number[], successMessage = '文件夹内文档优先级已保存') {
    const materialIds = materialsRef.current.map((material) => material.materialId);
    if (sameNumberOrder(previousOrder, materialIds)) {
      finishFolderOrderInteraction();
      return;
    }
    setOrderSaving(true);
    setError('');
    try {
      const saved = await updateReviewFolderMaterialOrder(resolvedFolderId, materialIds);
      updateFolderMaterials((current) => orderMaterialsByIds(current, saved.materialIds));
      setOrderMessage(successMessage);
    } catch (orderError) {
      updateFolderMaterials((current) => orderMaterialsByIds(current, previousOrder));
      setError(orderError instanceof Error ? `文件夹顺序保存失败，已恢复原顺序：${orderError.message}` : '文件夹顺序保存失败，已恢复原顺序');
    } finally {
      finishFolderOrderInteraction();
    }
  }

  // 数字排序直接把文档移动到目标下标，并沿用拖拽相同的乐观更新和失败回滚。
  function moveFolderMaterialToIndex(materialId: number, targetIndex: number) {
    if (orderSaving || movingMaterialId !== null || gradingId !== null || materialsRef.current.length < 2) return;
    const currentIndex = materialsRef.current.findIndex((material) => material.materialId === materialId);
    const boundedTarget = Math.max(0, Math.min(materialsRef.current.length - 1, targetIndex));
    if (currentIndex < 0 || currentIndex === boundedTarget) return;
    const previousOrder = materialsRef.current.map((material) => material.materialId);
    const title = materialsRef.current[currentIndex]?.title || '资料';
    setOrderMessage('');
    setError('');
    updateFolderMaterials((current) => moveItemToIndex(current, materialId, boundedTarget, (material) => material.materialId));
    void persistFolderOrder(previousOrder, `已将“${title}”调整到第 ${boundedTarget + 1} 位`);
  }

  // 桌面端从文档手柄开始拖拽，并保存拖拽前顺序用于失败回滚。
  function handleMaterialDragStart(event: ReactDragEvent<HTMLButtonElement>, materialId: number) {
    if (orderSaving || movingMaterialId !== null || gradingId !== null || materialsRef.current.length < 2) {
      event.preventDefault();
      return;
    }
    dragSourceIdRef.current = materialId;
    dragSnapshotOrderRef.current = materialsRef.current.map((material) => material.materialId);
    dragDropHandledRef.current = false;
    setOrderMessage('');
    setError('');
    setDragPreview(null);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(materialId));
    event.dataTransfer.setData('application/x-review-material-id', String(materialId));
    setDraggingMaterialId(materialId);
  }

  // 拖动经过其他文档时只更新虚影位置，真实顺序在松手时才改变。
  function handleMaterialDragEnter(targetMaterialId: number) {
    if (dragSourceIdRef.current == null || dragSourceIdRef.current === targetMaterialId) return;
    setDragPreview((previous) => previous || { targetId: targetMaterialId, placement: 'before' });
  }

  function handleMaterialDragOver(event: ReactDragEvent<HTMLElement>, targetMaterialId: number) {
    if (dragSourceIdRef.current == null || dragSourceIdRef.current === targetMaterialId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const target = event.currentTarget.getBoundingClientRect();
    setDragPreview({
      targetId: targetMaterialId,
      placement: event.clientY < target.top + target.height / 2 ? 'before' : 'after'
    });
  }

  // 文件夹详情整页监听拖拽位置，指针进入上下边缘时持续滚动长文档列表。
  function handleFolderPageDragOver(event: ReactDragEvent<HTMLDivElement>) {
    if (dragSourceIdRef.current == null) return;
    event.preventDefault();
    updateDragAutoScroll(event.clientY);
  }

  // 松手后按虚影位置更新文档顺序，并提交当前文件夹的完整排序。
  function handleMaterialDrop(event: ReactDragEvent<HTMLElement>, targetMaterialId: number) {
    const sourceMaterialId = dragSourceIdRef.current;
    if (sourceMaterialId == null) return;
    event.preventDefault();
    event.stopPropagation();
    dragDropHandledRef.current = true;
    const preview = dragPreview?.targetId === targetMaterialId
      ? dragPreview
      : { targetId: targetMaterialId, placement: 'before' as DropPlacement };
    const previousOrder = dragSnapshotOrderRef.current;
    updateFolderMaterials((current) => moveToDropPosition(current, sourceMaterialId, preview.targetId, preview.placement, (material) => material.materialId));
    dragSourceIdRef.current = null;
    setDraggingMaterialId(null);
    setDragPreview(null);
    void persistFolderOrder(previousOrder);
  }

  // 拖拽取消时恢复原顺序，不向服务端写入取消结果。
  function handleMaterialDragEnd() {
    if (dragDropHandledRef.current) {
      dragDropHandledRef.current = false;
      return;
    }
    if (dragSourceIdRef.current != null) updateFolderMaterials(orderMaterialsByIds(materialsRef.current, dragSnapshotOrderRef.current));
    finishFolderOrderInteraction();
  }

  return (
    <div className="review-center-page review-folder-detail-page" onDragOver={handleFolderPageDragOver}>
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
      {orderMessage ? <div className="review-alert success" role="status"><Check size={17} />{orderMessage}</div> : null}
      {loading ? <div className="review-loading"><Loader2 className="spin" size={22} /><span>正在读取文件夹</span></div> : null}
      {!loading && detail && !detail.materials.length ? <div className="review-folder-detail-empty"><FolderOpen size={28} /><h3>{detail.folder.materialCount > 0 ? '当前没有到期资料' : '这个文件夹还是空的'}</h3><p>{detail.folder.materialCount > 0 ? '已完成的资料将在其中任一卡片下次到期时重新显示。' : '返回复习中心，在“资料归档”中选择文档移入这里。'}</p><Link className="outline-action" to="/reviews"><ArrowLeft size={15} />返回复习中心</Link></div> : null}

      {!loading && detail?.materials.length ? <div className="review-folder-document-list">{detail.materials.map((material, index) => {
        const expanded = Boolean(expandedMaterials[material.materialId]);
        const dragging = draggingMaterialId === material.materialId;
        const showBefore = dragPreview?.targetId === material.materialId && dragPreview.placement === 'before';
        const showAfter = dragPreview?.targetId === material.materialId && dragPreview.placement === 'after';
        return (
          <Fragment key={material.materialId}>
            {showBefore ? <div className="review-order-drop-placeholder" role="status">松开后放到这里</div> : null}
          <section id={reviewFolderMaterialId(material.materialId)} className={`review-folder-document${locatedMaterialId === material.materialId ? ' is-located' : ''}${dragging ? ' is-dragging' : ''}`} tabIndex={-1} onDragEnter={() => handleMaterialDragEnter(material.materialId)} onDragOver={(event) => handleMaterialDragOver(event, material.materialId)} onDrop={(event) => handleMaterialDrop(event, material.materialId)}>
            <div className="review-folder-document-toolbar">
              <button className="review-folder-document-drag-handle" type="button" draggable={!orderSaving && movingMaterialId === null && gradingId === null && detail.materials.length > 1} disabled={orderSaving || movingMaterialId !== null || gradingId !== null || detail.materials.length < 2} title="拖拽调整文件夹内优先级" aria-label={`调整 ${material.title} 的优先级，当前第 ${index + 1} 位`} onDragStart={(event) => handleMaterialDragStart(event, material.materialId)} onDragEnd={handleMaterialDragEnd}><GripVertical size={17} /></button>
              <ReviewOrderPositionInput currentIndex={index} itemCount={detail.materials.length} itemLabel={material.title} disabled={orderSaving || movingMaterialId !== null || gradingId !== null || draggingMaterialId !== null} onMove={(targetIndex) => moveFolderMaterialToIndex(material.materialId, targetIndex)} />
              <button className="review-folder-document-header" type="button" aria-expanded={expanded} onClick={() => setExpandedMaterials((previous) => ({ ...previous, [material.materialId]: !expanded }))}>
                <span className="material-type-icon">{isVideoType(material.documentType) ? <FileVideo2 size={17} /> : <FileText size={17} />}</span>
                <span className="review-folder-document-copy"><strong title={material.title}>{material.title}</strong><small>{formatDocumentType(material.documentType)} · {material.cardCount} 张复习卡片</small></span>
                {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
              </button>
              <div className="review-folder-document-actions">
                {material.cardCount > 0 ? <button className="outline-action small review-ai-action" type="button" onClick={() => setMaterialRewriteTarget({ materialId: material.materialId, title: material.title, summary: material.summary, cardCount: material.cardCount })} disabled={movingMaterialId !== null || draggingMaterialId !== null || orderSaving}><Sparkles size={14} />AI 合并改写</button> : null}
                <button className="outline-action small" type="button" onClick={() => setManualCardTarget({ materialId: material.materialId, title: material.title, cardCount: material.cardCount })} disabled={movingMaterialId !== null || draggingMaterialId !== null || orderSaving}><PenLine size={14} />手动建卡</button>
                <button className="outline-action small" type="button" onClick={() => setMissingKnowledgeTarget({ materialId: material.materialId, title: material.title, cardCount: material.cardCount })} disabled={movingMaterialId !== null || draggingMaterialId !== null || orderSaving}>{missingKnowledgeTasks[material.materialId]?.status === 'QUEUED' || missingKnowledgeTasks[material.materialId]?.status === 'RUNNING' ? <Loader2 className="spin" size={14} /> : <MessageCirclePlus size={14} />}{missingKnowledgeTasks[material.materialId]?.status === 'QUEUED' || missingKnowledgeTasks[material.materialId]?.status === 'RUNNING' ? '查看进度' : '补充遗漏'}</button>
                <button className="outline-action small review-folder-remove" type="button" onClick={() => void removeFromFolder(material.materialId, material.title)} disabled={movingMaterialId !== null || draggingMaterialId !== null || orderSaving}><FolderX size={14} />{movingMaterialId === material.materialId ? '移出中' : '移出文件夹'}</button>
              </div>
            </div>
            {expanded ? (
              <div className="review-folder-document-body">
                <article className="review-material-summary-card" aria-label={`${material.title}资料总结`}>
                  <header className="review-material-summary-heading"><span className="review-material-summary-icon"><BookOpen size={16} /></span><div><h5>资料总结</h5><span>先建立文档脉络，再逐条回忆知识点</span></div></header>
                  <MarkdownText content={material.summary || '暂无资料总结'} />
                </article>
                {material.cards.length ? <div className="review-card-grid review-folder-card-grid">
                  {material.cards.map((card) => {
                    const revealed = revealedCards[card.id];
                    const showHint = Boolean(hintCardIds[card.id]);
                    const showEvidence = Boolean(evidenceCardIds[card.id]);
                    return (
                      <article className={`review-question-card${revealed?.answer ? ' is-revealed' : ''}`} key={card.id}>
                        <div className="review-card-meta"><div className="review-card-meta-leading"><span>知识点 {card.reviewCount > 0 ? `· 已复习 ${card.reviewCount} 次` : '· 首次复习'}</span>{card.sourceType === 'MANUAL' ? <em className="review-card-source-tag is-manual">手动卡片</em> : null}{card.isUserEdited ? <em className="review-card-source-tag">已编辑</em> : null}</div><div className="review-card-meta-actions"><time>{formatDueDate(card.dueAt)}</time><button className="icon-button tiny" type="button" title="编辑卡片" aria-label={`编辑卡片：${card.question}`} onClick={() => void openCardEditor(card, 'EDIT')} disabled={cardActionLoadingId !== null || deletingCardId !== null || gradingId !== null || orderSaving || draggingMaterialId !== null}>{cardActionLoadingId === card.id ? <Loader2 className="spin" size={14} /> : <Pencil size={14} />}</button><button className="icon-button tiny review-ai-action" type="button" title="让 LLM 改写" aria-label={`让 LLM 改写卡片：${card.question}`} onClick={() => void openCardEditor(card, 'REWRITE')} disabled={cardActionLoadingId !== null || deletingCardId !== null || gradingId !== null || orderSaving || draggingMaterialId !== null}><Sparkles size={14} /></button><button className="icon-button tiny danger" type="button" title="删除卡片" aria-label={`删除卡片：${card.question}`} onClick={() => requestCardDeletion(card)} disabled={deletingCardId !== null || gradingId !== null || orderSaving || draggingMaterialId !== null}>{deletingCardId === card.id ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button></div></div>
                        <MarkdownText content={card.question} className="review-card-question-markdown" />
                        {!revealed?.answer ? <div className="review-card-collapsed-actions"><button className="text-action" type="button" onClick={() => void revealCard(card)} disabled={revealLoadingId === card.id}>{revealLoadingId === card.id ? <Loader2 className="spin" size={15} /> : <Eye size={15} />}{revealLoadingId === card.id ? '读取中' : '查看答案'}</button><button className="icon-text-action" type="button" onClick={() => setHintCardIds((previous) => ({ ...previous, [card.id]: !previous[card.id] }))}><EyeOff size={15} />{showHint ? '收起提示' : '看提示'}</button></div> : <><div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={revealed.answer} /></div><div className="review-reveal-actions">{card.sourceType === 'MANUAL' ? <span className="review-card-no-evidence">手动卡片，无 RAG 原文</span> : <button className="outline-action small" type="button" onClick={() => setEvidenceCardIds((previous) => ({ ...previous, [card.id]: !previous[card.id] }))} disabled={!revealed.evidenceRefs?.length}><ArrowUpRight size={15} />{showEvidence ? '收起 RAG 原文' : '查看 RAG 原文'}</button>}<button className="icon-text-action" type="button" onClick={() => setRevealedCards((previous) => omitKey(previous, card.id))}><EyeOff size={15} />收起答案</button></div><div className="review-rating-block"><span>回忆结果</span><div className="review-rating-options">{RATING_OPTIONS.map((option) => <button key={option.rating} type="button" className={`rating-button rating-${option.rating}`} onClick={() => void gradeCard(card, option.rating)} disabled={gradingId !== null}>{gradingId === card.id ? <Loader2 className="spin" size={15} /> : <Check size={15} />}<span><strong>{option.label}</strong><small>{option.detail}</small></span></button>)}</div></div></>}
                        {showHint && !revealed?.answer ? <div className="review-hint"><span>提示</span><MarkdownText content={card.hint || '回忆该问题对应的定义、机制或关键步骤'} /></div> : null}
                        {showEvidence && revealed?.evidenceRefs?.length ? <div className="review-folder-evidence-list">{revealed.evidenceRefs.map((evidence) => { const href = buildEvidenceOpenHref(evidence); return <article key={evidence.evidenceId}><strong>{evidence.sectionTitle || evidence.sectionName || '原文片段'}</strong><MarkdownText content={evidence.snippet || '暂无片段'} />{href ? <a className="source-jump-link" href={href} target="_blank" rel="noreferrer"><ArrowUpRight size={14} />{isVideoType(evidence.documentType) ? '从此处播放' : '定位原文'}</a> : null}</article>; })}</div> : null}
                      </article>
                    );
                  })}
                </div> : <div className="review-folder-card-complete" role="status"><Check size={18} /><span><strong>当前没有到期卡片</strong><small>已复习卡片将在下次到期时重新显示</small></span></div>}
              </div>
            ) : null}
          </section>
            {showAfter ? <div className="review-order-drop-placeholder" role="status">松开后放到这里</div> : null}
          </Fragment>
        );
      })}</div> : null}
      <ReviewMissingKnowledgeDialog target={missingKnowledgeTarget} onClose={() => setMissingKnowledgeTarget(null)} onTaskChanged={(task) => { if (!task) return; setMissingKnowledgeTasks((previous) => ({ ...previous, [task.materialId]: task })); }} onCardsAdded={async (result) => { await refreshAfterCardAppend(result.materialId, result.cards, `已追加 ${result.addedCount} 张遗漏知识点卡片`); }} />
      <ReviewManualCardDialog target={manualCardTarget} onClose={() => setManualCardTarget(null)} onCreated={async (card) => { await refreshAfterCardAppend(card.materialId, [card], '已创建 1 张手动复习卡片'); }} />
      <ReviewCardEditDialog target={cardEditTarget} onClose={() => setCardEditTarget(null)} onSaved={applyUpdatedCard} />
      <ReviewCardRewriteDialog target={cardRewriteTarget} onClose={() => setCardRewriteTarget(null)} onSaved={applyUpdatedCard} />
      <ReviewMaterialRewriteDialog target={materialRewriteTarget} onClose={() => setMaterialRewriteTarget(null)} onApplied={applyMaterialRewrite} />
      <FolderCardDeletionDialog target={deleteTarget} deleting={deletingCardId !== null} onConfirm={() => void confirmCardDeletion()} onClose={() => { if (deletingCardId === null) setDeleteTarget(null); }} />
    </div>
  );
}

function omitKey<T>(value: Record<number, T>, key: number): Record<number, T> {
  const next = { ...value };
  delete next[key];
  return next;
}

// 从文件夹资料列表中移除指定卡片；资料已无可见卡片时同步隐藏整份资料。
function removeCardFromMaterials(materials: ReviewFolderMaterial[], cardId: number): ReviewFolderMaterial[] {
  return materials
    .map((material) => {
      if (!material.cards.some((card) => card.id === cardId)) return material;
      return {
        ...material,
        cardCount: Math.max(0, material.cardCount - 1),
        cards: material.cards.filter((card) => card.id !== cardId)
      };
    })
    .filter((material) => material.cards.length > 0);
}

// 文件夹只展示仍有到期卡片的资料，并过滤本次会话内刚完成但尚未刷新到期时间的卡片。
function filterVisibleFolderCards(materials: ReviewFolderMaterial[], reviewedCardIds: ReadonlySet<number>): ReviewFolderMaterial[] {
  const now = Date.now();
  return materials
    .map((material) => ({
      ...material,
      cards: material.cards.filter((card) => !reviewedCardIds.has(card.id) && shouldShowFolderCard(card, now))
    }))
    .filter((material) => material.cards.length > 0);
}

// 缺少或无法解析到期时间的卡片保留展示，只有明确排到未来的卡片才隐藏。
function shouldShowFolderCard(card: ReviewCard, now: number): boolean {
  if (!card.dueAt) return true;
  const dueAt = new Date(card.dueAt).getTime();
  return !Number.isFinite(dueAt) || dueAt <= now;
}

// 只有原本已经到期的卡片完成后，才即时递减文件夹到期统计。
function isDueCard(card: ReviewCard): boolean {
  if (!card.dueAt) return false;
  const dueAt = new Date(card.dueAt).getTime();
  return Number.isFinite(dueAt) && dueAt <= Date.now();
}

// 文件夹详情专用的单卡片删除确认框，沿用复习中心的危险操作视觉和键盘关闭行为。
function FolderCardDeletionDialog({ target, deleting, onConfirm, onClose }: { target: ReviewCard | null; deleting: boolean; onConfirm: () => void; onClose: () => void }) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!target) return undefined;
    cancelButtonRef.current?.focus();
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !deleting) onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [deleting, onClose, target]);

  if (!target) return null;
  return (
    <div className="review-delete-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !deleting) onClose(); }}>
      <section className="review-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="folder-delete-card-title" aria-describedby="folder-delete-card-description" aria-busy={deleting}>
        <div className="review-delete-icon"><AlertTriangle size={20} /></div>
        <div className="review-delete-copy">
          <h3 id="folder-delete-card-title">删除这张复习卡片？</h3>
          <strong>{target.question}</strong>
          <p id="folder-delete-card-description">该卡片将停止显示，后续同步或重新生成也不会恢复同一卡片；原始资料仍会保留。</p>
        </div>
        <div className="review-delete-actions">
          <button ref={cancelButtonRef} className="outline-action" type="button" onClick={onClose} disabled={deleting}>取消</button>
          <button className="danger-action" type="button" onClick={onConfirm} disabled={deleting}>{deleting ? <Loader2 className="spin" size={16} /> : <Trash2 size={16} />}{deleting ? '处理中' : '删除卡片'}</button>
        </div>
      </section>
    </div>
  );
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

function reviewFolderMaterialId(materialId: number): string {
  return `review-folder-material-${materialId}`;
}

// 按拖拽虚影的前后位置移动一项，保留其他文档的相对顺序。
function moveToDropPosition<T>(items: T[], sourceId: number, targetId: number, placement: DropPlacement, getId: (item: T) => number): T[] {
  const sourceIndex = items.findIndex((item) => getId(item) === sourceId);
  const targetIndex = items.findIndex((item) => getId(item) === targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceId === targetId) return items;
  const next = [...items];
  const [moved] = next.splice(sourceIndex, 1);
  const targetIndexAfterRemoval = next.findIndex((item) => getId(item) === targetId);
  const insertIndex = targetIndexAfterRemoval + (placement === 'after' ? 1 : 0);
  next.splice(Math.max(0, Math.min(next.length, insertIndex)), 0, moved);
  return next;
}

// 按零基下标移动指定资料，供数字排序入口复用。
function moveItemToIndex<T>(items: T[], sourceId: number, targetIndex: number, getId: (item: T) => number): T[] {
  const sourceIndex = items.findIndex((item) => getId(item) === sourceId);
  if (sourceIndex < 0 || sourceIndex === targetIndex) return items;
  const next = [...items];
  const [moved] = next.splice(sourceIndex, 1);
  next.splice(Math.max(0, Math.min(next.length, targetIndex)), 0, moved);
  return next;
}

// 按服务端返回的稳定顺序重排文档，未知文档保持在末尾。
function orderMaterialsByIds(materials: ReviewFolderMaterial[], materialIds: number[]): ReviewFolderMaterial[] {
  const rank = new Map(materialIds.map((materialId, index) => [materialId, index]));
  return materials
    .map((material, index) => ({ material, index }))
    .sort((left, right) => (rank.get(left.material.materialId) ?? Number.MAX_SAFE_INTEGER) - (rank.get(right.material.materialId) ?? Number.MAX_SAFE_INTEGER) || left.index - right.index)
    .map(({ material }) => material);
}

function sameNumberOrder(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

// 以卡片 ID 去重合并本次追加结果，并保持复习详情页不提前展示答案。
function mergeFolderCards(materials: ReviewFolderMaterial[], materialId: number, incomingCards: ReviewCard[]): ReviewFolderMaterial[] {
  if (!incomingCards.length) return materials;
  return materials.map((material) => {
    if (material.materialId !== materialId) return material;
    const knownIds = new Set(material.cards.map((card) => card.id));
    const appended = incomingCards
      .filter((card) => !knownIds.has(card.id))
      .map(hideReviewCardAnswer);
    const cards = [...material.cards, ...appended];
    return {
      ...material,
      cards,
      cardCount: Math.max(material.cardCount, cards.length)
    };
  });
}

// 补漏接口会返回完整答案供结果确认，详情列表仍遵守主动揭示答案的交互规则。
function hideReviewCardAnswer(card: ReviewCard): ReviewCard {
  return { ...card, answer: null, evidenceRefs: [] };
}
