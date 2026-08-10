import {
  Check,
  ChevronRight,
  CircleAlert,
  FileSearch,
  Layers3,
  Loader2,
  Merge,
  RotateCcw,
  Sparkles,
  Trash2,
  X
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import {
  fetchLatestReviewSegmentTask,
  fetchReviewSegmentTask,
  fetchReviewSegmentWorkspace,
  mergeReviewSegments,
  startReviewSegmentTask,
  type ReviewMaterialCardSnapshot,
  type ReviewMaterialRewriteApplyResult,
  type ReviewSegmentGenerationTask,
  type ReviewSegmentResult,
  type ReviewSegmentWorkspace
} from '../../api/reviews';

export interface ReviewSegmentWorkspaceTarget {
  materialId: number;
  title: string;
  cardCount: number;
  initialPrompt?: string;
}

interface ReviewSegmentWorkspaceDialogProps {
  target: ReviewSegmentWorkspaceTarget | null;
  onClose: () => void;
  onApplied: (result: ReviewMaterialRewriteApplyResult) => void | Promise<void>;
}

interface StoredSegmentDraft {
  sourceVersion: number;
  prompts: Record<string, string>;
  mode: 'STANDARD' | 'RELAXED';
  results: Record<string, ReviewSegmentResult>;
  drafts: Record<string, ReviewMaterialCardSnapshot[]>;
  included: Record<string, boolean>;
  summary: string;
}

const DEFAULT_SEGMENT_PROMPT = '请模拟真实 Java 面试官进行提问，问题要自然、直接、有追问感；完整保留本段独立知识点，避免“说明、列出、概括”等教材任务式表述。';

// 分段工作台把模型生成变为用户可观察、可中断、可编辑的候选流程，最终确认前不改正式卡片。
export function ReviewSegmentWorkspaceDialog({ target, onClose, onApplied }: ReviewSegmentWorkspaceDialogProps) {
  const [workspace, setWorkspace] = useState<ReviewSegmentWorkspace | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<'STANDARD' | 'RELAXED'>('RELAXED');
  const [results, setResults] = useState<Record<string, ReviewSegmentResult>>({});
  const [drafts, setDrafts] = useState<Record<string, ReviewMaterialCardSnapshot[]>>({});
  const [included, setIncluded] = useState<Record<string, boolean>>({});
  const [summary, setSummary] = useState('');
  const [task, setTask] = useState<ReviewSegmentGenerationTask | null>(null);
  const [starting, setStarting] = useState(false);
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState('');
  const [clockNow, setClockNow] = useState(() => Date.now());
  const handledTaskIdsRef = useRef(new Set<string>());
  const busy = starting || task?.status === 'QUEUED' || task?.status === 'RUNNING';

  useEffect(() => {
    if (!busy) return undefined;
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  // 将某轮结果并入已有草稿；未选中的旧分段不会被新任务清空。
  function absorbTaskResult(nextTask: ReviewSegmentGenerationTask) {
    if (!nextTask.result || handledTaskIdsRef.current.has(nextTask.taskId)) return;
    handledTaskIdsRef.current.add(nextTask.taskId);
    const nextResults = nextTask.result.segments;
    setResults((previous) => ({
      ...previous,
      ...Object.fromEntries(nextResults.map((item) => [item.segmentId, item]))
    }));
    setDrafts((previous) => {
      const next = { ...previous };
      nextResults.forEach((item) => {
        if (item.status === 'SUCCEEDED') next[item.segmentId] = item.cards;
      });
      return next;
    });
    setIncluded((previous) => {
      const next = { ...previous };
      nextResults.forEach((item) => {
        if (item.status === 'SUCCEEDED' && !(item.segmentId in next)) next[item.segmentId] = true;
      });
      return next;
    });
    setSelected((previous) => ({
      ...previous,
      ...Object.fromEntries(nextResults.map((item) => [item.segmentId, false]))
    }));
    const generatedSummaries = nextResults
      .map((item) => item.summary?.trim())
      .filter((item): item is string => Boolean(item));
    if (generatedSummaries.length) {
      setSummary((previous) => mergeSummaryText(previous, generatedSummaries));
    }
  }

  useEffect(() => {
    let active = true;
    handledTaskIdsRef.current = new Set<string>();
    setWorkspace(null);
    setSelected({});
    setPrompts({});
    setMode('RELAXED');
    setResults({});
    setDrafts({});
    setIncluded({});
    setSummary('');
    setTask(null);
    setError('');
    if (!target) return () => { active = false; };
    setLoading(true);
    void Promise.all([
      fetchReviewSegmentWorkspace(target.materialId),
      fetchLatestReviewSegmentTask(target.materialId)
    ]).then(([nextWorkspace, latestTask]) => {
      if (!active) return;
      setWorkspace(nextWorkspace);
      const initialPrompts = Object.fromEntries(nextWorkspace.segments.map((segment) => [
        segment.segmentId,
        defaultPrompt(target.initialPrompt)
      ]));
      const stored = readStoredDraft(target.materialId, nextWorkspace.sourceVersion);
      setPrompts(stored?.prompts || initialPrompts);
      setMode(stored?.mode || 'RELAXED');
      setResults(stored?.results || {});
      setDrafts(stored?.drafts || {});
      setIncluded(stored?.included || {});
      setSummary(stored?.summary || nextWorkspace.originalSummary || '');
      const firstUnfinished = nextWorkspace.segments.find((segment) => !stored?.drafts?.[segment.segmentId]?.length);
      setSelected(firstUnfinished ? { [firstUnfinished.segmentId]: true } : {});
      if (latestTask) {
        setTask(latestTask);
        if (latestTask.status === 'SUCCEEDED') absorbTaskResult(latestTask);
        if (latestTask.status === 'FAILED') setError(latestTask.error || '最近一次分段生成失败，可重新选择分段生成');
      }
    }).catch((loadError) => {
      if (active) setError(loadError instanceof Error ? loadError.message : '分段原文读取失败');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [target?.materialId, target?.initialPrompt]);

  // 多轮候选只保存在当前资料版本下，资料重新索引后不会误用旧草稿。
  useEffect(() => {
    if (!target || !workspace || loading) return;
    const stored: StoredSegmentDraft = {
      sourceVersion: workspace.sourceVersion,
      prompts,
      mode,
      results,
      drafts,
      included,
      summary
    };
    try {
      window.localStorage.setItem(segmentDraftKey(target.materialId), JSON.stringify(stored));
    } catch {
      // 浏览器禁用或空间不足时仅影响跨弹窗恢复，不影响本轮生成与合并。
    }
  }, [drafts, included, loading, mode, prompts, results, summary, target?.materialId, workspace]);

  useEffect(() => {
    if (!target) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !merging) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [merging, onClose, target]);

  useEffect(() => {
    if (!target || !task || !['QUEUED', 'RUNNING'].includes(task.status)) return undefined;
    let active = true;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const nextTask = await fetchReviewSegmentTask(target.materialId, task.taskId);
        if (!active) return;
        setTask(nextTask);
        if (nextTask.status === 'SUCCEEDED') {
          absorbTaskResult(nextTask);
        } else if (nextTask.status === 'FAILED') {
          setError(nextTask.error || '分段生成失败，可调整提示词后重试');
        } else {
          timer = window.setTimeout(() => void poll(), 1200);
        }
      } catch (pollError) {
        if (!active) return;
        const message = pollError instanceof Error ? pollError.message : '分段任务进度读取失败';
        if (/任务不存在|任务已过期|已过期/.test(message)) {
          // API 进程重启后进程内任务表会清空；停止轮询并释放按钮，用户可重新选择分段生成。
          setTask((previous) => previous ? { ...previous, status: 'FAILED', error: '后台服务已重启，旧分段任务已失效，请重新选择分段生成' } : previous);
          setError('后台服务已重启，旧分段任务已失效，请重新选择分段生成');
          return;
        }
        setError(message);
        timer = window.setTimeout(() => void poll(), 2000);
      }
    };
    timer = window.setTimeout(() => void poll(), 300);
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [target?.materialId, task?.taskId, task?.status]);

  const selectedIds = useMemo(
    () => workspace?.segments.filter((segment) => selected[segment.segmentId]).map((segment) => segment.segmentId) || [],
    [selected, workspace]
  );
  const candidateCards = useMemo(
    () => workspace?.segments.flatMap((segment) => included[segment.segmentId] ? (drafts[segment.segmentId] || []) : []) || [],
    [drafts, included, workspace]
  );
  const generatedSegmentCount = workspace?.segments.filter((segment) => (drafts[segment.segmentId] || []).length > 0).length || 0;
  const invalidCandidate = candidateCards.some((card) => !card.content.question.trim() || !card.content.answer.trim() || !card.evidenceIds.length);

  if (!target) return null;

  async function generateSegments(segmentIds = selectedIds, forceRestart = false) {
    if (!workspace || !segmentIds.length || (busy && !forceRestart) || merging) return;
    setStarting(true);
    setError('');
    try {
      const nextTask = await startReviewSegmentTask(target!.materialId, {
        segmentIds,
        prompts: Object.fromEntries(segmentIds.map((segmentId) => [segmentId, prompts[segmentId] || DEFAULT_SEGMENT_PROMPT])),
        mode,
        forceRestart
      });
      setTask(nextTask);
      if (nextTask.status === 'SUCCEEDED') absorbTaskResult(nextTask);
      if (nextTask.status === 'FAILED') setError(nextTask.error || '分段生成失败，可调整提示词后重试');
    } catch (generationError) {
      setError(generationError instanceof Error ? generationError.message : '分段生成任务创建失败');
    } finally {
      setStarting(false);
    }
  }

  function selectUnfinishedSegments() {
    if (!workspace) return;
    setSelected(Object.fromEntries(workspace.segments.map((segment) => [
      segment.segmentId,
      !(drafts[segment.segmentId] || []).length
    ])));
  }

  function resetDraft() {
    if (!workspace) return;
    try {
      window.localStorage.removeItem(segmentDraftKey(target!.materialId));
    } catch {
      // 清理失败不阻塞当前内存草稿重置。
    }
    setResults({});
    setDrafts({});
    setIncluded({});
    setSummary(workspace.originalSummary || '');
    setTask(null);
    setError('');
    setSelected(workspace.segments[0] ? { [workspace.segments[0].segmentId]: true } : {});
  }

  async function mergeCandidates() {
    if (!workspace || !candidateCards.length || invalidCandidate || merging || busy) return;
    setMerging(true);
    setError('');
    try {
      const applied = await mergeReviewSegments(target!.materialId, {
        sourceVersion: workspace.sourceVersion,
        originalFingerprint: workspace.originalFingerprint,
        originalCardIds: workspace.originalCardIds,
        proposedSummary: summary.trim() || null,
        proposedCards: candidateCards.map((card) => ({
          question: card.content.question.trim(),
          answer: card.content.answer.trim(),
          hint: card.content.hint?.trim() || null,
          rewriteMode: 'STRICT_SOURCE',
          evidenceIds: card.evidenceIds
        }))
      });
      try {
        window.localStorage.removeItem(segmentDraftKey(target!.materialId));
      } catch {
        // 正式发布成功后，浏览器草稿清理失败不影响服务端结果。
      }
      await onApplied(applied);
      onClose();
    } catch (mergeError) {
      setError(mergeError instanceof Error ? mergeError.message : '分段候选合并失败');
    } finally {
      setMerging(false);
    }
  }

  return (
    <div className="review-delete-overlay review-segment-overlay" role="dialog" aria-modal="true" aria-labelledby="review-segment-workspace-title" onMouseDown={(event) => { if (event.target === event.currentTarget && !merging) onClose(); }}>
      <section className="review-segment-dialog" aria-busy={loading || busy || merging}>
        <header className="review-card-editor-header review-segment-header">
          <div><span className="review-card-editor-icon is-ai"><Layers3 size={18} /></span><div><h3 id="review-segment-workspace-title">交互式分段生成</h3><p>{target.title} · 先看原文、再选择分段；确认合并前不会覆盖当前 {target.cardCount} 张正式卡片。</p></div></div>
          <button className="icon-button compact" type="button" onClick={onClose} disabled={merging} title={busy ? '保留草稿并在后台继续' : '保留草稿并关闭'} aria-label={busy ? '保留草稿并在后台继续' : '保留草稿并关闭'}><X size={18} /></button>
        </header>

        {error ? <div className="review-alert danger" role="alert"><CircleAlert size={16} />{error}</div> : null}
        {loading ? <div className="review-segment-loading"><Loader2 className="spin" size={20} />正在整理原始 evidence 分段</div> : null}

        {workspace ? <>
          <div className="review-segment-toolbar">
            <div className="review-segment-metrics" aria-label="分段工作台统计"><span><strong>{workspace.segments.length}</strong> 个原文分段</span><span><strong>{generatedSegmentCount}</strong> 个已生成</span><span><strong>{candidateCards.length}</strong> 张参与合并</span></div>
            <div className="review-segment-mode" role="radiogroup" aria-label="分段生成质量门禁">
              <button type="button" role="radio" aria-checked={mode === 'RELAXED'} className={mode === 'RELAXED' ? 'is-active' : ''} onClick={() => setMode('RELAXED')} disabled={busy || merging}><strong>宽松门禁</strong><small>保留更多原文知识点，失败段由你决定是否重试</small></button>
              <button type="button" role="radio" aria-checked={mode === 'STANDARD'} className={mode === 'STANDARD' ? 'is-active' : ''} onClick={() => setMode('STANDARD')} disabled={busy || merging}><strong>标准门禁</strong><small>提高逐论断忠实度要求</small></button>
            </div>
            <div className="review-segment-toolbar-actions"><button className="outline-action small" type="button" onClick={selectUnfinishedSegments} disabled={busy || merging}><Check size={14} />选择未生成分段</button><button className="primary-action" type="button" onClick={() => void generateSegments()} disabled={!selectedIds.length || busy || merging}>{busy ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}{busy ? '后台生成中' : `生成已选 ${selectedIds.length} 段`}</button></div>
          </div>

          {task?.progress ? <SegmentTaskProgressPanel task={task} now={clockNow} restarting={starting} onRestart={() => void generateSegments(task.segmentIds, true)} /> : null}

          <div className="review-segment-layout">
            <section className="review-segment-source-column" aria-labelledby="review-segment-source-title">
              <div className="review-segment-column-heading"><div><FileSearch size={16} /><h4 id="review-segment-source-title">原始内容与逐段提示词</h4></div><span>只调用勾选段</span></div>
              <div className="review-segment-list">
                {workspace.segments.map((segment) => {
                  const result = results[segment.segmentId];
                  const hasDraft = Boolean(drafts[segment.segmentId]?.length);
                  const isGenerating = busy && task?.progress.currentSegmentId === segment.segmentId;
                  return <article className={`review-segment-source${selected[segment.segmentId] ? ' is-selected' : ''}`} key={segment.segmentId}>
                    <div className="review-segment-source-head"><label><input type="checkbox" checked={Boolean(selected[segment.segmentId])} onChange={(event) => setSelected((previous) => ({ ...previous, [segment.segmentId]: event.target.checked }))} disabled={busy || merging} /><span><strong>第 {segment.segmentIndex} 段</strong><small>{segment.title}</small></span></label><span className={`review-segment-state ${isGenerating ? 'is-running' : result?.status === 'FAILED' ? 'is-failed' : hasDraft ? 'is-ready' : ''}`}>{isGenerating ? task?.progress.stageLabel.replace(/^原文第 \d+ 段 · /, '') || '生成中' : result?.status === 'FAILED' ? '生成失败' : hasDraft ? `${drafts[segment.segmentId].length} 张候选` : '未生成'}</span></div>
                    <div className="review-segment-source-meta"><span>{segment.evidenceCount} 条 evidence</span><span>{segment.characterCount.toLocaleString('zh-CN')} 字</span><span>ID {segment.segmentId.slice(-8)}</span></div>
                    <details className="review-segment-raw"><summary>查看本段原始内容<ChevronRight size={14} /></summary><pre>{segment.rawContent}</pre></details>
                    <label className="review-segment-prompt"><span>本段补充提示词</span><textarea value={prompts[segment.segmentId] || ''} maxLength={2000} rows={4} onChange={(event) => setPrompts((previous) => ({ ...previous, [segment.segmentId]: event.target.value }))} disabled={busy || merging} /><small>建议补充追问方向、必须覆盖的术语或希望保留的原始问句。</small></label>
                    {result?.status === 'FAILED' ? <div className="review-segment-failure"><strong>{result.error || '本段未通过质量校验'}</strong>{result.qualityFeedback.slice(0, 4).map((feedback, index) => <span key={`${segment.segmentId}-feedback-${index}`}>{feedback}</span>)}<button className="outline-action small" type="button" onClick={() => void generateSegments([segment.segmentId])} disabled={busy || merging}><RotateCcw size={14} />调整后重试本段</button></div> : null}
                  </article>;
                })}
              </div>
            </section>

            <section className="review-segment-result-column" aria-labelledby="review-segment-result-title">
              <div className="review-segment-column-heading"><div><Sparkles size={16} /><h4 id="review-segment-result-title">可编辑候选</h4></div><span>逐段决定是否合并</span></div>
              <div className="review-segment-result-list">
                {workspace.segments.some((segment) => (drafts[segment.segmentId] || []).length) ? workspace.segments.map((segment) => {
                  const segmentDrafts = drafts[segment.segmentId] || [];
                  if (!segmentDrafts.length) return null;
                  return <article className={`review-segment-result${included[segment.segmentId] ? ' is-included' : ''}`} key={`result-${segment.segmentId}`}>
                    <div className="review-segment-result-head"><div><strong>第 {segment.segmentIndex} 段</strong><span>{segmentDrafts.length} 张候选</span></div><label className="review-segment-include"><input type="checkbox" checked={Boolean(included[segment.segmentId])} onChange={(event) => setIncluded((previous) => ({ ...previous, [segment.segmentId]: event.target.checked }))} disabled={merging} /><span>参与最终合并</span></label></div>
                    <div className="review-segment-card-list">{segmentDrafts.map((card, cardIndex) => <article className="review-segment-card-editor" key={`${segment.segmentId}-card-${cardIndex}`}>
                      <div className="review-segment-card-title"><span>候选卡片 {cardIndex + 1}</span><button className="icon-button tiny danger" type="button" title="移除这张候选" aria-label={`移除第 ${segment.segmentIndex} 段的候选卡片 ${cardIndex + 1}`} onClick={() => setDrafts((previous) => ({ ...previous, [segment.segmentId]: previous[segment.segmentId].filter((_, index) => index !== cardIndex) }))} disabled={merging}><Trash2 size={13} /></button></div>
                      <label><span>面试官问题</span><textarea value={card.content.question} maxLength={500} rows={3} onChange={(event) => updateSegmentCard(setDrafts, segment.segmentId, cardIndex, 'question', event.target.value)} disabled={merging} /></label>
                      <label><span>参考答案（Markdown）</span><textarea value={card.content.answer} maxLength={5000} rows={8} onChange={(event) => updateSegmentCard(setDrafts, segment.segmentId, cardIndex, 'answer', event.target.value)} disabled={merging} /></label>
                      <label><span>作答提示（可选）</span><textarea value={card.content.hint || ''} maxLength={1000} rows={2} onChange={(event) => updateSegmentCard(setDrafts, segment.segmentId, cardIndex, 'hint', event.target.value)} disabled={merging} /></label>
                      <small className="review-segment-evidence-count">保留 {card.evidenceIds.length} 条 evidence 引用</small>
                    </article>)}</div>
                  </article>;
                }) : <div className="review-segment-empty"><Layers3 size={28} /><strong>还没有候选卡片</strong><span>在左侧查看原文，勾选一个或多个分段后开始生成。</span></div>}
              </div>
              <label className="review-segment-summary"><span>合并后的资料摘要（可选）</span><textarea value={summary} maxLength={5000} rows={4} onChange={(event) => setSummary(event.target.value)} disabled={merging} /></label>
            </section>
          </div>

          <footer className="review-segment-footer"><div><Check size={14} /><span>草稿会按当前资料版本保存在浏览器；正式合并时才替换原卡片。</span></div><div className="review-delete-actions"><button className="outline-action" type="button" onClick={resetDraft} disabled={busy || merging}><RotateCcw size={15} />清空本地草稿</button><button className="outline-action" type="button" onClick={onClose} disabled={merging}><X size={15} />保留草稿并关闭</button><button className="primary-action" type="button" onClick={() => void mergeCandidates()} disabled={!candidateCards.length || invalidCandidate || busy || merging}>{merging ? <Loader2 className="spin" size={16} /> : <Merge size={16} />}{merging ? '正在合并' : `合并 ${candidateCards.length} 张为正式卡片`}</button></div></footer>
        </> : null}
      </section>
    </div>
  );
}

function SegmentTaskProgressPanel({ task, now, restarting, onRestart }: { task: ReviewSegmentGenerationTask; now: number; restarting: boolean; onRestart: () => void }) {
  const progress = task.progress;
  const heartbeatTime = progress.heartbeatAt || task.updatedAt || progress.createdAt;
  const heartbeatAge = heartbeatTime ? Math.max(0, Math.floor((now - new Date(heartbeatTime).getTime()) / 1000)) : null;
  const elapsedSeconds = progress.elapsedSeconds ?? (task.createdAt ? Math.max(0, Math.floor((now - new Date(task.createdAt).getTime()) / 1000)) : null);
  const heartbeatLost = ['QUEUED', 'RUNNING'].includes(task.status) && heartbeatAge !== null && heartbeatAge >= 60;
  const events = [...(progress.events || [])].slice(-6).reverse();
  const currentSegment = progress.currentSegmentIndex && progress.totalSegments
    ? `原文第 ${progress.currentSegmentIndex} 段 · 本轮共 ${progress.totalSegments} 段`
    : '正在准备所选分段';
  const attempt = progress.attempt && progress.maxAttempts ? `模型第 ${progress.attempt}/${progress.maxAttempts} 轮` : null;
  return <div className={`missing-knowledge-task-status is-${task.status.toLowerCase()}${heartbeatLost ? ' is-stale' : ''}`} role="status" aria-live="polite">
    <div className="missing-knowledge-task-heading">
      <span className="missing-knowledge-task-icon">{task.status === 'FAILED' ? <CircleAlert size={16} /> : task.status === 'SUCCEEDED' ? <Check size={16} /> : <Loader2 className="spin" size={16} />}</span>
      <div><strong>{progress.stageLabel}</strong><span>{progress.message}</span></div><b>{progress.percent}%</b>
    </div>
    <div className="missing-knowledge-task-progress"><i style={{ width: `${progress.percent}%` }} /></div>
    <div className="review-segment-task-meta"><span>{currentSegment}</span>{progress.completedSegments != null && progress.totalSegments ? <span>已完成 {progress.completedSegments}/{progress.totalSegments} 段</span> : null}{attempt ? <span>{attempt}</span> : null}<span>已运行 {formatElapsed(elapsedSeconds)}</span>{heartbeatAge !== null ? <span>最近心跳 {heartbeatAge < 5 ? '刚刚' : `${heartbeatAge} 秒前`}</span> : null}</div>
    {heartbeatLost ? <div className="review-segment-task-warning"><CircleAlert size={14} /><span>超过 1 分钟没有收到后台心跳，任务可能正在等待模型超时或进程异常；你可以继续等待单段超时，也可以立即替代这条旧任务。</span><button className="outline-action small" type="button" onClick={onRestart} disabled={restarting}>{restarting ? <Loader2 className="spin" size={13} /> : <RotateCcw size={13} />}{restarting ? '正在创建新任务' : '放弃旧任务并重新生成原选择'}</button></div> : null}
    {progress.detail ? <p className="review-segment-task-detail">{progress.detail}</p> : null}
    {events.length ? <details className="review-segment-task-events"><summary>查看最近阶段（{events.length}）</summary><ol>{events.map((event, index) => <li key={`${event.stageCode}-${event.createdAt || index}-${index}`}><strong>{event.stageLabel}</strong><span>{event.message}{event.attempt && event.maxAttempts ? ` · 第 ${event.attempt}/${event.maxAttempts} 轮` : ''}{event.createdAt ? ` · ${formatTaskTime(event.createdAt)}` : ''}</span>{event.detail ? <small>{event.detail}</small> : null}</li>)}</ol></details> : null}
  </div>;
}

function formatElapsed(seconds: number | null) {
  if (seconds === null || !Number.isFinite(seconds)) return '计算中';
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return minutes ? `${minutes} 分 ${String(remainder).padStart(2, '0')} 秒` : `${remainder} 秒`;
}

function formatTaskTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function defaultPrompt(initialPrompt?: string) {
  const normalized = initialPrompt?.replace(/\s+/g, ' ').trim();
  return normalized ? `${DEFAULT_SEGMENT_PROMPT} 用户补充：${normalized}` : DEFAULT_SEGMENT_PROMPT;
}

function segmentDraftKey(materialId: number) {
  return `review-segment-workspace:${materialId}`;
}

function readStoredDraft(materialId: number, sourceVersion: number): StoredSegmentDraft | null {
  try {
    const raw = window.localStorage.getItem(segmentDraftKey(materialId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredSegmentDraft;
    if (parsed.sourceVersion !== sourceVersion) {
      window.localStorage.removeItem(segmentDraftKey(materialId));
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function mergeSummaryText(current: string, additions: string[]) {
  const parts = [current.trim(), ...additions].filter(Boolean);
  return Array.from(new Set(parts)).join('\n\n').slice(0, 5000);
}

function updateSegmentCard(
  setDrafts: Dispatch<SetStateAction<Record<string, ReviewMaterialCardSnapshot[]>>>,
  segmentId: string,
  cardIndex: number,
  field: 'question' | 'answer' | 'hint',
  value: string
) {
  setDrafts((previous) => ({
    ...previous,
    [segmentId]: (previous[segmentId] || []).map((card, index) => index === cardIndex
      ? { ...card, content: { ...card.content, [field]: value } }
      : card)
  }));
}
