import {
  AlertTriangle,
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
  Loader2,
  RefreshCw,
  Save,
  Settings2,
  Target,
  Trash2,
  X
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type Dispatch, type FormEvent, type MutableRefObject, type SetStateAction } from 'react';
import { MarkdownText } from '../../components/MarkdownText';
import {
  REVIEW_OVERVIEW_UPDATED_EVENT,
  REVIEW_CONTENT_UPDATED_EVENT,
  deleteReviewCard,
  deleteReviewCards,
  deleteReviewMaterial,
  deleteReviewMaterials,
  fetchDueReviewGroups,
  fetchReviewCard,
  fetchReviewMaterials,
  fetchReviewOverview,
  generateReviewMaterial,
  gradeReviewCard,
  syncReviewMaterials,
  updateReviewSettings,
  type ReviewCard,
  type ReviewCardGroup,
  type ReviewGradeResult,
  type ReviewMaterial,
  type ReviewOverview,
  type ReviewSettings,
  type ReviewSyncResult
} from '../../api/reviews';
import { buildEvidenceOpenHref } from '../../utils/evidenceLinks';
import type { RagEvidence } from '../../api/types';
import { MATERIAL_UPLOADED_EVENT } from '../../hooks/useMaterialUpload';
import '../../styles/ReviewCenter.css';

type ReviewRating = 1 | 2 | 3 | 4;
type ReviewDeleteTarget =
  | { scope: 'CARD'; card: ReviewCard }
  | { scope: 'MATERIAL'; materialId: number; title: string }
  | { scope: 'CARD_BATCH'; cardIds: number[] }
  | { scope: 'MATERIAL_BATCH'; materialIds: number[]; titles: string[] };

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

