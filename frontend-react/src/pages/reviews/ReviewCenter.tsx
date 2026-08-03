import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  Bell,
  BellRing,
  BookOpen,
  Check,
  CircleAlert,
  Clock3,
  Eye,
  EyeOff,
  FileText,
  FileVideo2,
  Folder,
  FolderOpen,
  FolderPlus,
  FolderX,
  GripVertical,
  LocateFixed,
  Loader2,
  MessageCirclePlus,
  MoveRight,
  Pencil,
  RefreshCw,
  Save,
  Settings2,
  Target,
  Trash2,
  X
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type Dispatch, type DragEvent as ReactDragEvent, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type MutableRefObject, type SetStateAction } from 'react';
import { useNavigate } from 'react-router-dom';
import { MarkdownText } from '../../components/MarkdownText';
import {
  REVIEW_OVERVIEW_UPDATED_EVENT,
  REVIEW_CONTENT_UPDATED_EVENT,
  assignReviewMaterialsToFolder,
  createReviewFolder,
  deleteReviewCard,
  deleteReviewCards,
  deleteReviewMaterial,
  deleteReviewMaterials,
  deleteReviewFolder,
  fetchDueReviewGroups,
  fetchReviewCard,
  fetchReviewFolders,
  fetchReviewMaterials,
  fetchReviewOverview,
  generateReviewMaterial,
  gradeReviewCard,
  renameReviewFolder,
  syncReviewMaterials,
  updateDueReviewGroupOrder,
  updateReviewSettings,
  type ReviewCard,
  type ReviewCardGroup,
  type ReviewFolder,
  type ReviewGradeResult,
  type ReviewGenerationProgressEvent,
  type ReviewMaterial,
  type ReviewOverview,
  type ReviewSettings,
  type ReviewSyncResult
} from '../../api/reviews';
import { buildEvidenceOpenHref } from '../../utils/evidenceLinks';
import type { RagEvidence } from '../../api/types';
import { MATERIAL_UPLOADED_EVENT } from '../../hooks/useMaterialUpload';
import '../../styles/ReviewCenter.css';
import { ReviewMissingKnowledgeDialog, type MissingKnowledgeTarget } from './ReviewMissingKnowledgeDialog';

type ReviewRating = 1 | 2 | 3 | 4;
type ReviewDeleteTarget =
  | { scope: 'CARD'; card: ReviewCard }
  | { scope: 'MATERIAL'; materialId: number; title: string }
  | { scope: 'CARD_BATCH'; cardIds: number[] }
  | { scope: 'MATERIAL_BATCH'; materialIds: number[]; titles: string[] };
type ReviewFolderEditorTarget = { mode: 'CREATE' } | { mode: 'RENAME'; folder: ReviewFolder };

const DEFAULT_SETTINGS: ReviewSettings = {
  enabled: true,
  desiredRetention: 0.9,
  dailyLimit: 20,
  reminderTime: '09:00',
  timezone: 'Asia/Shanghai'
};

const RATING_OPTIONS: Array<{ rating: ReviewRating; label: string; detail: string }> = [
  { rating: 1, label: '忘记', detail: '再次安排' },
  { rating: 2, label: '困难', detail: '短间隔' },
  { rating: 3, label: '记得', detail: '正常间隔' },
  { rating: 4, label: '轻松', detail: '延长间隔' }
];

const TIMEZONE_OPTIONS = ['Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Taipei', 'Asia/Singapore', 'UTC'];
const MAX_AUTOMATIC_REVIEW_SYNC_COUNT = 100;

