import { ArrowRight, Check, CircleAlert, Loader2, RotateCcw, Sparkles, WandSparkles, X } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import {
  applyReviewMaterialRewrite,
  fetchLatestReviewMaterialRewriteTask,
  fetchReviewMaterialRewriteTask,
  startReviewMaterialRewriteTask,
  type ReviewCardContent,
  type ReviewCardRewriteMode,
  type ReviewMaterialRewriteApplyResult,
  type ReviewMaterialRewritePreview,
  type ReviewMaterialRewriteTask
} from '../../api/reviews';
import { MarkdownText } from '../../components/MarkdownText';

export interface MaterialRewriteTarget {
  materialId: number;
  title: string;
  summary?: string | null;
  cardCount: number;
}

interface ReviewMaterialRewriteDialogProps {
  target: MaterialRewriteTarget | null;
  onClose: () => void;
  onApplied: (result: ReviewMaterialRewriteApplyResult) => void | Promise<void>;
}

const REWRITE_MODES: Array<{ value: ReviewCardRewriteMode; label: string; detail: string }> = [
  { value: 'STRICT_SOURCE', label: '严格依赖原文', detail: '答案事实必须由引用片段直接支持' },
  { value: 'SOURCE_FIRST', label: '尽量以原文为主', detail: '保留核心术语，允许去重和重组' },
  { value: 'SOURCE_REFERENCE', label: '原文仅参考', detail: '优先按你的结构要求组织内容' }
];

const DEFAULT_INSTRUCTION = '将当前资料已有卡片合并成 1 张综合卡片，保留所有核心知识点，去除重复内容并按面试复习顺序重新组织。';