// 复习中心按上传资料展示每日到期 group，每张小卡片独立揭示、定位和评分。
export function ReviewCenter() {
  const [overview, setOverview] = useState<ReviewOverview | null>(null);
  const [groups, setGroups] = useState<ReviewCardGroup[]>([]);
  const [materials, setMaterials] = useState<ReviewMaterial[]>([]);
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
  const settingsDirtyRef = useRef(false);
  const syncPromiseRef = useRef<Promise<ReviewSyncResult> | null>(null);
  const reviewStartedAtRef = useRef<Record<number, number>>({});

  const dueCount = overview?.actionableDueCount ?? groups.reduce((count, group) => count + group.dueCardCount, 0);
  const dailyLimit = overview?.settings.dailyLimit || settingsDraft.dailyLimit || 20;
  const reviewProgress = Math.min(100, Math.round(((overview?.todayReviewedCount || 0) / Math.max(1, dailyLimit)) * 100));
  const selectedCardIdList = selectedIds(selectedCardIds);
  const selectedMaterialIdList = selectedIds(selectedMaterialIds);

  // 同时读取概览、分组队列和资料状态，单个区域失败时保留其余数据。
  const loadData = useCallback(async () => {
    const results = await Promise.allSettled([
      fetchReviewOverview(),
      fetchDueReviewGroups(100),
      fetchReviewMaterials()
    ]);
    const failures: string[] = [];
    const overviewResult = results[0];
    const groupsResult = results[1];
    const materialsResult = results[2];
    if (overviewResult.status === 'fulfilled') {
      setOverview(overviewResult.value);
      if (!settingsDirtyRef.current) setSettingsDraft(normalizeSettings(overviewResult.value.settings));
      publishOverviewEvent();
    } else {
      failures.push('复习概览');
    }
    if (groupsResult.status === 'fulfilled') {
      setGroups(groupsResult.value.groups);
    } else {
      failures.push('复习卡片');
    }
    if (materialsResult.status === 'fulfilled') {
      setMaterials(materialsResult.value);
    } else {
      failures.push('资料状态');
    }
    if (failures.length === 3) throw new Error('复习数据加载失败，请稍后重试');
    setError(failures.length ? `${failures.join('、')}暂时不可用` : '');
  }, []);

  // 页面首次打开时增量同步已完成 RAG 入库的资料，再读取最新队列。
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
      if (!syncPromiseRef.current) syncPromiseRef.current = syncReviewMaterials(1);
      setSyncing(true);
      try {
        const result = await syncPromiseRef.current;
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
  }, [loadData]);

  // 上传资料完成 RAG 入库并生成卡片后，立即刷新当前复习中心，不等待定时轮询。
  useEffect(() => {
    const refreshGeneratedMaterial = () => {
      void loadData().catch(() => undefined);
    };
    window.addEventListener(REVIEW_CONTENT_UPDATED_EVENT, refreshGeneratedMaterial);
    return () => window.removeEventListener(REVIEW_CONTENT_UPDATED_EVENT, refreshGeneratedMaterial);
  }, [loadData]);

  // 到期时间和评分日志由服务端维护，页面定时或重新聚焦时刷新概览与分组队列。
  useEffect(() => {
    const refresh = () => {
      void Promise.allSettled([fetchReviewOverview(), fetchDueReviewGroups(100)]).then(([overviewResult, groupsResult]) => {
        if (overviewResult.status === 'fulfilled') {
          setOverview(overviewResult.value);
          if (!settingsDirtyRef.current) setSettingsDraft(normalizeSettings(overviewResult.value.settings));
        }
        if (groupsResult.status === 'fulfilled') setGroups(groupsResult.value.groups);
      });
    };
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener('focus', refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
    };
  }, []);

  // 上传新资料后只触发一次增量同步，不在普通概览轮询中重复调用模型。
  useEffect(() => {
    const onMaterialUploaded = () => void runSync();
    window.addEventListener(MATERIAL_UPLOADED_EVENT, onMaterialUploaded);
    return () => window.removeEventListener(MATERIAL_UPLOADED_EVENT, onMaterialUploaded);
  }, [syncing]);

  async function runSync() {
    if (syncing) return;
    setSyncing(true);
    setError('');
    try {
      const result = await syncReviewMaterials(1);
      setSyncMessage(formatSyncMessage(result));
      await loadData();
    } catch (syncError) {
      setError(syncError instanceof Error ? syncError.message : '学习资料同步失败');
    } finally {
      setSyncing(false);
    }
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
    const results = await Promise.allSettled([
      fetchReviewOverview(),
      fetchDueReviewGroups(100),
      fetchReviewMaterials()
    ]);
    if (results[0].status === 'fulfilled') {
      setOverview(results[0].value);
      if (!settingsDirtyRef.current) setSettingsDraft(normalizeSettings(results[0].value.settings));
      publishOverviewEvent();
    }
    if (results[1].status === 'fulfilled') setGroups(results[1].value.groups);
    if (results[2].status === 'fulfilled') setMaterials(results[2].value);
  }

  function removeCards(cardIds: number[]) {
    const removed = new Set(cardIds);
    setGroups((previous) => previous
      .map((group) => {
        const cards = group.cards.filter((card) => !removed.has(card.id));
        return { ...group, cards, dueCardCount: cards.length };
      })
      .filter((group) => group.cards.length > 0));
  }

  function removeMaterialsFromView(materialIds: number[]) {
    const removed = new Set(materialIds);
    setGroups((previous) => previous.filter((group) => !removed.has(group.materialId)));
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

  async function regenerateMaterial(material: ReviewMaterial) {
    const materialId = resolveMaterialId(material);
    if (materialId == null || busyMaterialId !== null) return;
    setBusyMaterialId(materialId);
    setError('');
    try {
      await generateReviewMaterial(materialId);
      setSyncMessage(`“${material.title}”已重新生成复习卡片`);
      await loadData();
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : '复习卡片生成失败');
    } finally {
      setBusyMaterialId(null);
    }
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

  return (
    <div className="review-center-page">
      <header className="review-page-header">
        <div>
          <div className="page-eyebrow"><Target size={14} />每日复习</div>
          <h2>复习中心</h2>
          <p>按资料整理的到期知识点</p>
        </div>
        <div className="review-header-actions">
          <button className="outline-action" type="button" onClick={() => void runSync()} disabled={syncing}>
            {syncing ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            {syncing ? '同步中' : '同步资料'}
          </button>
          <button className="icon-button compact" type="button" title="刷新复习数据" aria-label="刷新复习数据" onClick={() => void loadData()}>
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

      <section className="review-stat-strip" aria-label="复习统计">
        <div className="review-stat primary"><span>今日待复习</span><strong>{dueCount}</strong><small>{overview && overview.dueCount > dueCount ? `到期积压 ${overview.dueCount} 张` : totalCards ? `当前展示 ${totalCards} 张` : '队列已清空'}</small></div>
        <div className="review-stat"><span>今日已完成</span><strong>{overview?.todayReviewedCount ?? '--'}</strong><small>每日上限 {dailyLimit} 张</small></div>
        <div className="review-stat"><span>学习资料</span><strong>{overview?.activeMaterialCount ?? '--'}</strong><small>已生成复习卡片的资料</small></div>
        <div className="review-stat progress-stat"><div><span>今日进度</span><strong>{reviewProgress}%</strong></div><div className="review-progress"><i style={{ width: `${reviewProgress}%` }} /></div><small>{overview?.nextDueAt ? `下一张 ${formatTime(overview.nextDueAt)}` : '暂无下一张'}</small></div>
      </section>

      <div className="review-content-grid">
        <section className="review-queue-column" aria-labelledby="review-queue-title">
          <div className="review-section-heading"><div><h3 id="review-queue-title">今日卡片</h3><span>{groups.length ? `${groups.length} 份资料` : '暂无到期资料'}</span></div><div className="review-section-actions">{selectedCardIdList.length ? <button className="outline-action small danger-outline" type="button" onClick={requestCardBatchDeletion} disabled={deletingKey !== null}><Trash2 size={14} />删除选中 {selectedCardIdList.length}</button> : <Clock3 size={18} />}</div></div>
          {loading ? <div className="review-loading"><Loader2 className="spin" size={22} /><span>正在读取复习队列</span></div> : null}
          {!loading && groups.length === 0 ? <EmptyReviewQueue onSync={() => void runSync()} syncing={syncing} /> : null}
          {!loading && groups.length > 0 ? groups.map((group) => (
            <ReviewMaterialGroup
              key={group.materialId}
              group={group}
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
              onToggleSelected={(cardId) => setSelectedCardIds((previous) => toggleSelected(previous, cardId))}
            />
          )) : null}
        </section>

        <aside className="review-side-column">
          <section className="review-panel settings-panel">
            <div className="review-panel-heading"><div><Settings2 size={17} /><h3>复习设置</h3></div><span>{settingsDraft.enabled ? '已开启' : '已暂停'}</span></div>
            <form onSubmit={saveSettings} className="review-settings-form">
              <label className="review-toggle-row"><span><strong>每日提醒</strong><small>到期后显示浏览器提醒</small></span><input type="checkbox" checked={settingsDraft.enabled} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { enabled: event.target.checked })} /></label>
              <label><span>目标记忆率</span><div className="input-with-suffix"><input type="number" min="0.8" max="0.97" step="0.01" value={settingsDraft.desiredRetention} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { desiredRetention: Number(event.target.value) })} /><em>{formatRetention(settingsDraft.desiredRetention)}</em></div></label>
              <label><span>每日上限</span><div className="input-with-suffix"><input type="number" min="1" max="100" step="1" value={settingsDraft.dailyLimit} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { dailyLimit: Number(event.target.value) })} /><em>张</em></div></label>
              <label><span>提醒时间</span><input type="time" value={settingsDraft.reminderTime} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { reminderTime: event.target.value })} /></label>
              <label><span>时区</span><select value={settingsDraft.timezone} onChange={(event) => updateDraft(setSettingsDraft, settingsDirtyRef, { timezone: event.target.value })}>{TIMEZONE_OPTIONS.map((timezone) => <option key={timezone} value={timezone}>{timezone}</option>)}</select></label>
              <button className="primary-action full" type="submit" disabled={settingsSaving}>{settingsSaving ? <Loader2 className="spin" size={16} /> : <Save size={16} />}{settingsSaving ? '保存中' : '保存设置'}</button>
              {settingsMessage ? <p className="form-message">{settingsMessage}</p> : null}
            </form>
            <button className="notification-action" type="button" onClick={() => void requestBrowserNotification()} disabled={notificationPermission === 'granted'}>{notificationPermission === 'granted' ? <BellRing size={16} /> : <Bell size={16} />}{notificationPermission === 'granted' ? '浏览器提醒已开启' : '开启浏览器提醒'}</button>
          </section>

          <section className="review-panel materials-panel">
            <div className="review-panel-heading"><div><BookOpen size={17} /><h3>资料分组</h3></div><span>{materials.length}</span></div>
            {selectedMaterialIdList.length ? <div className="review-material-bulkbar"><span>已选 {selectedMaterialIdList.length} 份</span><button className="outline-action small danger-outline" type="button" onClick={requestMaterialBatchDeletion} disabled={deletingKey !== null}><Trash2 size={14} />批量移出</button></div> : null}
            <div className="review-material-list">
              {materials.length ? materials.map((material) => {
                const materialId = resolveMaterialId(material);
                return <ReviewMaterialRow key={materialId ?? material.title} material={material} selected={materialId != null && Boolean(selectedMaterialIds[materialId])} busy={busyMaterialId === materialId} deleting={materialId != null && deletingKey === `MATERIAL:${materialId}`} onToggleSelected={() => { if (materialId != null) setSelectedMaterialIds((previous) => toggleSelected(previous, materialId)); }} onRegenerate={() => void regenerateMaterial(material)} onDelete={() => { if (materialId != null) requestMaterialDeletion(materialId, material.title); }} />;
              }) : <p className="panel-empty">暂无已索引资料</p>}
            </div>
          </section>
        </aside>
      </div>

      <OriginalEvidenceDialog card={originalCard} onClose={() => setOriginalCard(null)} />
      <ReviewDeletionDialog target={deleteTarget} deleting={deletingKey !== null} onConfirm={() => void confirmDeletion()} onClose={() => { if (deletingKey === null) setDeleteTarget(null); }} />
    </div>
  );
}