// 复习中心按上传资料展示每日到期 group，每张小卡片独立揭示、定位和评分。
export function ReviewCenter() {
  const navigate = useNavigate();
  const [overview, setOverview] = useState<ReviewOverview | null>(null);
  const [groups, setGroups] = useState<ReviewCardGroup[]>([]);
  const [materials, setMaterials] = useState<ReviewMaterial[]>([]);
  const [folders, setFolders] = useState<ReviewFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const [error, setError] = useState('');
  const [revealedCards, setRevealedCards] = useState<Record<number, ReviewCard>>({});
  const [revealLoadingId, setRevealLoadingId] = useState<number | null>(null);
  const [hintCardIds, setHintCardIds] = useState<Record<number, boolean>>({});
  const [gradingId, setGradingId] = useState<number | null>(null);
  const [gradeMessage, setGradeMessage] = useState('');
  const [gradeError, setGradeError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<ReviewDeleteTarget | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [deleteMessage, setDeleteMessage] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const [selectedCardIds, setSelectedCardIds] = useState<Record<number, boolean>>({});
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<Record<number, boolean>>({});
  const [originalCard, setOriginalCard] = useState<ReviewCard | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<ReviewSettings>(DEFAULT_SETTINGS);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState('');
  const [notificationPermission, setNotificationPermission] = useState<NotificationPermission>(readNotificationPermission());
  const [busyMaterialId, setBusyMaterialId] = useState<number | null>(null);
  const [draggingMaterialId, setDraggingMaterialId] = useState<number | null>(null);
  const [orderSaving, setOrderSaving] = useState(false);
  const [orderMessage, setOrderMessage] = useState('');
  const [orderError, setOrderError] = useState('');
  const [folderEditorTarget, setFolderEditorTarget] = useState<ReviewFolderEditorTarget | null>(null);
  const [folderEditorName, setFolderEditorName] = useState('');
  const [folderDeleteTarget, setFolderDeleteTarget] = useState<ReviewFolder | null>(null);
  const [folderTargetValue, setFolderTargetValue] = useState('unfiled');
  const [folderBusy, setFolderBusy] = useState(false);
  const [folderMessage, setFolderMessage] = useState('');
  const [folderError, setFolderError] = useState('');
  const [folderDropTargetId, setFolderDropTargetId] = useState<number | null>(null);
  const [reviewFeedbackTarget, setReviewFeedbackTarget] = useState<ReviewMaterial | null>(null);
  const [reviewFeedbackText, setReviewFeedbackText] = useState('');
  const [reviewFeedbackBusy, setReviewFeedbackBusy] = useState(false);
  const [missingKnowledgeTarget, setMissingKnowledgeTarget] = useState<MissingKnowledgeTarget | null>(null);
  const [locatedMaterialId, setLocatedMaterialId] = useState<number | null>(null);
  const locateTimerRef = useRef<number | null>(null);
  const settingsDirtyRef = useRef(false);
  const syncPromiseRef = useRef<Promise<ReviewSyncResult> | null>(null);
  const reviewStartedAtRef = useRef<Record<number, number>>({});
  const groupsRef = useRef<ReviewCardGroup[]>([]);
  const groupReadVersionRef = useRef(0);
  const orderInteractionRef = useRef(false);
  const dragSourceIdRef = useRef<number | null>(null);
  const dragSnapshotOrderRef = useRef<number[]>([]);
  const dragDropHandledRef = useRef(false);

  // 服务端的 actionableDueCount 表示当前还能进入队列的资料份数，而非卡片数。
  const dueMaterialCount = overview?.actionableDueCount ?? groups.length;
  const dailyLimit = overview?.settings.dailyLimit || settingsDraft.dailyLimit || 20;
  const reviewProgress = Math.min(100, Math.round(((overview?.todayReviewedCount || 0) / Math.max(1, dailyLimit)) * 100));
  const selectedCardIdList = selectedIds(selectedCardIds);
  const selectedMaterialIdList = selectedIds(selectedMaterialIds);
  const selectableMaterialIdList = materials
    .map(resolveMaterialId)
    .filter((materialId): materialId is number => materialId !== null);
  const allMaterialsSelected = selectableMaterialIdList.length > 0
    && selectableMaterialIdList.every((materialId) => Boolean(selectedMaterialIds[materialId]));
  const pendingMaterialIdList = materials
    .filter((material) => (material.status || '').toUpperCase() === 'PENDING')
    .map(resolveMaterialId)
    .filter((materialId): materialId is number => materialId !== null);
  const generationPollingActive = syncing || busyMaterialId !== null || reviewFeedbackBusy;

  // 同步维护可立即读取的 group 引用，拖拽事件无需等待 React 批量提交状态。
  const updateGroups = useCallback((update: SetStateAction<ReviewCardGroup[]>) => {
    const next = typeof update === 'function'
      ? (update as (value: ReviewCardGroup[]) => ReviewCardGroup[])(groupsRef.current)
      : update;
    groupsRef.current = next;
    setGroups(next);
  }, []);

  // 同一页面始终只启动一条串行同步链，避免 StrictMode、上传事件和手动检查重复调用模型。
  const startReviewSyncQueue = useCallback((onProgress?: ReviewSyncProgressHandler) => {
    if (syncPromiseRef.current) return syncPromiseRef.current;
    const promise = drainPendingReviewMaterials(onProgress);
    syncPromiseRef.current = promise;
    void promise.then(
      () => {
        if (syncPromiseRef.current === promise) syncPromiseRef.current = null;
      },
      () => {
        if (syncPromiseRef.current === promise) syncPromiseRef.current = null;
      }
    );
    return promise;
  }, []);

  // 同时读取概览、分组队列和资料状态，单个区域失败时保留其余数据。
  const loadData = useCallback(async () => {
    const groupReadVersion = ++groupReadVersionRef.current;
    const groupsMayApply = !orderInteractionRef.current;
    const results = await Promise.allSettled([
      fetchReviewOverview(),
      fetchDueReviewGroups(100),
      fetchReviewMaterials(),
      fetchReviewFolders()
    ]);
    const failures: string[] = [];
    const overviewResult = results[0];
    const groupsResult = results[1];
    const materialsResult = results[2];
    const foldersResult = results[3];
    if (overviewResult.status === 'fulfilled') {
      setOverview(overviewResult.value);
      if (!settingsDirtyRef.current) setSettingsDraft(normalizeSettings(overviewResult.value.settings));
      publishOverviewEvent();
    } else {
      failures.push('复习概览');
    }
    if (groupsResult.status === 'fulfilled' && groupsMayApply && groupReadVersion === groupReadVersionRef.current && !orderInteractionRef.current) {
      updateGroups(groupsResult.value.groups);
    } else {
      if (groupsResult.status === 'rejected') failures.push('复习卡片');
    }
    if (materialsResult.status === 'fulfilled') {
      setMaterials(materialsResult.value);
    } else {
      failures.push('资料状态');
    }
    if (foldersResult.status === 'fulfilled') {
      setFolders(foldersResult.value);
    } else {
      failures.push('复习文件夹');
    }
    if (failures.length === 4) throw new Error('复习数据加载失败，请稍后重试');
    setError(failures.length ? `${failures.join('、')}暂时不可用` : '');
  }, [updateGroups]);

  // 页面首次打开时串行排空等待生成的资料；后端单次仍只处理一份，避免请求内并发调用模型。
  useEffect(() => {
    let active = true;
    const initialize = async () => {
      setLoading(true);
      try {
        await loadData();
      } catch (loadError) {
        if (active) setError(loadError instanceof Error ? loadError.message : '复习数据加载失败');
      } finally {
        if (active) setLoading(false);
      }
      setSyncing(true);
      try {
        const result = await startReviewSyncQueue(async (progress) => {
          if (!active) return;
          setSyncMessage(formatSyncMessage(progress));
          await loadData();
        });
        if (active) {
          setSyncMessage(formatSyncMessage(result));
          await loadData();
        }
      } catch (syncError) {
        if (active) setSyncMessage('资料同步暂未完成，已有复习卡片仍可使用');
      } finally {
        if (active) setSyncing(false);
      }
    };
    void initialize();
    return () => {
      active = false;
    };
  }, [loadData, startReviewSyncQueue]);

  // 上传资料完成 RAG 入库并生成卡片后，立即刷新当前复习中心，不等待定时轮询。
  useEffect(() => {
    const refreshGeneratedMaterial = () => {
      void loadData().catch(() => undefined);
    };
    window.addEventListener(REVIEW_CONTENT_UPDATED_EVENT, refreshGeneratedMaterial);
    return () => window.removeEventListener(REVIEW_CONTENT_UPDATED_EVENT, refreshGeneratedMaterial);
  }, [loadData]);

  // 生成请求仍在执行时单独轮询资料阶段，及时展示 LangGraph 节点、轮次和质量修复反馈。
  useEffect(() => {
    if (!generationPollingActive) return undefined;
    let active = true;
    let reading = false;
    const refreshGenerationProgress = async () => {
      if (reading) return;
      reading = true;
      try {
        const result = await fetchReviewMaterials();
        if (active) setMaterials(result);
      } catch {
        // 主同步请求负责最终错误提示，短轮询失败时保留最后一份阶段快照。
      } finally {
        reading = false;
      }
    };
    void refreshGenerationProgress();
    const timer = window.setInterval(refreshGenerationProgress, 1200);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [generationPollingActive]);

  useEffect(() => () => {
    if (locateTimerRef.current !== null) window.clearTimeout(locateTimerRef.current);
  }, []);

  // 到期时间和评分日志由服务端维护，页面定时或重新聚焦时刷新概览与分组队列。
  useEffect(() => {
    const refresh = () => {
      const groupReadVersion = ++groupReadVersionRef.current;
      const groupsMayApply = !orderInteractionRef.current;
      void Promise.allSettled([fetchReviewOverview(), fetchDueReviewGroups(100)]).then(([overviewResult, groupsResult]) => {
        if (overviewResult.status === 'fulfilled') {
          setOverview(overviewResult.value);
          if (!settingsDirtyRef.current) setSettingsDraft(normalizeSettings(overviewResult.value.settings));
        }
        if (groupsResult.status === 'fulfilled' && groupsMayApply && groupReadVersion === groupReadVersionRef.current && !orderInteractionRef.current) {
          updateGroups(groupsResult.value.groups);
        }
      });
    };
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener('focus', refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
    };
  }, [updateGroups]);

  // 上传新资料后只触发一次增量同步，不在普通概览轮询中重复调用模型。
  useEffect(() => {
    const onMaterialUploaded = () => void runSync();
    window.addEventListener(MATERIAL_UPLOADED_EVENT, onMaterialUploaded);
    return () => window.removeEventListener(MATERIAL_UPLOADED_EVENT, onMaterialUploaded);
  }, [syncing]);

  async function runSync() {
    setSyncing(true);
    setError('');
    try {
      const result = await startReviewSyncQueue(async (progress) => {
        setSyncMessage(formatSyncMessage(progress));
        await loadData();
      });
      setSyncMessage(formatSyncMessage(result));
      await loadData();
    } catch (syncError) {
      setError(syncError instanceof Error ? syncError.message : '学习资料同步失败');
    } finally {
      setSyncing(false);
    }
  }

  // 开始一次排序交互，并使更早发出的 group 查询结果失效。
  function beginOrderInteraction(allowSingleGroup = false) {
    if (orderInteractionRef.current || loading || deletingKey !== null || deleteTarget !== null || (!allowSingleGroup && groupsRef.current.length < 2) || (allowSingleGroup && groupsRef.current.length < 1)) return false;
    orderInteractionRef.current = true;
    groupReadVersionRef.current += 1;
    setOrderMessage('');
    setOrderError('');
    return true;
  }

  // 结束排序时再次推进读取版本，丢弃排序期间开始的旧顺序请求。
  function finishOrderInteraction() {
    orderInteractionRef.current = false;
    groupReadVersionRef.current += 1;
    setOrderSaving(false);
    setDraggingMaterialId(null);
  }

  // 乐观顺序提交失败时只恢复资料次序，保留期间发生的卡片评分等内容变化。
  async function persistGroupOrder(previousOrder: number[], successMessage: string) {
    const materialIds = materialOrder(groupsRef.current);
    if (sameNumberOrder(previousOrder, materialIds)) {
      finishOrderInteraction();
      return;
    }
    setOrderSaving(true);
    try {
      const saved = await updateDueReviewGroupOrder(materialIds);
      updateGroups((current) => orderGroupsByMaterialIds(current, saved.materialIds));
      setOrderMessage(successMessage);
    } catch (orderFailure) {
      updateGroups((current) => orderGroupsByMaterialIds(current, previousOrder));
      setOrderError(orderFailure instanceof Error ? `优先级保存失败，已恢复原顺序：${orderFailure.message}` : '优先级保存失败，已恢复原顺序');
    } finally {
      finishOrderInteraction();
    }
  }

  // 触摸按钮和键盘共用同一移动入口，每次移动立即保存完整可见顺序。
  function moveGroupToIndex(materialId: number, targetIndex: number) {
    if (!beginOrderInteraction()) return;
    const previousOrder = materialOrder(groupsRef.current);
    const currentIndex = groupsRef.current.findIndex((group) => group.materialId === materialId);
    const boundedTarget = Math.max(0, Math.min(groupsRef.current.length - 1, targetIndex));
    if (currentIndex < 0 || currentIndex === boundedTarget) {
      finishOrderInteraction();
      return;
    }
    const moved = moveGroup(groupsRef.current, materialId, boundedTarget);
    updateGroups(moved);
    const title = moved[boundedTarget]?.materialTitle || '资料';
    void persistGroupOrder(previousOrder, `已将“${title}”调整到第 ${boundedTarget + 1} 位`);
  }

  // 桌面端只允许从标题手柄开始拖拽，并保存拖拽前的稳定顺序用于失败回滚。
  function handleGroupDragStart(event: ReactDragEvent<HTMLButtonElement>, materialId: number) {
    if (!beginOrderInteraction(true)) {
      event.preventDefault();
      return;
    }
    dragSourceIdRef.current = materialId;
    dragSnapshotOrderRef.current = materialOrder(groupsRef.current);
    dragDropHandledRef.current = false;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(materialId));
    event.dataTransfer.setData('application/x-review-material-id', String(materialId));
    setDraggingMaterialId(materialId);
  }

  // 指针进入其他资料组时立即重排，用户无需等待松手才看到目标位置。
  function handleGroupDragEnter(targetMaterialId: number) {
    const sourceMaterialId = dragSourceIdRef.current;
    if (sourceMaterialId == null || sourceMaterialId === targetMaterialId) return;
    const targetIndex = groupsRef.current.findIndex((group) => group.materialId === targetMaterialId);
    if (targetIndex < 0) return;
    updateGroups(moveGroup(groupsRef.current, sourceMaterialId, targetIndex));
  }

  function handleGroupDragOver(event: ReactDragEvent<HTMLElement>) {
    if (dragSourceIdRef.current == null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }

  // 松手后只发一次批量排序请求，避免拖动过程产生请求风暴。
  function handleGroupDrop(event: ReactDragEvent<HTMLElement>) {
    if (dragSourceIdRef.current == null) return;
    event.preventDefault();
    dragDropHandledRef.current = true;
    dragSourceIdRef.current = null;
    setDraggingMaterialId(null);
    void persistGroupOrder(dragSnapshotOrderRef.current, '今日资料优先级已保存');
  }

  // 拖拽被 Esc 或移出列表取消时恢复拖拽前顺序，不向服务端写入。
  function handleGroupDragEnd() {
    if (dragDropHandledRef.current) {
      dragDropHandledRef.current = false;
      return;
    }
    if (dragSourceIdRef.current != null) {
      updateGroups((current) => orderGroupsByMaterialIds(current, dragSnapshotOrderRef.current));
    }
    dragSourceIdRef.current = null;
    setFolderDropTargetId(null);
    finishOrderInteraction();
  }

  // 文件夹是文档拖拽目标；投放时恢复队列的临时排序，不写入优先级接口。
  function handleFolderDragEnter(folderId: number) {
    if (dragSourceIdRef.current != null) setFolderDropTargetId(folderId);
  }

  function handleFolderDragOver(event: ReactDragEvent<HTMLElement>) {
    if (dragSourceIdRef.current == null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }

  async function handleFolderDrop(event: ReactDragEvent<HTMLElement>, folder: ReviewFolder) {
    const materialId = dragSourceIdRef.current;
    if (materialId == null) return;
    event.preventDefault();
    event.stopPropagation();
    dragDropHandledRef.current = true;
    updateGroups((current) => orderGroupsByMaterialIds(current, dragSnapshotOrderRef.current));
    dragSourceIdRef.current = null;
    setFolderDropTargetId(null);
    finishOrderInteraction();
    setFolderBusy(true);
    setFolderError('');
    try {
      const result = await assignReviewMaterialsToFolder([materialId], folder.id);
      setFolderMessage(`已将 1 份资料移入“${folder.name}”`);
      setSelectedMaterialIds((previous) => omitKey(previous, materialId));
      if (result.movedCount) await loadData();
    } catch (folderFailure) {
      setFolderError(folderFailure instanceof Error ? folderFailure.message : '拖拽归档失败');
    } finally {
      setFolderBusy(false);
    }
  }

  // 今日队列可以定位到未归档资料行，也可携带文档 ID 跳进对应文件夹。
  function locateReviewMaterial(group: ReviewCardGroup) {
    if (group.folderId) {
      const params = new URLSearchParams({ materialId: String(group.materialId) });
      navigate(`/reviews/folders/${group.folderId}?${params.toString()}`);
      return;
    }
    const target = document.getElementById(reviewMaterialArchiveId(group.materialId));
    if (!target) {
      setFolderError('暂时没有找到对应资料，请刷新资料归档后重试');
      return;
    }
    setLocatedMaterialId(group.materialId);
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => target.focus({ preventScroll: true }), 320);
    if (locateTimerRef.current !== null) window.clearTimeout(locateTimerRef.current);
    locateTimerRef.current = window.setTimeout(() => {
      setLocatedMaterialId((current) => current === group.materialId ? null : current);
      locateTimerRef.current = null;
    }, 2600);
  }

  // 手柄获得焦点后支持方向键逐项移动，Home/End 可快速置顶或置底。
  function handleGroupOrderKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, materialId: number, currentIndex: number) {
    const targetIndex = event.key === 'ArrowUp'
      ? currentIndex - 1
      : event.key === 'ArrowDown'
        ? currentIndex + 1
        : event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? groupsRef.current.length - 1
            : null;
    if (targetIndex == null) return;
    event.preventDefault();
    moveGroupToIndex(materialId, targetIndex);
  }

  // 用户主动揭示单张卡片的答案和原文 evidence。
  async function revealCard(card: ReviewCard) {
    if (revealedCards[card.id]?.answer || revealLoadingId === card.id) return;
    setRevealLoadingId(card.id);
    setGradeError('');
    try {
      const revealed = await fetchReviewCard(card.id);
      if (!revealed.answer) throw new Error('答案暂时不可用');
      setRevealedCards((previous) => ({ ...previous, [card.id]: revealed }));
      reviewStartedAtRef.current[card.id] = Date.now();
    } catch (revealError) {
      setGradeError(revealError instanceof Error ? revealError.message : '答案读取失败');
    } finally {
      setRevealLoadingId(null);
    }
  }

  // 评分后移除当前卡片，再用服务端队列补齐剩余每日额度。
  async function gradeCard(card: ReviewCard, rating: ReviewRating) {
    const revealed = revealedCards[card.id];
    if (!revealed?.answer || gradingId !== null) return;
    setGradingId(card.id);
    setGradeError('');
    setGradeMessage('');
    try {
      const result = await gradeReviewCard(card.id, {
        rating,
        durationMs: Math.max(0, Date.now() - (reviewStartedAtRef.current[card.id] || Date.now()))
      });
      setGradeMessage(formatGradeMessage(result, rating));
      removeCards([card.id]);
      setRevealedCards((previous) => omitKey(previous, card.id));
      setHintCardIds((previous) => omitKey(previous, card.id));
      await refreshAfterGrade();
    } catch (gradeLoadError) {
      setGradeError(gradeLoadError instanceof Error ? gradeLoadError.message : '复习评分提交失败');
    } finally {
      setGradingId(null);
    }
  }

  async function refreshAfterGrade() {
    const groupReadVersion = ++groupReadVersionRef.current;
    const groupsMayApply = !orderInteractionRef.current;
    const results = await Promise.allSettled([
      fetchReviewOverview(),
      fetchDueReviewGroups(100),
      fetchReviewMaterials(),
      fetchReviewFolders()
    ]);
    if (results[0].status === 'fulfilled') {
      setOverview(results[0].value);
      if (!settingsDirtyRef.current) setSettingsDraft(normalizeSettings(results[0].value.settings));
      publishOverviewEvent();
    }
    if (results[1].status === 'fulfilled' && groupsMayApply && groupReadVersion === groupReadVersionRef.current && !orderInteractionRef.current) {
      updateGroups(results[1].value.groups);
    }
    if (results[2].status === 'fulfilled') setMaterials(results[2].value);
    if (results[3].status === 'fulfilled') setFolders(results[3].value);
  }

  function removeCards(cardIds: number[]) {
    const removed = new Set(cardIds);
    groupReadVersionRef.current += 1;
    updateGroups((previous) => previous
      .map((group) => {
        const cards = group.cards.filter((card) => !removed.has(card.id));
        return { ...group, cards, dueCardCount: cards.length };
      })
      .filter((group) => group.cards.length > 0));
  }

  function removeMaterialsFromView(materialIds: number[]) {
    const removed = new Set(materialIds);
    groupReadVersionRef.current += 1;
    updateGroups((previous) => previous.filter((group) => !removed.has(group.materialId)));
    setMaterials((previous) => previous.filter((material) => {
      const materialId = resolveMaterialId(material);
      return materialId == null || !removed.has(materialId);
    }));
    setRevealedCards((previous) => omitMaterialCards(previous, removed));
    setSelectedMaterialIds((previous) => omitKeys(previous, materialIds));
    setSelectedCardIds({});
    setHintCardIds({});
    if (originalCard && removed.has(originalCard.materialId)) setOriginalCard(null);
  }

  function requestCardDeletion(card: ReviewCard) {
    setDeleteError('');
    setDeleteMessage('');
    setDeleteTarget({ scope: 'CARD', card });
  }

  function requestMaterialDeletion(materialId: number, title: string) {
    setDeleteError('');
    setDeleteMessage('');
    setDeleteTarget({ scope: 'MATERIAL', materialId, title });
  }

  function requestCardBatchDeletion() {
    if (!selectedCardIdList.length) return;
    setDeleteError('');
    setDeleteMessage('');
    setDeleteTarget({ scope: 'CARD_BATCH', cardIds: selectedCardIdList });
  }

  function requestMaterialBatchDeletion() {
    if (!selectedMaterialIdList.length) return;
    const selected = new Set(selectedMaterialIdList);
    const titles = materials
      .filter((material) => {
        const materialId = resolveMaterialId(material);
        return materialId != null && selected.has(materialId);
      })
      .map((material) => material.title);
    setDeleteError('');
    setDeleteMessage('');
    setDeleteTarget({ scope: 'MATERIAL_BATCH', materialIds: selectedMaterialIdList, titles });
  }

  // 删除成功后先更新本地列表，再独立刷新服务端统计，刷新失败不会误报删除失败。
  async function confirmDeletion() {
    const target = deleteTarget;
    if (!target || deletingKey !== null) return;
    const key = deletionKey(target);
    setDeletingKey(key);
    setDeleteError('');
    try {
      if (target.scope === 'CARD') {
        await deleteReviewCard(target.card.id);
        removeCards([target.card.id]);
        setRevealedCards((previous) => omitKey(previous, target.card.id));
        setHintCardIds((previous) => omitKey(previous, target.card.id));
        setSelectedCardIds((previous) => omitKey(previous, target.card.id));
        delete reviewStartedAtRef.current[target.card.id];
        if (originalCard?.id === target.card.id) setOriginalCard(null);
        setDeleteMessage('卡片已删除，后续同步不会恢复同一卡片');
      } else if (target.scope === 'MATERIAL') {
        await deleteReviewMaterial(target.materialId);
        removeMaterialsFromView([target.materialId]);
        setDeleteMessage(`“${target.title}”已移出复习中心，原始资料仍保留`);
      } else if (target.scope === 'CARD_BATCH') {
        const result = await deleteReviewCards(target.cardIds);
        removeCards(result.cardIds);
        setRevealedCards((previous) => omitKeys(previous, result.cardIds));
        setHintCardIds((previous) => omitKeys(previous, result.cardIds));
        setSelectedCardIds((previous) => omitKeys(previous, result.cardIds));
        result.cardIds.forEach((cardId) => delete reviewStartedAtRef.current[cardId]);
        if (originalCard && result.cardIds.includes(originalCard.id)) setOriginalCard(null);
        setDeleteMessage(`已删除 ${result.deletedCount} 张卡片，后续同步不会恢复`);
      } else {
        const result = await deleteReviewMaterials(target.materialIds);
        removeMaterialsFromView(result.materialIds);
        setDeleteMessage(`已将 ${result.deletedCount} 份资料移出复习中心，原始资料仍保留`);
      }
      setDeleteTarget(null);
      await refreshAfterGrade();
    } catch (deleteFailure) {
      setDeleteError(deleteFailure instanceof Error ? deleteFailure.message : '复习内容删除失败');
    } finally {
      setDeletingKey(null);
    }
  }

  async function regenerateMaterial(material: ReviewMaterial, userFeedback?: string) {
    const materialId = resolveMaterialId(material);
    if (materialId == null || busyMaterialId !== null) return;
    const needsContext = material.status === 'FAILED' || material.status === 'NEEDS_REVIEW' || material.needsManualReview;
    if (!userFeedback && needsContext) {
      setReviewFeedbackTarget(material);
      setReviewFeedbackText('');
      return;
    }
    setBusyMaterialId(materialId);
    setError('');
    try {
      const result = await generateReviewMaterial(materialId, userFeedback);
      if (result.status === 'NEEDS_REVIEW' || result.needsManualReview) {
        setReviewFeedbackTarget(result);
        setSyncMessage(`“${material.title}”自动修复仍未通过，请补充说明后再试`);
      } else if (result.status === 'FAILED') {
        setReviewFeedbackTarget(result);
        setSyncMessage(`“${material.title}”暂时生成失败，可补充说明后重试`);
      } else {
        setReviewFeedbackTarget(null);
        setSyncMessage(`“${material.title}”已重新生成复习卡片`);
      }
      await loadData();
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : '复习卡片生成失败');
    } finally {
      setBusyMaterialId(null);
    }
  }

  async function submitReviewFeedback(event: FormEvent) {
    event.preventDefault();
    const target = reviewFeedbackTarget;
    if (!target || reviewFeedbackBusy) return;
    const feedback = reviewFeedbackText.trim();
    setReviewFeedbackBusy(true);
    try {
      await regenerateMaterial(target, feedback || undefined);
      setReviewFeedbackText('');
    } finally {
      setReviewFeedbackBusy(false);
    }
  }

  function openCreateFolder() {
    setFolderError('');
    setFolderMessage('');
    setFolderEditorName('');
    setFolderEditorTarget({ mode: 'CREATE' });
  }

  function openRenameFolder(folder: ReviewFolder) {
    setFolderError('');
    setFolderMessage('');
    setFolderEditorName(folder.name);
    setFolderEditorTarget({ mode: 'RENAME', folder });
  }

  // 新建和重命名共用同一受控表单，成功后以服务端统计刷新文件夹区。
  async function saveFolder(event: FormEvent) {
    event.preventDefault();
    const target = folderEditorTarget;
    const name = folderEditorName.replace(/\s+/g, ' ').trim();
    if (!target || !name || folderBusy) return;
    setFolderBusy(true);
    setFolderError('');
    try {
      const saved = target.mode === 'CREATE'
        ? await createReviewFolder(name)
        : await renameReviewFolder(target.folder.id, name);
      setFolderMessage(target.mode === 'CREATE' ? `已创建文件夹“${saved.name}”` : `已重命名为“${saved.name}”`);
      setFolderEditorTarget(null);
      await loadData();
    } catch (folderFailure) {
      setFolderError(folderFailure instanceof Error ? folderFailure.message : '复习文件夹保存失败');
    } finally {
      setFolderBusy(false);
    }
  }

  // 删除目录只解除归档，资料和卡片继续保留在复习中心。
  async function confirmFolderDeletion() {
    const folder = folderDeleteTarget;
    if (!folder || folderBusy) return;
    setFolderBusy(true);
    setFolderError('');
    try {
      const result = await deleteReviewFolder(folder.id);
      setFolderDeleteTarget(null);
      setFolderMessage(`已删除“${folder.name}”，${result.unfiledMaterialCount} 份资料已回到未归档`);
      await loadData();
    } catch (folderFailure) {
      setFolderError(folderFailure instanceof Error ? folderFailure.message : '复习文件夹删除失败');
    } finally {
      setFolderBusy(false);
    }
  }

  // 资料归档按完整文档批量提交，卡片不会被拆分到不同文件夹。
  async function moveSelectedMaterialsToFolder() {
    if (!selectedMaterialIdList.length || folderBusy) return;
    const folderId = folderTargetValue === 'unfiled' ? null : Number(folderTargetValue);
    setFolderBusy(true);
    setFolderError('');
    try {
      const result = await assignReviewMaterialsToFolder(selectedMaterialIdList, folderId);
      const folderName = folders.find((folder) => folder.id === result.folderId)?.name;
      setFolderMessage(folderName
        ? `已将 ${result.movedCount} 份资料移入“${folderName}”`
        : `已将 ${result.movedCount} 份资料移回未归档`);
      setSelectedMaterialIds({});
      await loadData();
    } catch (folderFailure) {
      setFolderError(folderFailure instanceof Error ? folderFailure.message : '复习资料归档失败');
    } finally {
      setFolderBusy(false);
    }
  }

  // 主页面提供全选入口，仍以整份文档为最小批量归档单位。
  function toggleAllMaterials() {
    if (!selectableMaterialIdList.length) return;
    setSelectedMaterialIds(allMaterialsSelected
      ? {}
      : Object.fromEntries(selectableMaterialIdList.map((materialId) => [materialId, true])));
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault();
    setSettingsSaving(true);
    setSettingsMessage('');
    try {
      const saved = await updateReviewSettings(normalizeSettings(settingsDraft));
      setSettingsDraft(saved);
      setOverview((previous) => previous ? { ...previous, settings: saved } : previous);
      settingsDirtyRef.current = false;
      setSettingsMessage('设置已保存');
    } catch (settingsError) {
      setSettingsMessage(settingsError instanceof Error ? settingsError.message : '设置保存失败');
    } finally {
      setSettingsSaving(false);
    }
  }

  async function requestBrowserNotification() {
    if (!('Notification' in window)) {
      setSettingsMessage('当前浏览器不支持通知');
      return;
    }
    const permission = await Notification.requestPermission();
    setNotificationPermission(permission);
    setSettingsMessage(permission === 'granted' ? '浏览器提醒已开启' : '浏览器提醒未开启');
    window.dispatchEvent(new Event('review-notification-permission-updated'));
  }

  const totalCards = groups.reduce((count, group) => count + group.cards.length, 0);
  const unfiledMaterialCount = materials.filter((material) => material.cardCount > 0 && !material.folderId).length;
  const orderBusy = draggingMaterialId !== null || orderSaving;
  const orderControlsDisabled = loading || orderSaving || deletingKey !== null || deleteTarget !== null || groups.length < 1;

  return (
    <div className="review-center-page">
      <header className="review-page-header">
        <div>
          <div className="page-eyebrow"><Target size={14} />每日复习</div>
          <h2>复习中心</h2>
          <p>按资料整理的到期知识点</p>
        </div>
        <div className="review-header-actions">
          <button className="outline-action" type="button" onClick={() => void runSync()} disabled={syncing || orderBusy}>
            {syncing ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            {syncing ? '同步中' : '同步资料'}
          </button>
          <button className="icon-button compact" type="button" title="刷新复习数据" aria-label="刷新复习数据" onClick={() => void loadData()} disabled={orderBusy}>
            <RefreshCw size={17} />
          </button>
        </div>
      </header>

      {error ? <div className="review-alert danger"><CircleAlert size={17} />{error}</div> : null}
      {syncMessage ? <div className="review-alert"><Check size={17} />{syncMessage}</div> : null}
      {gradeMessage ? <div className="review-alert success"><Check size={17} />{gradeMessage}</div> : null}
      {gradeError ? <div className="review-alert danger"><CircleAlert size={17} />{gradeError}</div> : null}
      {deleteMessage ? <div className="review-alert success"><Check size={17} />{deleteMessage}</div> : null}
      {deleteError ? <div className="review-alert danger"><CircleAlert size={17} />{deleteError}</div> : null}
      {orderMessage ? <div className="review-alert success" role="status"><Check size={17} />{orderMessage}</div> : null}
      {orderError ? <div className="review-alert danger" role="alert"><CircleAlert size={17} />{orderError}</div> : null}
      {folderMessage ? <div className="review-alert success" role="status"><Check size={17} />{folderMessage}</div> : null}
      {folderError ? <div className="review-alert danger" role="alert"><CircleAlert size={17} />{folderError}</div> : null}

      <section className="review-stat-strip" aria-label="复习统计">
        <div className="review-stat primary"><span>今日待复习资料</span><strong>{dueMaterialCount}</strong><small>{overview && overview.dueCount > totalCards ? `到期积压 ${overview.dueCount} 张卡片` : totalCards ? `当前展示 ${groups.length} 份资料 · ${totalCards} 张卡片` : '队列已清空'}</small></div>
        <div className="review-stat"><span>今日已复习资料</span><strong>{overview?.todayReviewedCount ?? '--'}</strong><small>每日上限 {dailyLimit} 份资料</small></div>
        <div className="review-stat"><span>学习资料</span><strong>{overview?.activeMaterialCount ?? '--'}</strong><small>已生成复习卡片的资料</small></div>
        <div className="review-stat progress-stat"><div><span>今日进度</span><strong>{reviewProgress}%</strong></div><div className="review-progress"><i style={{ width: `${reviewProgress}%` }} /></div><small>{overview?.nextDueAt ? `下一张卡片 ${formatTime(overview.nextDueAt)}` : '暂无下一张'}</small></div>
      </section>

      <ReviewFolderLibrary
        folders={folders}
        unfiledMaterialCount={unfiledMaterialCount}
        busy={folderBusy}
        dropTargetId={folderDropTargetId}
        onCreate={openCreateFolder}
        onOpen={(folder) => navigate(`/reviews/folders/${folder.id}`)}
        onRename={openRenameFolder}
        onDelete={setFolderDeleteTarget}
        onDragEnter={handleFolderDragEnter}
        onDragOver={handleFolderDragOver}
        onDrop={(event, folder) => void handleFolderDrop(event, folder)}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFolderDropTargetId(null);
        }}
      />

      <div className="review-content-grid">
        <section className="review-queue-column" aria-labelledby="review-queue-title">
          <div className="review-section-heading"><div><h3 id="review-queue-title">今日复习资料</h3><span>{groups.length ? `${groups.length} 份资料 · ${totalCards} 张卡片` : '暂无到期资料'}</span></div><div className="review-section-actions">{orderSaving ? <span className="review-order-saving" role="status"><Loader2 className="spin" size={14} />保存排序</span> : selectedCardIdList.length ? <button className="outline-action small danger-outline" type="button" onClick={requestCardBatchDeletion} disabled={deletingKey !== null || orderBusy}><Trash2 size={14} />删除选中 {selectedCardIdList.length}</button> : <Clock3 size={18} />}</div></div>
          <p className="review-visually-hidden" id="review-order-instructions">拖动手柄调整资料优先级；键盘可使用上下方向键移动，Home 置顶，End 置底。</p>
          {loading ? <div className="review-loading"><Loader2 className="spin" size={22} /><span>正在读取复习队列</span></div> : null}
          {!loading && groups.length === 0 ? <EmptyReviewQueue onSync={() => void runSync()} syncing={syncing} /> : null}
          {!loading && groups.length > 0 ? groups.map((group, groupIndex) => (
            <ReviewMaterialGroup
              key={group.materialId}
              group={group}
              position={groupIndex}
              groupCount={groups.length}
              dragging={draggingMaterialId === group.materialId}
              ordering={orderBusy}
              orderDisabled={orderControlsDisabled}
              revealedCards={revealedCards}
              hintCardIds={hintCardIds}
              selectedCardIds={selectedCardIds}
              revealLoadingId={revealLoadingId}
              gradingId={gradingId}
              deletingKey={deletingKey}
              onReveal={(card) => void revealCard(card)}
              onToggleHint={(cardId) => setHintCardIds((previous) => ({ ...previous, [cardId]: !previous[cardId] }))}
              onHide={(cardId) => setRevealedCards((previous) => omitKey(previous, cardId))}
              onOriginal={setOriginalCard}
              onGrade={(card, rating) => void gradeCard(card, rating)}
              onDeleteCard={requestCardDeletion}
              onDeleteMaterial={() => requestMaterialDeletion(group.materialId, group.materialTitle)}
              onLocateMaterial={() => locateReviewMaterial(group)}
              onToggleSelected={(cardId) => setSelectedCardIds((previous) => toggleSelected(previous, cardId))}
              onMove={(targetIndex) => moveGroupToIndex(group.materialId, targetIndex)}
              onOrderKeyDown={(event) => handleGroupOrderKeyDown(event, group.materialId, groupIndex)}
              onDragStart={(event) => handleGroupDragStart(event, group.materialId)}
              onDragEnter={() => handleGroupDragEnter(group.materialId)}
              onDragOver={handleGroupDragOver}
              onDrop={handleGroupDrop}
              onDragEnd={handleGroupDragEnd}
            />
          )) : null}
        </section>

        <aside className="review-side-column">
          <section className="review-panel settings-panel">
            <div className="review-panel-heading"><div><Settings2 size={17} /><h3>复习设置</h3></div><span>{settingsDraft.enabled ? '已开启' : '已暂停'}</span></div>
            <form onSubmit={saveSettings} className="review-settings-form">
              <label className="review-toggle-row"><span><strong>每日提醒</strong><small>到期后显示浏览器提醒</small></span><input type="checkbox" checked={settingsDraft.enabled} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { enabled: event.target.checked })} /></label>
              <label><span>目标记忆率</span><div className="input-with-suffix"><input type="number" min="0.8" max="0.97" step="0.01" value={settingsDraft.desiredRetention} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { desiredRetention: Number(event.target.value) })} /><em>{formatRetention(settingsDraft.desiredRetention)}</em></div></label>
              <label><span>每日文档上限</span><div className="input-with-suffix"><input type="number" min="1" max="100" step="1" value={settingsDraft.dailyLimit} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { dailyLimit: Number(event.target.value) })} /><em>份</em></div></label>
              <label><span>提醒时间</span><input type="time" value={settingsDraft.reminderTime} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { reminderTime: event.target.value })} /></label>
              <label><span>时区</span><select value={settingsDraft.timezone} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { timezone: event.target.value })}>{TIMEZONE_OPTIONS.map((timezone) => <option key={timezone} value={timezone}>{timezone}</option>)}</select></label>
              <button className="primary-action full" type="submit" disabled={settingsSaving}>{settingsSaving ? <Loader2 className="spin" size={16} /> : <Save size={16} />}{settingsSaving ? '保存中' : '保存设置'}</button>
              {settingsMessage ? <p className="form-message">{settingsMessage}</p> : null}
            </form>
            <button className="notification-action" type="button" onClick={() => void requestBrowserNotification()} disabled={notificationPermission === 'granted'}>{notificationPermission === 'granted' ? <BellRing size={16} /> : <Bell size={16} />}{notificationPermission === 'granted' ? '浏览器提醒已开启' : '开启浏览器提醒'}</button>
          </section>

        </aside>

        <section className="review-panel materials-panel" aria-labelledby="review-materials-title">
          <div className="review-panel-heading"><div><BookOpen size={17} /><h3 id="review-materials-title">资料归档</h3></div><span>{materials.length} 份</span></div>
          <div className="review-material-bulkbar">
            <label className="review-material-select-all"><input type="checkbox" checked={allMaterialsSelected} onChange={toggleAllMaterials} disabled={!selectableMaterialIdList.length || folderBusy || orderBusy} /><span>{allMaterialsSelected ? '已全选' : '全选未归档资料'}</span></label>
            {selectedMaterialIdList.length ? <><span className="review-material-selected-count">已选 {selectedMaterialIdList.length} 份文档</span><div className="review-material-folder-controls"><select aria-label="目标复习文件夹" value={folderTargetValue} onChange={(event) => setFolderTargetValue(event.target.value)} disabled={folderBusy}><option value="unfiled">未归档</option>{folders.map((folder) => <option key={folder.id} value={String(folder.id)}>{folder.name}</option>)}</select><button className="outline-action small" type="button" onClick={() => void moveSelectedMaterialsToFolder()} disabled={folderBusy}><MoveRight size={14} />{folderTargetValue === 'unfiled' ? '批量移出文件夹' : '批量进入文件夹'}</button><button className="outline-action small danger-outline" type="button" onClick={requestMaterialBatchDeletion} disabled={deletingKey !== null || orderBusy || folderBusy}><Trash2 size={14} />移出中心</button></div></> : <span className="review-material-select-hint">选择多份文档后可批量进入文件夹</span>}
          </div>
          <div className="review-material-list">
            {materials.length ? materials.map((material) => {
              const materialId = resolveMaterialId(material);
              const queueIndex = materialId == null ? -1 : pendingMaterialIdList.indexOf(materialId);
              return <ReviewMaterialRow key={materialId ?? material.title} material={material} queuePosition={queueIndex >= 0 ? queueIndex + 1 : null} queueTotal={pendingMaterialIdList.length} selected={materialId != null && Boolean(selectedMaterialIds[materialId])} located={materialId != null && locatedMaterialId === materialId} busy={busyMaterialId === materialId} deleting={materialId != null && deletingKey === `MATERIAL:${materialId}`} locked={orderBusy} onToggleSelected={() => { if (materialId != null) setSelectedMaterialIds((previous) => toggleSelected(previous, materialId)); }} onFindMissing={() => { if (materialId != null) setMissingKnowledgeTarget({ materialId, title: material.title, cardCount: material.cardCount }); }} onRegenerate={() => void regenerateMaterial(material)} onDelete={() => { if (materialId != null) requestMaterialDeletion(materialId, material.title); }} />;
            }) : <p className="panel-empty">暂无已索引资料</p>}
          </div>
        </section>
      </div>

      <OriginalEvidenceDialog card={originalCard} onClose={() => setOriginalCard(null)} />
      <ReviewDeletionDialog target={deleteTarget} deleting={deletingKey !== null} onConfirm={() => void confirmDeletion()} onClose={() => { if (deletingKey === null) setDeleteTarget(null); }} />
      <ReviewFolderEditorDialog target={folderEditorTarget} name={folderEditorName} busy={folderBusy} onNameChange={setFolderEditorName} onSubmit={saveFolder} onClose={() => { if (!folderBusy) setFolderEditorTarget(null); }} />
      <ReviewFolderDeleteDialog folder={folderDeleteTarget} busy={folderBusy} onConfirm={() => void confirmFolderDeletion()} onClose={() => { if (!folderBusy) setFolderDeleteTarget(null); }} />
      <ReviewGenerationFeedbackDialog target={reviewFeedbackTarget} feedback={reviewFeedbackText} busy={reviewFeedbackBusy || busyMaterialId !== null} onFeedbackChange={setReviewFeedbackText} onSubmit={submitReviewFeedback} onClose={() => { if (!reviewFeedbackBusy && busyMaterialId === null) setReviewFeedbackTarget(null); }} />
      <ReviewMissingKnowledgeDialog target={missingKnowledgeTarget} onClose={() => setMissingKnowledgeTarget(null)} onCardsAdded={async (addedCount) => { setMissingKnowledgeTarget((previous) => previous ? { ...previous, cardCount: previous.cardCount + addedCount } : null); setFolderMessage(`已追加 ${addedCount} 张遗漏知识点卡片`); await loadData(); window.dispatchEvent(new CustomEvent(REVIEW_CONTENT_UPDATED_EVENT)); }} />
    </div>
  );
}

