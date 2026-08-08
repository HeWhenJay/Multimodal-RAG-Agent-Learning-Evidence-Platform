import { ArrowRight, Check, CircleAlert, Loader2, RotateCcw, Sparkles, WandSparkles, X } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import {
  fetchLatestReviewCardRewriteTask,
  fetchReviewCardRewriteTask,
  startReviewCardRewriteTask,
  updateReviewCard,
  type ReviewCard,
  type ReviewCardContent,
  type ReviewCardRewriteMode,
  type ReviewCardRewritePreview,
  type ReviewCardRewriteTask
} from '../../api/reviews';
import { MarkdownText } from '../../components/MarkdownText';

interface ReviewCardRewriteDialogProps {
  target: ReviewCard | null;
  onClose: () => void;
  onSaved: (card: ReviewCard) => void | Promise<void>;
}

const REWRITE_MODES: Array<{ value: ReviewCardRewriteMode; label: string; detail: string }> = [
  { value: 'STRICT_SOURCE', label: '严格依赖原文', detail: '答案事实必须由引用片段直接支持' },
  { value: 'SOURCE_FIRST', label: '尽量以原文为主', detail: '优先原文，允许轻量解释与重组' },
  { value: 'SOURCE_REFERENCE', label: '原文仅参考', detail: '优先满足你的表达和补充想法' }
];