function ReviewMaterialGroup({
  group,
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
  onToggleSelected
}: {
  group: ReviewCardGroup;
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
  onToggleSelected: (cardId: number) => void;
}) {
  return (
    <section className="review-material-group">
      <header className="review-group-header">
        <div className="review-group-title"><span className="material-type-icon">{isVideoType(group.documentType) ? <FileVideo2 size={17} /> : <FileText size={17} />}</span><div><h4>{group.materialTitle}</h4><span>{formatDocumentType(group.documentType)} · {group.dueCardCount} 张到期</span></div></div>
        <div className="review-group-actions"><span className="group-count">{group.cards.length}</span><button className="icon-button tiny danger" type="button" title="将资料移出复习中心" aria-label={`将 ${group.materialTitle} 移出复习中心`} onClick={onDeleteMaterial} disabled={deletingKey !== null}>{deletingKey === `MATERIAL:${group.materialId}` ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button></div>
      </header>
      <div className="review-card-grid">
        {group.cards.map((card) => <ReviewQuestionCard key={card.id} card={card} revealed={revealedCards[card.id]} selected={Boolean(selectedCardIds[card.id])} showHint={Boolean(hintCardIds[card.id])} revealLoading={revealLoadingId === card.id} grading={gradingId === card.id} deleting={deletingKey === `CARD:${card.id}`} onToggleSelected={() => onToggleSelected(card.id)} onReveal={() => onReveal(card)} onHide={() => onHide(card.id)} onToggleHint={() => onToggleHint(card.id)} onOriginal={() => onOriginal(revealedCards[card.id] || card)} onGrade={(rating) => onGrade(card, rating)} onDelete={() => onDeleteCard(card)} />)}
      </div>
    </section>
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
      <div className="review-card-meta"><div className="review-card-meta-leading"><input type="checkbox" checked={selected} onChange={onToggleSelected} aria-label={`选择卡片：${card.question}`} /><span>知识点 {card.reviewCount > 0 ? `· 已复习 ${card.reviewCount} 次` : '· 首次复习'}</span></div><div className="review-card-meta-actions"><time>{formatDueDate(card.dueAt)}</time><button className="icon-button tiny danger" type="button" title="删除卡片" aria-label={`删除卡片：${card.question}`} onClick={onDelete} disabled={deleting}>{deleting ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button></div></div>
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

function ReviewMaterialRow({ material, selected, busy, deleting, onToggleSelected, onRegenerate, onDelete }: { material: ReviewMaterial; selected: boolean; busy: boolean; deleting: boolean; onToggleSelected: () => void; onRegenerate: () => void; onDelete: () => void }) {
  return <div className={`review-material-row${selected ? ' is-selected' : ''}`}><input className="material-row-selector" type="checkbox" checked={selected} onChange={onToggleSelected} aria-label={`选择资料：${material.title}`} /><div className="material-row-icon">{isVideoType(material.documentType) ? <FileVideo2 size={15} /> : <FileText size={15} />}</div><div className="material-row-copy"><strong title={material.title}>{material.title}</strong><span>{formatReviewClassification(material.category, material.isLearningContent)} · {material.cardCount} 张卡片</span></div><div className={`material-status ${statusClass(material.status)}`}>{formatGenerationStatus(material.status)}</div><div className="material-row-actions"><button className="icon-button tiny" type="button" title="重新生成卡片" aria-label={`重新生成 ${material.title}`} onClick={onRegenerate} disabled={busy || deleting}>{busy ? <Loader2 className="spin" size={14} /> : <RefreshCw size={14} />}</button><button className="icon-button tiny danger" type="button" title="移出复习中心" aria-label={`将 ${material.title} 移出复习中心`} onClick={onDelete} disabled={busy || deleting}>{deleting ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button></div></div>;
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
  const details = [result.skippedMaterialCount ? `跳过 ${result.skippedMaterialCount} 份非学习资料` : '', result.failedMaterialCount ? `${result.failedMaterialCount} 份处理失败` : ''].filter(Boolean);
  return `已同步 ${result.processedMaterialCount} 份资料，生成 ${result.generatedCardCount} 张卡片${details.length ? `，${details.join('，')}` : ''}`;
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
  if (normalized === 'GENERATING' || normalized === 'RUNNING' || normalized === 'PROCESSING' || normalized === 'PENDING') return '生成中';
  if (normalized === 'SKIPPED') return '已跳过';
  if (normalized === 'FAILED') return '失败';
  return value || '待处理';
}

function statusClass(value?: string | null) {
  const normalized = (value || '').toUpperCase();
  if (normalized === 'FAILED') return 'failed';
  if (normalized === 'SKIPPED') return 'warning';
  if (normalized === 'GENERATING' || normalized === 'RUNNING' || normalized === 'PROCESSING' || normalized === 'PENDING') return 'running';
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