function ReviewFolderLibrary({
  folders,
  unfiledMaterialCount,
  busy,
  dropTargetId,
  onCreate,
  onOpen,
  onRename,
  onDelete,
  onDragEnter,
  onDragOver,
  onDrop,
  onDragLeave
}: {
  folders: ReviewFolder[];
  unfiledMaterialCount: number;
  busy: boolean;
  dropTargetId: number | null;
  onCreate: () => void;
  onOpen: (folder: ReviewFolder) => void;
  onRename: (folder: ReviewFolder) => void;
  onDelete: (folder: ReviewFolder) => void;
  onDragEnter: (folderId: number) => void;
  onDragOver: (event: ReactDragEvent<HTMLElement>) => void;
  onDrop: (event: ReactDragEvent<HTMLElement>, folder: ReviewFolder) => void;
  onDragLeave: (event: ReactDragEvent<HTMLElement>) => void;
}) {
  return (
    <section className="review-folder-library" aria-labelledby="review-folder-title">
      <div className="review-folder-library-heading">
        <div><span className="review-folder-heading-icon"><FolderOpen size={18} /></span><div><h3 id="review-folder-title">复习文件夹</h3><p>按文档归档，进入文件夹后逐份查看全部卡片</p></div></div>
        <div className="review-folder-heading-actions"><span>{unfiledMaterialCount} 份未归档</span><button className="primary-action" type="button" onClick={onCreate} disabled={busy}><FolderPlus size={16} />新建文件夹</button></div>
      </div>
      {folders.length ? (
        <div className="review-folder-grid">
          {folders.map((folder) => (
            <article className={`review-folder-card${dropTargetId === folder.id ? ' is-drop-target' : ''}`} key={folder.id} onDragEnter={() => onDragEnter(folder.id)} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={(event) => onDrop(event, folder)}>
              <button className="review-folder-open" type="button" onClick={() => onOpen(folder)} disabled={busy} aria-label={`进入文件夹 ${folder.name}`}>
                <span className="review-folder-icon"><Folder size={24} /></span>
                <span className="review-folder-copy"><strong>{folder.name}</strong><small>{folder.materialCount} 份文档 · {folder.cardCount} 张卡片</small></span>
                <span className={`review-folder-due${folder.dueCardCount ? ' has-due' : ''}`}>{folder.dueCardCount ? `${folder.dueCardCount} 张到期` : '暂无到期'}</span>
              </button>
              {dropTargetId === folder.id ? <span className="review-folder-drop-hint" role="status">松开移入此文件夹</span> : null}
              <div className="review-folder-actions">
                <button className="icon-button tiny" type="button" title="重命名文件夹" aria-label={`重命名 ${folder.name}`} onClick={() => onRename(folder)} disabled={busy}><Pencil size={14} /></button>
                <button className="icon-button tiny danger" type="button" title="删除文件夹" aria-label={`删除 ${folder.name}`} onClick={() => onDelete(folder)} disabled={busy}><FolderX size={14} /></button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <button className="review-folder-empty" type="button" onClick={onCreate} disabled={busy}><FolderPlus size={22} /><span><strong>还没有复习文件夹</strong><small>创建后可在右侧选择整份文档归档</small></span></button>
      )}
    </section>
  );
}

function ReviewFolderEditorDialog({ target, name, busy, onNameChange, onSubmit, onClose }: { target: ReviewFolderEditorTarget | null; name: string; busy: boolean; onNameChange: (value: string) => void; onSubmit: (event: FormEvent) => void; onClose: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (target) inputRef.current?.focus();
  }, [target]);
  useEffect(() => {
    if (!target) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [target, busy, onClose]);
  if (!target) return null;
  const title = target.mode === 'CREATE' ? '新建复习文件夹' : '重命名复习文件夹';
  return (
    <div className="review-delete-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <form className="review-folder-dialog" role="dialog" aria-modal="true" aria-labelledby="review-folder-editor-title" onSubmit={onSubmit}>
        <div className="review-folder-dialog-icon"><FolderPlus size={20} /></div>
        <div className="review-folder-dialog-copy"><h3 id="review-folder-editor-title">{title}</h3><p>文件夹以整份文档为单位收纳复习卡片。</p><label><span>文件夹名称</span><input ref={inputRef} value={name} maxLength={80} onChange={(event) => onNameChange(event.target.value)} placeholder="例如：Python 面试" disabled={busy} /></label></div>
        <div className="review-delete-actions"><button className="outline-action" type="button" onClick={onClose} disabled={busy}>取消</button><button className="primary-action" type="submit" disabled={busy || !name.trim()}>{busy ? <Loader2 className="spin" size={16} /> : <Save size={16} />}{busy ? '保存中' : '保存'}</button></div>
      </form>
    </div>
  );
}

function ReviewFolderDeleteDialog({ folder, busy, onConfirm, onClose }: { folder: ReviewFolder | null; busy: boolean; onConfirm: () => void; onClose: () => void }) {
  if (!folder) return null;
  return (
    <div className="review-delete-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <section className="review-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="review-folder-delete-title" aria-describedby="review-folder-delete-description">
        <div className="review-delete-icon"><FolderX size={20} /></div>
        <div className="review-delete-copy"><h3 id="review-folder-delete-title">删除文件夹？</h3><strong>{folder.name}</strong><p id="review-folder-delete-description">文件夹中的 {folder.materialCount} 份文档会回到未归档，原始资料、复习卡片和学习记录都不会删除。</p></div>
        <div className="review-delete-actions"><button className="outline-action" type="button" onClick={onClose} disabled={busy}>取消</button><button className="danger-action" type="button" onClick={onConfirm} disabled={busy}>{busy ? <Loader2 className="spin" size={16} /> : <FolderX size={16} />}{busy ? '处理中' : '删除文件夹'}</button></div>
      </section>
    </div>
  );
}

function ReviewGenerationFeedbackDialog({
  target,
  feedback,
  busy,
  onFeedbackChange,
  onSubmit,
  onClose
}: {
  target: ReviewMaterial | null;
  feedback: string;
  busy: boolean;
  onFeedbackChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onClose: () => void;
}) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (target) inputRef.current?.focus();
  }, [target]);
  useEffect(() => {
    if (!target) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [target, busy, onClose]);
  if (!target) return null;
  const diagnostics = target.qualityFeedback || [];
  return (
    <div className="review-delete-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <form className="review-generation-dialog" role="dialog" aria-modal="true" aria-labelledby="review-generation-title" onSubmit={onSubmit}>
        <div className="review-generation-dialog-icon"><AlertTriangle size={20} /></div>
        <div className="review-generation-dialog-copy">
          <h3 id="review-generation-title">需要人工补充后再生成</h3>
          <p className="review-generation-title" title={target.title}>{target.title}</p>
          <p>自动修复会把下面的质量反馈交给下一轮 DeepSeek。你可以指出本节真正的重点、问题范围或需要保留的原始问句。</p>
          {diagnostics.length ? <div className="review-generation-feedback"><strong>最近质量反馈</strong><ul>{diagnostics.slice(-8).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div> : null}
          <label><span>补充说明（可选）</span><textarea ref={inputRef} value={feedback} maxLength={2000} onChange={(event) => onFeedbackChange(event.target.value)} placeholder="例如：本节只讲 Kafka delete 与 compact 两类清理策略，请逐条保留视频中的原始问题。" disabled={busy} /></label>
        </div>
        <div className="review-delete-actions"><button className="outline-action" type="button" onClick={onClose} disabled={busy}>稍后处理</button><button className="primary-action" type="submit" disabled={busy}>{busy ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}{busy ? '修复生成中' : '带说明重新生成'}</button></div>
      </form>
    </div>
  );
}

function ReviewMaterialGroup({
  group,
  position,
  groupCount,
  dragging,
  ordering,
  orderDisabled,
  revealedCards,
  hintCardIds,
  selectedCardIds,
  revealLoadingId,
  gradingId,
  deletingKey,
  onReveal,
  onToggleHint,
  onHide,
  onOriginal,
  onGrade,
  onDeleteCard,
  onDeleteMaterial,
  onLocateMaterial,
  onToggleSelected,
  onMove,
  onOrderKeyDown,
  onDragStart,
  onDragEnter,
  onDragOver,
  onDrop,
  onDragEnd
}: {
  group: ReviewCardGroup;
  position: number;
  groupCount: number;
  dragging: boolean;
  ordering: boolean;
  orderDisabled: boolean;
  revealedCards: Record<number, ReviewCard>;
  hintCardIds: Record<number, boolean>;
  selectedCardIds: Record<number, boolean>;
  revealLoadingId: number | null;
  gradingId: number | null;
  deletingKey: string | null;
  onReveal: (card: ReviewCard) => void;
  onToggleHint: (cardId: number) => void;
  onHide: (cardId: number) => void;
  onOriginal: (card: ReviewCard) => void;
  onGrade: (card: ReviewCard, rating: ReviewRating) => void;
  onDeleteCard: (card: ReviewCard) => void;
  onDeleteMaterial: () => void;
  onLocateMaterial: () => void;
  onToggleSelected: (cardId: number) => void;
  onMove: (targetIndex: number) => void;
  onOrderKeyDown: (event: ReactKeyboardEvent<HTMLButtonElement>) => void;
  onDragStart: (event: ReactDragEvent<HTMLButtonElement>) => void;
  onDragEnter: () => void;
  onDragOver: (event: ReactDragEvent<HTMLElement>) => void;
  onDrop: (event: ReactDragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
}) {
  const summary = materialSummary(group.materialSummary);
  return (
    <section
      className={`review-material-group${dragging ? ' is-dragging' : ''}`}
      aria-label={`${group.materialTitle}，优先级第 ${position + 1} 位`}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <header className="review-group-header">
        <div className="review-group-title">
          <button
            className="review-group-drag-handle"
            type="button"
            draggable={!orderDisabled}
            disabled={orderDisabled}
            title="拖拽调整优先级；键盘使用方向键"
            aria-label={`调整 ${group.materialTitle} 的优先级，当前第 ${position + 1} 位`}
            aria-describedby="review-order-instructions"
            aria-keyshortcuts="ArrowUp ArrowDown Home End"
            onKeyDown={onOrderKeyDown}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
          >
            <GripVertical size={17} />
          </button>
          <span className="material-type-icon">{isVideoType(group.documentType) ? <FileVideo2 size={17} /> : <FileText size={17} />}</span>
          <div><h4>{group.materialTitle}</h4><span>{formatDocumentType(group.documentType)} · {group.dueCardCount} 张到期 · {group.folderName ? `文件夹：${group.folderName}` : '未归档'} · 优先级 {position + 1}</span></div>
        </div>
        <div className="review-group-actions">
          <div className="review-group-step-actions" aria-label="调整资料优先级">
            <button className="icon-button tiny" type="button" title="上移资料" aria-label={`上移 ${group.materialTitle}`} onClick={() => onMove(position - 1)} disabled={orderDisabled || ordering || position === 0}><ArrowUp size={14} /></button>
            <button className="icon-button tiny" type="button" title="下移资料" aria-label={`下移 ${group.materialTitle}`} onClick={() => onMove(position + 1)} disabled={orderDisabled || ordering || position === groupCount - 1}><ArrowDown size={14} /></button>
          </div>
          <span className="group-count">{group.cards.length}</span>
          <button className="outline-action small review-locate-action" type="button" title={group.folderId ? `在文件夹“${group.folderName || '复习文件夹'}”中定位资料` : '在资料归档中定位资料'} aria-label={`定位资料：${group.materialTitle}`} onClick={onLocateMaterial} disabled={ordering}><LocateFixed size={14} /><span className="review-locate-label">定位资料</span></button>
          <button className="icon-button tiny danger" type="button" title="将资料移出复习中心" aria-label={`将 ${group.materialTitle} 移出复习中心`} onClick={onDeleteMaterial} disabled={deletingKey !== null || ordering}>{deletingKey === `MATERIAL:${group.materialId}` ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button>
        </div>
      </header>
      <div className="review-card-grid">
        <ReviewMaterialSummary materialId={group.materialId} summary={summary} />
        {group.cards.map((card) => <ReviewQuestionCard key={card.id} card={card} revealed={revealedCards[card.id]} selected={Boolean(selectedCardIds[card.id])} showHint={Boolean(hintCardIds[card.id])} revealLoading={revealLoadingId === card.id} grading={gradingId === card.id} deleting={deletingKey === `CARD:${card.id}`} locked={ordering} onToggleSelected={() => onToggleSelected(card.id)} onReveal={() => onReveal(card)} onHide={() => onHide(card.id)} onToggleHint={() => onToggleHint(card.id)} onOriginal={() => onOriginal(revealedCards[card.id] || card)} onGrade={(rating) => onGrade(card, rating)} onDelete={() => onDeleteCard(card)} />)}
      </div>
    </section>
  );
}

// 资料摘要独立占据卡片网格首行，帮助用户先建立整体脉络，再进入逐题回忆。
function ReviewMaterialSummary({ materialId, summary }: { materialId: number; summary: string }) {
  const headingId = `review-summary-${materialId}`;
  return (
    <article className="review-material-summary-card" aria-labelledby={headingId}>
      <header className="review-material-summary-heading">
        <span className="review-material-summary-icon" aria-hidden="true"><BookOpen size={16} /></span>
        <div>
          <h5 id={headingId}>资料总结</h5>
          <span>先掌握本资料的核心脉络，再开始知识点回忆</span>
        </div>
      </header>
      <MarkdownText content={summary} />
    </article>
  );
}

function ReviewQuestionCard({
  card,
  revealed,
  selected,
  showHint,
  revealLoading,
  grading,
  deleting,
  locked,
  onToggleSelected,
  onReveal,
  onHide,
  onToggleHint,
  onOriginal,
  onGrade,
  onDelete
}: {
  card: ReviewCard;
  revealed?: ReviewCard;
  selected: boolean;
  showHint: boolean;
  revealLoading: boolean;
  grading: boolean;
  deleting: boolean;
  locked: boolean;
  onToggleSelected: () => void;
  onReveal: () => void;
  onHide: () => void;
  onToggleHint: () => void;
  onOriginal: () => void;
  onGrade: (rating: ReviewRating) => void;
  onDelete: () => void;
}) {
  const isRevealed = Boolean(revealed?.answer);
  return (
    <article className={`review-question-card${isRevealed ? ' is-revealed' : ''}${selected ? ' is-selected' : ''}`}>
      <div className="review-card-meta"><div className="review-card-meta-leading"><input type="checkbox" checked={selected} onChange={onToggleSelected} aria-label={`选择卡片：${card.question}`} /><span>知识点 {card.reviewCount > 0 ? `· 已复习 ${card.reviewCount} 次` : '· 首次复习'}</span></div><div className="review-card-meta-actions"><time>{formatDueDate(card.dueAt)}</time><button className="icon-button tiny danger" type="button" title="删除卡片" aria-label={`删除卡片：${card.question}`} onClick={onDelete} disabled={deleting || locked}>{deleting ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button></div></div>
      <h5>{card.question}</h5>
      {!isRevealed ? (
        <div className="review-card-collapsed-actions">
          <button className="text-action" type="button" onClick={onReveal} disabled={revealLoading}>{revealLoading ? <Loader2 className="spin" size={15} /> : <Eye size={15} />}{revealLoading ? '读取中' : '查看答案'}</button>
          <button className="icon-text-action" type="button" onClick={onToggleHint} aria-expanded={showHint}><EyeOff size={15} />{showHint ? '收起提示' : '看提示'}</button>
        </div>
      ) : (
        <>
          <div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={revealed?.answer || ''} /></div>
          <div className="review-reveal-actions"><button className="outline-action small" type="button" onClick={onOriginal} disabled={!revealed?.evidenceRefs?.length}><ArrowUpRight size={15} />查看 RAG 原文</button><button className="icon-text-action" type="button" onClick={onHide}><EyeOff size={15} />收起答案</button></div>
          <div className="review-rating-block"><span>回忆结果</span><div className="review-rating-options">{RATING_OPTIONS.map((option) => <button key={option.rating} type="button" className={`rating-button rating-${option.rating}`} onClick={() => onGrade(option.rating)} disabled={grading}>{grading ? <Loader2 className="spin" size={15} /> : <Check size={15} />}<span><strong>{option.label}</strong><small>{option.detail}</small></span></button>)}</div></div>
        </>
      )}
      {showHint && !isRevealed ? <div className="review-hint"><span>提示</span>{card.hint || '先回忆这一节的核心概念、作用和关键步骤'}</div> : null}
    </article>
  );
}

function ReviewDeletionDialog({
  target,
  deleting,
  onConfirm,
  onClose
}: {
  target: ReviewDeleteTarget | null;
  deleting: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!target) return undefined;
    cancelButtonRef.current?.focus();
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !deleting) onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [target, deleting, onClose]);

  if (!target) return null;
  const isMaterial = target.scope === 'MATERIAL' || target.scope === 'MATERIAL_BATCH';
  const count = target.scope === 'CARD_BATCH' ? target.cardIds.length : target.scope === 'MATERIAL_BATCH' ? target.materialIds.length : 1;
  const title = target.scope === 'CARD'
    ? target.card.question
    : target.scope === 'MATERIAL'
      ? target.title
      : target.scope === 'CARD_BATCH'
        ? `已选择 ${count} 张复习卡片`
        : `${target.titles.slice(0, 2).join('、')}${target.titles.length > 2 ? ` 等 ${count} 份资料` : ''}`;
  const dialogTitle = isMaterial
    ? count > 1 ? `将 ${count} 份资料移出复习中心？` : '将资料移出复习中心？'
    : count > 1 ? `删除 ${count} 张复习卡片？` : '删除这张复习卡片？';
  return (
    <div className="review-delete-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !deleting) onClose(); }}>
      <section className="review-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="review-delete-title" aria-describedby="review-delete-description" aria-busy={deleting}>
        <div className="review-delete-icon"><AlertTriangle size={20} /></div>
        <div className="review-delete-copy">
          <h3 id="review-delete-title">{dialogTitle}</h3>
          <strong>{title}</strong>
          <p id="review-delete-description">{isMaterial ? '该资料的全部复习卡片将停止显示，后续同步也不会重新生成；原始文件和 RAG 索引仍会保留。' : '该卡片将停止显示，后续同步或重新生成也不会恢复同一卡片。'}</p>
        </div>
        <div className="review-delete-actions">
          <button ref={cancelButtonRef} className="outline-action" type="button" onClick={onClose} disabled={deleting}>取消</button>
          <button className="danger-action" type="button" onClick={onConfirm} disabled={deleting}>{deleting ? <Loader2 className="spin" size={16} /> : <Trash2 size={16} />}{deleting ? '处理中' : isMaterial ? count > 1 ? `移出 ${count} 份` : '确认移出' : count > 1 ? `删除 ${count} 张` : '删除卡片'}</button>
        </div>
      </section>
    </div>
  );
}

function OriginalEvidenceDialog({ card, onClose }: { card: ReviewCard | null; onClose: () => void }) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!card) return undefined;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [card, onClose]);

  useEffect(() => {
    if (card) closeButtonRef.current?.focus();
  }, [card]);

  if (!card) return null;
  const evidences = card.evidenceRefs || [];
  return (
    <div className="evidence-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="evidence-dialog" role="dialog" aria-modal="true" aria-labelledby="evidence-dialog-title">
        <header><div><span className="page-eyebrow">原文证据</span><h3 id="evidence-dialog-title">{card.materialTitle}</h3></div><button ref={closeButtonRef} className="icon-button compact" type="button" title="关闭原文" aria-label="关闭原文" onClick={onClose}><X size={18} /></button></header>
        <div className="evidence-dialog-question"><span>问题</span><strong>{card.question}</strong></div>
        <div className="evidence-dialog-list">{evidences.length ? evidences.map((evidence) => <EvidenceRow key={evidence.evidenceId} evidence={evidence} />) : <div className="panel-empty">暂无可定位的原文 evidence</div>}</div>
      </section>
    </div>
  );
}