// 资料级改写统一采用“生成候选—前后对比—确认覆盖”三阶段流程。
export function ReviewMaterialRewriteDialog({ target, onClose, onApplied }: ReviewMaterialRewriteDialogProps) {
  const [mode, setMode] = useState<ReviewCardRewriteMode>('SOURCE_FIRST');
  const [instruction, setInstruction] = useState(DEFAULT_INSTRUCTION);
  const [preview, setPreview] = useState<ReviewMaterialRewritePreview | null>(null);
  const [drafts, setDrafts] = useState<ReviewCardContent[]>([]);
  const [targetCardCount, setTargetCardCount] = useState<number | null>(null);
  const [draftSummary, setDraftSummary] = useState('');
  const [starting, setStarting] = useState(false);
  const [task, setTask] = useState<ReviewMaterialRewriteTask | null>(null);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const busy = starting || task?.status === 'QUEUED' || task?.status === 'RUNNING';

  useEffect(() => {
    let active = true;
    if (!target) return () => { active = false; };
    setMode('SOURCE_FIRST');
    setInstruction(DEFAULT_INSTRUCTION);
    setPreview(null);
    setDrafts([]);
    setTargetCardCount(null);
    setDraftSummary(target.summary || '');
    setStarting(false);
    setTask(null);
    setError('');
    void fetchLatestReviewMaterialRewriteTask(target.materialId)
      .then((latest) => {
        if (!active || !latest) return;
        setTask(latest);
        setInstruction(latest.instruction);
        setMode(latest.mode);
        if (latest.result) {
          setPreview(latest.result);
          setDrafts(latest.result.proposedCards.map((card) => card.content));
          setDraftSummary(latest.result.proposedSummary || latest.result.originalSummary || '');
        }
        if (latest.status === 'FAILED') setError(latest.error || '资料级改写预览生成失败');
      })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : '资料改写任务状态读取失败');
      });
    return () => { active = false; };
  }, [target?.materialId]);

  useEffect(() => {
    if (!target) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !applying) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [applying, onClose, target]);

  useEffect(() => {
    if (!target || !task || task.status === 'SUCCEEDED' || task.status === 'FAILED') return undefined;
    let active = true;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const nextTask = await fetchReviewMaterialRewriteTask(target.materialId, task.taskId);
        if (!active) return;
        setTask(nextTask);
        if (nextTask.status === 'SUCCEEDED' && nextTask.result) {
          setPreview(nextTask.result);
          setDrafts(nextTask.result.proposedCards.map((card) => card.content));
          setDraftSummary(nextTask.result.proposedSummary || nextTask.result.originalSummary || '');
        } else if (nextTask.status === 'FAILED') {
          setError(nextTask.error || '资料级改写预览生成失败');
        } else {
          timer = window.setTimeout(() => void poll(), 1200);
        }
      } catch (pollError) {
        if (!active) return;
        setError(pollError instanceof Error ? pollError.message : '资料改写进度读取失败');
        timer = window.setTimeout(() => void poll(), 2000);
      }
    };
    timer = window.setTimeout(() => void poll(), 300);
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [target?.materialId, task?.taskId]);

  if (!target) return null;
  const activeTarget = target;
  const progress = task?.progress;

  async function generatePreview(event?: FormEvent) {
    event?.preventDefault();
    if (!instruction.trim() || busy || applying) return;
    setStarting(true);
    setError('');
    setPreview(null);
    try {
      const nextTask = await startReviewMaterialRewriteTask(activeTarget.materialId, {
        instruction: instruction.trim(),
        mode,
        targetCardCount,
        baseCards: preview?.proposedCards.map((card, index) => ({
          ...card,
          cardId: null,
          content: drafts[index] || card.content
        })) || []
      });
      setTask(nextTask);
      setInstruction(nextTask.instruction);
      setMode(nextTask.mode);
      if (nextTask.result) {
        setPreview(nextTask.result);
        setDrafts(nextTask.result.proposedCards.map((card) => card.content));
        setDraftSummary(nextTask.result.proposedSummary || nextTask.result.originalSummary || '');
      }
      if (nextTask.status === 'FAILED') setError(nextTask.error || '资料级改写预览生成失败');
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : '资料级改写预览生成失败');
    } finally {
      setStarting(false);
    }
  }

  async function applyPreview() {
    if (!preview || applying || drafts.length !== preview.proposedCards.length) return;
    if (drafts.some((draft) => !draft.question.trim() || !draft.answer.trim())) return;
    setApplying(true);
    setError('');
    try {
      const result = await applyReviewMaterialRewrite(activeTarget.materialId, {
        sourceVersion: preview.sourceVersion,
        originalFingerprint: preview.originalFingerprint,
        originalCardIds: preview.originalCardIds,
        proposedSummary: draftSummary.trim() || null,
        proposedCards: drafts.map((draft, index) => ({
          question: draft.question.trim(),
          answer: draft.answer.trim(),
          hint: draft.hint?.trim() || null,
          rewriteMode: preview.mode,
          evidenceIds: preview.proposedCards[index]?.evidenceIds || []
        }))
      });
      await onApplied(result);
      onClose();
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : '应用资料级改写失败');
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="review-delete-overlay review-rewrite-overlay" role="dialog" aria-modal="true" aria-labelledby="review-material-rewrite-title" onMouseDown={(event) => { if (event.target === event.currentTarget && !applying) onClose(); }}>
      <section className="review-rewrite-dialog review-material-rewrite-dialog" aria-busy={busy}>
        <header className="review-card-editor-header">
          <div><span className="review-card-editor-icon is-ai"><WandSparkles size={18} /></span><div><h3 id="review-material-rewrite-title">AI 重组整份资料</h3><p>当前 {target.cardCount} 张卡片会先重组为目标数量的独立候选，确认前不会覆盖原内容。</p></div></div>
          <button className="icon-button compact" type="button" onClick={onClose} disabled={applying} title={busy ? '关闭窗口并在后台继续' : '关闭资料改写'} aria-label={busy ? '关闭窗口并在后台继续' : '关闭资料改写'}><X size={18} /></button>
        </header>
        {error ? <div className="review-alert danger">{error}</div> : null}
        <form className="review-rewrite-controls" onSubmit={(event) => void generatePreview(event)}>
          <div className="review-rewrite-mode-group" role="radiogroup" aria-label="资料原文依赖档位">
            {REWRITE_MODES.map((option) => <button key={option.value} type="button" role="radio" aria-checked={mode === option.value} className={mode === option.value ? 'is-active' : ''} onClick={() => { setMode(option.value); setPreview(null); }} disabled={busy || applying}><strong>{option.label}</strong><span>{option.detail}</span></button>)}
          </div>
          <label className="review-rewrite-instruction"><span>告诉 AI 你希望怎样修改</span><textarea value={instruction} maxLength={2000} rows={3} onChange={(event) => setInstruction(event.target.value)} disabled={busy || applying} /></label>
          <label className="review-rewrite-card-count"><span>候选卡片数量</span><input type="number" min={1} step={1} value={targetCardCount ?? ''} placeholder="自动识别" onChange={(event) => { const value = event.target.value.trim(); setTargetCardCount(value ? Math.max(1, Number(value)) : null); }} disabled={busy || applying} /><small>留空自动识别；输入任意正整数，不设置卡片数量上限。</small></label>
          <button className="primary-action" type="submit" disabled={!instruction.trim() || busy || applying}>{busy ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}{busy ? '后台生成中' : preview ? '重新生成候选' : '生成资料级对比'}</button>
        </form>
        {task && progress ? (
          <div className={`missing-knowledge-task-status is-${task.status.toLowerCase()}`} role="status" aria-live="polite">
            <div className="missing-knowledge-task-heading">
              <span className="missing-knowledge-task-icon">{task.status === 'FAILED' ? <CircleAlert size={16} /> : task.status === 'SUCCEEDED' ? <Check size={16} /> : <Loader2 className="spin" size={16} />}</span>
              <div><strong>{progress.stageLabel}</strong><span>{progress.message}</span></div>
              <b>{progress.percent}%</b>
            </div>
            <div className="missing-knowledge-task-progress"><i style={{ width: `${progress.percent}%` }} /></div>
            {busy ? <button className="outline-action small" type="button" onClick={onClose}><X size={14} />关闭并后台运行</button> : null}
            {progress.events.length > 1 ? <div className="missing-knowledge-task-events">{progress.events.slice(-4).map((event, index) => <span key={`${event.stageCode}-${event.createdAt || index}`}>{event.stageLabel}</span>)}</div> : null}
          </div>
        ) : null}
        {preview ? <>
          <div className="review-rewrite-comparison review-material-comparison" aria-label="资料原卡片与综合卡片对比">
            <div className="review-material-original-column"><div className="review-comparison-column-title">修改前 · {preview.originalCards.length} 张卡片</div>{preview.originalCards.map((card, index) => <ComparisonCard key={card.cardId || index} label={`原卡片 ${index + 1}`} content={card.content} tone="original" />)}</div>
            <div className="review-rewrite-arrow" aria-hidden="true"><ArrowRight size={20} /></div>
            <div className="review-rewrite-proposed-column"><div className="review-comparison-column-title">修改后 · {drafts.length} 张候选卡片</div><div className="review-material-proposed-list">{drafts.map((draft, index) => <div className="review-material-proposed-item" key={`proposed-${index}`}><ComparisonCard label={`候选卡片 ${index + 1}`} content={draft} tone="proposed" /><div className="review-rewrite-inline-editor"><label><span>新问题</span><textarea value={draft.question} maxLength={500} rows={3} onChange={(event) => setDrafts((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, question: event.target.value } : item))} disabled={applying} /></label><label><span>新答案（Markdown）</span><textarea value={draft.answer} maxLength={5000} rows={8} onChange={(event) => setDrafts((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, answer: event.target.value } : item))} disabled={applying} /></label><label><span>新提示（可选）</span><textarea value={draft.hint || ''} maxLength={1000} rows={3} onChange={(event) => setDrafts((previous) => previous.map((item, itemIndex) => itemIndex === index ? { ...item, hint: event.target.value } : item))} disabled={applying} /></label></div></div>)}</div><div className="review-rewrite-inline-editor"><label><span>新摘要（可选）</span><textarea value={draftSummary} maxLength={5000} rows={4} onChange={(event) => setDraftSummary(event.target.value)} disabled={applying} /></label></div></div>
          </div>
          {preview.mergeNote ? <p className="review-material-merge-note"><Check size={14} />{preview.mergeNote}</p> : null}
          <footer className="review-card-editor-footer review-rewrite-footer"><span><Check size={14} />由 {preview.modelName} 生成 · 原 {preview.originalCards.length} 张卡片将在确认后停用</span><div className="review-delete-actions"><button className="outline-action" type="button" onClick={onClose} disabled={applying}><RotateCcw size={15} />回退并关闭</button><button className="primary-action" type="button" onClick={() => void applyPreview()} disabled={applying || drafts.length !== preview.proposedCards.length || drafts.some((draft) => !draft.question.trim() || !draft.answer.trim())}>{applying ? <Loader2 className="spin" size={16} /> : <Check size={16} />}{applying ? '覆盖中' : `确认覆盖为 ${drafts.length} 张`}</button></div></footer>
        </> : null}
      </section>
    </div>
  );
}

function ComparisonCard({ label, content, tone }: { label: string; content: ReviewCardContent; tone: 'original' | 'proposed' }) {
  return <article className={`review-question-card is-revealed review-comparison-card is-${tone}`}><div className="review-comparison-label">{label}</div><MarkdownText content={content.question || '暂无问题'} className="review-card-question-markdown" /><div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={content.answer || '暂无答案'} /></div>{content.hint ? <div className="review-hint"><span>提示</span><MarkdownText content={content.hint} /></div> : null}</article>;
}