// LLM 只生成候选；用户可在对比页继续编辑，点击应用后才写入数据库。
export function ReviewCardRewriteDialog({ target, onClose, onSaved }: ReviewCardRewriteDialogProps) {
  const [mode, setMode] = useState<ReviewCardRewriteMode>('SOURCE_FIRST');
  const [instruction, setInstruction] = useState('');
  const [preview, setPreview] = useState<ReviewCardRewritePreview | null>(null);
  const [draft, setDraft] = useState<ReviewCardContent>({ question: '', answer: '', hint: '' });
  const [starting, setStarting] = useState(false);
  const [task, setTask] = useState<ReviewCardRewriteTask | null>(null);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const busy = starting || task?.status === 'QUEUED' || task?.status === 'RUNNING';

  useEffect(() => {
    let active = true;
    if (!target) return () => { active = false; };
    setMode('SOURCE_FIRST');
    setInstruction('');
    setPreview(null);
    setDraft({ question: target.question, answer: target.answer || '', hint: target.hint || '' });
    setStarting(false);
    setTask(null);
    setError('');
    void fetchLatestReviewCardRewriteTask(target.id)
      .then((latest) => {
        if (!active || !latest) return;
        setTask(latest);
        setInstruction(latest.instruction);
        setMode(latest.mode);
        if (latest.result) {
          setPreview(latest.result);
          setDraft(latest.result.proposed);
        }
        if (latest.status === 'FAILED') setError(latest.error || '卡片改写预览生成失败');
      })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : '卡片改写任务状态读取失败');
      });
    return () => { active = false; };
  }, [target?.id]);

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
        const nextTask = await fetchReviewCardRewriteTask(target.id, task.taskId);
        if (!active) return;
        setTask(nextTask);
        if (nextTask.status === 'SUCCEEDED' && nextTask.result) {
          setPreview(nextTask.result);
          setDraft(nextTask.result.proposed);
        } else if (nextTask.status === 'FAILED') {
          setError(nextTask.error || '卡片改写预览生成失败');
        } else {
          timer = window.setTimeout(() => void poll(), 1200);
        }
      } catch (pollError) {
        if (!active) return;
        setError(pollError instanceof Error ? pollError.message : '卡片改写进度读取失败');
        timer = window.setTimeout(() => void poll(), 2000);
      }
    };
    timer = window.setTimeout(() => void poll(), 300);
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [target?.id, task?.taskId]);

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
      const nextTask = await startReviewCardRewriteTask(activeTarget.id, { instruction: instruction.trim(), mode });
      setTask(nextTask);
      setInstruction(nextTask.instruction);
      setMode(nextTask.mode);
      if (nextTask.result) {
        setPreview(nextTask.result);
        setDraft(nextTask.result.proposed);
      }
      if (nextTask.status === 'FAILED') setError(nextTask.error || '卡片改写预览生成失败');
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : '卡片改写预览生成失败');
    } finally {
      setStarting(false);
    }
  }

  async function applyPreview() {
    if (!preview || !draft.question.trim() || !draft.answer.trim() || applying) return;
    setApplying(true);
    setError('');
    try {
      const updated = await updateReviewCard(activeTarget.id, {
        question: draft.question.trim(),
        answer: draft.answer.trim(),
        hint: draft.hint?.trim() || null,
        rewriteMode: preview.mode,
        evidenceIds: preview.evidenceRefs.map((item) => item.evidenceId)
      });
      await onSaved(updated);
      onClose();
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : '应用卡片改写失败');
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="review-delete-overlay review-rewrite-overlay" role="dialog" aria-modal="true" aria-labelledby="review-rewrite-title" onMouseDown={(event) => { if (event.target === event.currentTarget && !applying) onClose(); }}>
      <section className="review-rewrite-dialog" aria-busy={busy}>
        <header className="review-card-editor-header">
          <div><span className="review-card-editor-icon is-ai"><WandSparkles size={18} /></span><div><h3 id="review-rewrite-title">让 LLM 改写卡片</h3><p>先生成对比候选，再由你继续编辑、回退或应用。</p></div></div>
          <button className="icon-button compact" type="button" onClick={onClose} disabled={applying} title={busy ? '关闭窗口并在后台继续' : '关闭卡片改写'} aria-label={busy ? '关闭窗口并在后台继续' : '关闭卡片改写'}><X size={18} /></button>
        </header>
        {error ? <div className="review-alert danger">{error}</div> : null}
        <form className="review-rewrite-controls" onSubmit={(event) => void generatePreview(event)}>
          <div className="review-rewrite-mode-group" role="radiogroup" aria-label="原文依赖档位">
            {REWRITE_MODES.map((option) => <button key={option.value} type="button" role="radio" aria-checked={mode === option.value} className={mode === option.value ? 'is-active' : ''} onClick={() => { setMode(option.value); setPreview(null); }} disabled={busy || applying}><strong>{option.label}</strong><span>{option.detail}</span></button>)}
          </div>
          <label className="review-rewrite-instruction"><span>告诉 LLM 你想怎么改</span><textarea value={instruction} maxLength={2000} rows={3} placeholder="例如：把答案改成三步流程，突出易混点，并保留原文里的术语。" onChange={(event) => setInstruction(event.target.value)} disabled={busy || applying} /></label>
          <button className="primary-action" type="submit" disabled={!instruction.trim() || busy || applying}>{busy ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}{busy ? '后台生成中' : preview ? '重新生成候选' : '生成改写对比'}</button>
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
        {preview ? (
          <>
            <div className="review-rewrite-comparison" aria-label="原卡片与新卡片对比">
              <ReviewComparisonCard label="原卡片" content={preview.original} tone="original" />
              <div className="review-rewrite-arrow" aria-hidden="true"><ArrowRight size={20} /></div>
              <div className="review-rewrite-proposed-column">
                <ReviewComparisonCard label={`新卡片 · ${modeLabel(preview.mode)}`} content={draft} tone="proposed" />
                <div className="review-rewrite-inline-editor">
                  <label><span>新问题</span><textarea value={draft.question} maxLength={500} rows={2} onChange={(event) => setDraft((previous) => ({ ...previous, question: event.target.value }))} disabled={applying} /></label>
                  <label><span>新答案（Markdown）</span><textarea value={draft.answer} maxLength={5000} rows={8} onChange={(event) => setDraft((previous) => ({ ...previous, answer: event.target.value }))} disabled={applying} /></label>
                  <label><span>新提示（可选）</span><textarea value={draft.hint || ''} maxLength={1000} rows={3} onChange={(event) => setDraft((previous) => ({ ...previous, hint: event.target.value }))} disabled={applying} /></label>
                </div>
              </div>
            </div>
            <footer className="review-card-editor-footer review-rewrite-footer">
              <span><Check size={14} />由 {preview.modelName} 生成 · 应用前不会改动原卡片</span>
              <div className="review-delete-actions"><button className="outline-action" type="button" onClick={onClose} disabled={applying}><RotateCcw size={15} />回退并关闭</button><button className="primary-action" type="button" onClick={() => void applyPreview()} disabled={applying || !draft.question.trim() || !draft.answer.trim()}>{applying ? <Loader2 className="spin" size={16} /> : <Check size={16} />}{applying ? '应用中' : '应用新卡片'}</button></div>
            </footer>
          </>
        ) : null}
      </section>
    </div>
  );
}

function ReviewComparisonCard({ label, content, tone }: { label: string; content: ReviewCardContent; tone: 'original' | 'proposed' }) {
  return (
    <article className={`review-question-card is-revealed review-comparison-card is-${tone}`}>
      <div className="review-comparison-label">{label}</div>
      <MarkdownText content={content.question || '暂无问题'} className="review-card-question-markdown" />
      <div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={content.answer || '暂无答案'} /></div>
      {content.hint ? <div className="review-hint"><span>提示</span><MarkdownText content={content.hint} /></div> : null}
    </article>
  );
}

function modeLabel(mode: ReviewCardRewriteMode) {
  return REWRITE_MODES.find((item) => item.value === mode)?.label || 'AI 候选';
}