function EvidenceRow({ evidence }: { evidence: RagEvidence }) {
  const href = buildEvidenceOpenHref(evidence);
  const video = isVideoType(evidence.documentType) || Boolean(evidence.startTime);
  return (
    <article className="evidence-dialog-item">
      <div className="evidence-item-head"><span>{video ? <FileVideo2 size={15} /> : <FileText size={15} />}{evidence.sectionTitle || evidence.sectionName || '原文片段'}</span><small>{evidence.startTime ? `${evidence.startTime}${evidence.endTime ? ` - ${evidence.endTime}` : ''}` : `evidence ${evidence.evidenceId}`}</small></div>
      <MarkdownText content={evidence.snippet || '暂无片段'} />
      {href ? <a className="source-jump-link" href={href} target="_blank" rel="noreferrer"><ArrowUpRight size={15} />{video ? '从此处播放' : '定位原文'}</a> : <span className="source-unavailable">暂无可用跳转地址</span>}
    </article>
  );
}

function ReviewMaterialRow({ material, queuePosition, queueTotal, selected, located, busy, deleting, locked, onToggleSelected, onFindMissing, onRegenerate, onDelete }: { material: ReviewMaterial; queuePosition: number | null; queueTotal: number; selected: boolean; located: boolean; busy: boolean; deleting: boolean; locked: boolean; onToggleSelected: () => void; onFindMissing: () => void; onRegenerate: () => void; onDelete: () => void }) {
  const summary = materialSummary(material.summary, material.reason, material.status);
  const manualReview = material.status === 'NEEDS_REVIEW' || material.needsManualReview;
  const showProgress = ['PENDING', 'GENERATING', 'FAILED', 'NEEDS_REVIEW'].includes((material.status || '').toUpperCase());
  const materialId = resolveMaterialId(material);
  return (
    <article id={materialId == null ? undefined : reviewMaterialArchiveId(materialId)} className={`review-material-row${selected ? ' is-selected' : ''}${located ? ' is-located' : ''}${manualReview ? ' needs-manual-review' : ''}`} tabIndex={-1}>
      <label className="material-row-selector-hitbox" title={`选择资料：${material.title}`}>
        <input className="material-row-selector" type="checkbox" checked={selected} onChange={onToggleSelected} aria-label={`选择资料：${material.title}`} />
      </label>
      <div className="material-row-icon">{isVideoType(material.documentType) ? <FileVideo2 size={17} /> : <FileText size={17} />}</div>
      <div className="material-row-copy">
        <strong title={material.title}>{material.title}</strong>
        <span>{formatReviewClassification(material.category, material.isLearningContent)} · {material.cardCount} 张卡片 · {material.folderName || '未归档'}</span>
      </div>
      <div className={`material-status ${statusClass(material.status)}`}>{formatGenerationStatus(material.status)}</div>
      <div className="material-row-actions">
        {material.status === 'GENERATED' ? <button className="icon-button tiny" type="button" title="对话补充遗漏知识点" aria-label={`为 ${material.title} 补充遗漏知识点`} onClick={onFindMissing} disabled={busy || deleting || locked}><MessageCirclePlus size={14} /></button> : null}
        <button className="icon-button tiny" type="button" title={manualReview ? '补充说明并重新生成' : '重新生成卡片'} aria-label={`${manualReview ? '补充说明并重新生成' : '重新生成'} ${material.title}`} onClick={onRegenerate} disabled={busy || deleting || locked}>{busy ? <Loader2 className="spin" size={14} /> : manualReview ? <AlertTriangle size={14} /> : <RefreshCw size={14} />}</button>
        <button className="icon-button tiny danger" type="button" title="移出复习中心" aria-label={`将 ${material.title} 移出复习中心`} onClick={onDelete} disabled={busy || deleting || locked}>{deleting ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button>
      </div>
      {showProgress
        ? <ReviewGenerationProgressPanel material={material} queuePosition={queuePosition} queueTotal={queueTotal} />
        : <p className="material-row-summary" title={summary}>{summary}</p>}
    </article>
  );
}

function ReviewGenerationProgressPanel({ material, queuePosition, queueTotal }: { material: ReviewMaterial; queuePosition: number | null; queueTotal: number }) {
  const normalizedStatus = (material.status || '').toUpperCase();
  const progress = material.generationProgress;
  const pending = normalizedStatus === 'PENDING';
  const terminalWithoutProgress = !progress && ['FAILED', 'NEEDS_REVIEW'].includes(normalizedStatus);
  const percent = terminalWithoutProgress ? 100 : pending && !progress ? 0 : clampNumber(progress?.percent ?? 0, 0, 100);
  const stageLabel = progress?.stageLabel || (pending ? '等待队列' : formatGenerationStatus(material.status));
  const queueLabel = queuePosition && queueTotal ? `当前位于队列第 ${queuePosition}/${queueTotal} 位` : '已进入串行生成队列';
  const message = progress?.message || (pending ? `${queueLabel}，前一份资料完成后会自动开始` : material.reason || '等待后端更新生成阶段');
  const events = (progress?.events || []).slice(-6).reverse();
  const detail = progress?.detail || (pending ? material.reason : null);
  const progressState = normalizedStatus === 'FAILED'
    ? 'failed'
    : normalizedStatus === 'NEEDS_REVIEW'
      ? 'manual'
      : normalizedStatus === 'GENERATING'
        ? 'running'
        : 'pending';
  return (
    <section className={`review-generation-progress is-${progressState}`} aria-label={`${material.title} 复习生成进度`}>
      <div className="review-generation-progress-head">
        <span>{progressState === 'running' ? <Loader2 className="spin" size={12} /> : <Clock3 size={12} />}{stageLabel}</span>
        <strong>{Math.round(percent)}%</strong>
      </div>
      <div className="review-generation-progress-bar" role="progressbar" aria-label="复习卡片生成进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <p>{message}</p>
      <div className="review-generation-progress-meta">
        {progress?.currentStep && progress.totalSteps ? <span>阶段 {progress.currentStep}/{progress.totalSteps}</span> : <span>{pending && queuePosition ? `队列 ${queuePosition}/${queueTotal}` : pending ? '等待自动开始' : terminalWithoutProgress ? '自动流程已结束' : '处理中'}</span>}
        {typeof progress?.attempt === 'number' && progress.maxAttempts ? <span>模型轮次 {progress.attempt}/{progress.maxAttempts}</span> : null}
        {progress?.createdAt ? <span>更新于 {formatTime(progress.createdAt)}</span> : null}
      </div>
      {detail || events.length ? (
        <details className="review-generation-progress-events">
          <summary>查看详细流程{events.length ? `（${events.length}）` : ''}</summary>
          {detail ? <p className="review-generation-progress-detail">{detail}</p> : null}
          {events.length ? <ol>{events.map((event, index) => <ReviewGenerationProgressEventRow key={`${event.stageCode}-${event.createdAt || index}-${index}`} event={event} />)}</ol> : null}
        </details>
      ) : null}
    </section>
  );
}

function ReviewGenerationProgressEventRow({ event }: { event: ReviewGenerationProgressEvent }) {
  return <li><span className={`event-dot ${progressEventClass(event.status)}`} /><div><strong>{event.stageLabel}</strong><small>{event.message}{event.attempt && event.maxAttempts ? ` · 第 ${event.attempt}/${event.maxAttempts} 轮` : ''}{event.createdAt ? ` · ${formatTime(event.createdAt)}` : ''}</small></div></li>;
}

function progressEventClass(status?: string | null) {
  const normalized = (status || '').toUpperCase();
  if (normalized === 'FAILED') return 'failed';
  if (normalized === 'NEEDS_REVIEW') return 'manual';
  if (normalized === 'COMPLETED' || normalized === 'SKIPPED') return 'completed';
  return 'running';
}

// 优先展示 DeepSeek 摘要；失败或跳过时直接展示后端原因，避免“摘要生成中”掩盖真实状态。
function materialSummary(summary?: string | null, reason?: string | null, status?: string): string {
  const normalizedSummary = summary?.replace(/\s+/g, ' ').trim();
  if (normalizedSummary) return normalizedSummary;
  const normalizedReason = reason?.replace(/\s+/g, ' ').trim();
  const normalizedStatus = (status || '').toUpperCase();
  if (normalizedReason && ['FAILED', 'NEEDS_REVIEW', 'SKIPPED', 'PENDING'].includes(normalizedStatus)) {
    return `${normalizedStatus === 'SKIPPED' ? '跳过原因' : normalizedStatus === 'NEEDS_REVIEW' ? '人工处理原因' : normalizedStatus === 'PENDING' ? '等待原因' : '失败原因'}：${normalizedReason}`;
  }
  if (normalizedStatus === 'PENDING') return '等待 DeepSeek 生成摘要';
  if (['GENERATING', 'RUNNING', 'PROCESSING'].includes(normalizedStatus)) return '摘要生成中';
  return '暂无复习摘要';
}

function EmptyReviewQueue({ onSync, syncing }: { onSync: () => void; syncing: boolean }) {
  return <div className="review-empty-queue"><div className="empty-queue-icon"><Check size={24} /></div><h3>今天没有到期卡片</h3><p>新的资料完成索引后会自动出现在这里</p><button className="outline-action" type="button" onClick={onSync} disabled={syncing}>{syncing ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}{syncing ? '同步中' : '检查新资料'}</button></div>;
}

function updateDraft(setter: Dispatch<SetStateAction<ReviewSettings>>, dirtyRef: MutableRefObject<boolean>, patch: Partial<ReviewSettings>) {
  dirtyRef.current = true;
  setter((previous) => normalizeSettings({ ...previous, ...patch }));
}

function omitKey<T>(value: Record<number, T>, key: number): Record<number, T> {
  const next = { ...value };
  delete next[key];
  return next;
}

function omitKeys<T>(value: Record<number, T>, keys: number[]): Record<number, T> {
  const removed = new Set(keys);
  return Object.fromEntries(Object.entries(value).filter(([key]) => !removed.has(Number(key)))) as Record<number, T>;
}

function omitMaterialCards(value: Record<number, ReviewCard>, materialIds: ReadonlySet<number>): Record<number, ReviewCard> {
  return Object.fromEntries(Object.entries(value).filter(([, card]) => !materialIds.has(card.materialId))) as Record<number, ReviewCard>;
}

function toggleSelected(value: Record<number, boolean>, id: number): Record<number, boolean> {
  if (value[id]) return omitKey(value, id);
  return { ...value, [id]: true };
}

function selectedIds(value: Record<number, boolean>): number[] {
  return Object.entries(value)
    .filter(([, selected]) => selected)
    .map(([id]) => Number(id))
    .filter(Number.isFinite)
    .sort((left, right) => left - right);
}

// 提取当前可见 group 的资料顺序，作为排序接口的完整批量载荷。
function materialOrder(groups: ReviewCardGroup[]): number[] {
  return groups.map((group) => group.materialId);
}

function reviewMaterialArchiveId(materialId: number): string {
  return `review-material-archive-${materialId}`;
}

// 将一个资料组移动到指定索引，卡片内容和对象引用保持不变。
function moveGroup(groups: ReviewCardGroup[], materialId: number, targetIndex: number): ReviewCardGroup[] {
  const sourceIndex = groups.findIndex((group) => group.materialId === materialId);
  if (sourceIndex < 0 || sourceIndex === targetIndex) return groups;
  const next = [...groups];
  const [moved] = next.splice(sourceIndex, 1);
  next.splice(Math.max(0, Math.min(next.length, targetIndex)), 0, moved);
  return next;
}

// 按服务端最近一次稳定顺序重排现有 group，未知或新出现的资料保持在末尾。
function orderGroupsByMaterialIds(groups: ReviewCardGroup[], materialIds: number[]): ReviewCardGroup[] {
  const rank = new Map(materialIds.map((materialId, index) => [materialId, index]));
  return groups
    .map((group, index) => ({ group, index }))
    .sort((left, right) => (rank.get(left.group.materialId) ?? Number.MAX_SAFE_INTEGER) - (rank.get(right.group.materialId) ?? Number.MAX_SAFE_INTEGER) || left.index - right.index)
    .map(({ group }) => group);
}

function sameNumberOrder(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function deletionKey(target: ReviewDeleteTarget): string {
  if (target.scope === 'CARD') return `CARD:${target.card.id}`;
  if (target.scope === 'MATERIAL') return `MATERIAL:${target.materialId}`;
  return target.scope;
}

function normalizeSettings(settings?: Partial<ReviewSettings> | null): ReviewSettings {
  return {
    enabled: settings?.enabled ?? DEFAULT_SETTINGS.enabled,
    desiredRetention: clampNumber(settings?.desiredRetention ?? DEFAULT_SETTINGS.desiredRetention, 0.8, 0.97),
    dailyLimit: clampInteger(settings?.dailyLimit ?? DEFAULT_SETTINGS.dailyLimit, 1, 100),
    reminderTime: settings?.reminderTime || DEFAULT_SETTINGS.reminderTime,
    timezone: settings?.timezone || DEFAULT_SETTINGS.timezone
  };
}

function resolveMaterialId(material: ReviewMaterial) {
  const value = material.materialId ?? material.id;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function formatSyncMessage(result: ReviewSyncResult) {
  if (!result.processedMaterialCount) return '当前没有等待生成的资料';
  const details = [result.skippedMaterialCount ? `跳过 ${result.skippedMaterialCount} 份非学习资料` : '', result.failedMaterialCount ? `${result.failedMaterialCount} 份处理失败` : ''].filter(Boolean);
  return `已处理 ${result.processedMaterialCount} 份资料，生成 ${result.generatedCardCount} 张卡片${details.length ? `，${details.join('，')}` : ''}`;
}

type ReviewSyncProgressHandler = (result: ReviewSyncResult) => void | Promise<void>;

// 用多个单资料请求串行排空 Prompt 升级后的待生成队列，并在每份完成后刷新进度。
async function drainPendingReviewMaterials(onProgress?: ReviewSyncProgressHandler): Promise<ReviewSyncResult> {
  const total: ReviewSyncResult = {
    processedMaterialCount: 0,
    generatedCardCount: 0,
    skippedMaterialCount: 0,
    failedMaterialCount: 0
  };
  for (let index = 0; index < MAX_AUTOMATIC_REVIEW_SYNC_COUNT; index += 1) {
    const current = await syncReviewMaterials(1);
    if (!current.processedMaterialCount) break;
    total.processedMaterialCount += current.processedMaterialCount;
    total.generatedCardCount += current.generatedCardCount;
    total.skippedMaterialCount += current.skippedMaterialCount;
    total.failedMaterialCount += current.failedMaterialCount;
    if (onProgress) await onProgress({ ...total });
    const reachedTerminalState = current.generatedCardCount > 0
      || current.skippedMaterialCount > 0
      || current.failedMaterialCount > 0;
    if (!reachedTerminalState) break;
  }
  return total;
}

function formatGradeMessage(result: ReviewGradeResult, rating: ReviewRating) {
  const label = RATING_OPTIONS.find((item) => item.rating === rating)?.label || '已评分';
  return `${label}已记录 · 下次复习 ${result.nextDueAt ? formatDateTime(result.nextDueAt) : '待排程'} · 间隔 ${formatInterval(result.intervalDays)}`;
}

function formatInterval(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '小于 1 天';
  if (value < 1) return `${Math.max(1, Math.round(value * 24))} 小时`;
  return `${Number(value.toFixed(value >= 10 ? 0 : 1))} 天`;
}

function formatRetention(value: number) {
  return `${Math.round((value <= 1 ? value : value / 100) * 100)}%`;
}

function formatDueDate(value?: string | null) {
  if (!value) return '待复习';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '待复习';
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function formatDateTime(value?: string | null) {
  if (!value) return '暂无';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function formatTime(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function formatDocumentType(value?: string | null) {
  const normalized = (value || '').toLowerCase();
  if (isVideoType(normalized)) return '视频';
  if (normalized === 'pdf') return 'PDF';
  if (normalized === 'markdown' || normalized === 'md') return '笔记';
  if (normalized === 'doc' || normalized === 'docx') return '文档';
  return value ? value.toUpperCase() : '资料';
}

function formatReviewClassification(value?: string | null, isLearningContent?: boolean | null) {
  if (isLearningContent === false) return '非学习内容';
  const normalized = (value || '').toUpperCase();
  if (normalized === 'INTERVIEW_PREP') return '八股与面经';
  if (normalized === 'COURSE') return '课程讲解';
  if (normalized === 'TECHNICAL_KNOWLEDGE') return '技术知识';
  if (normalized === 'STUDY_NOTES') return '学习笔记';
  if (isLearningContent === true) return value || '学习内容';
  return value || '待分类';
}

function formatGenerationStatus(value?: string | null) {
  const normalized = (value || '').toUpperCase();
  if (normalized === 'GENERATED' || normalized === 'READY' || normalized === 'SUCCESS') return '已生成';
  if (normalized === 'GENERATING' || normalized === 'RUNNING' || normalized === 'PROCESSING') return '生成中';
  if (normalized === 'PENDING') return '等待生成';
  if (normalized === 'SKIPPED') return '已跳过';
  if (normalized === 'FAILED') return '失败';
  if (normalized === 'NEEDS_REVIEW') return '待人工处理';
  return value || '待处理';
}

function statusClass(value?: string | null) {
  const normalized = (value || '').toUpperCase();
  if (normalized === 'FAILED') return 'failed';
  if (normalized === 'NEEDS_REVIEW') return 'manual-review';
  if (normalized === 'SKIPPED') return 'warning';
  if (normalized === 'GENERATING' || normalized === 'RUNNING' || normalized === 'PROCESSING') return 'running';
  if (normalized === 'PENDING') return 'pending';
  return 'indexed';
}

function isVideoType(value?: string | null) {
  return /^(mp4|mov|m4v|webm|mkv|avi)$/i.test(value || '') || Boolean(value && /video/i.test(value));
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

function clampInteger(value: string | number, min: number, max: number) {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return min;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function readNotificationPermission(): NotificationPermission {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'default';
  return Notification.permission;
}

function publishOverviewEvent() {
  window.dispatchEvent(new Event(REVIEW_OVERVIEW_UPDATED_EVENT));
}
