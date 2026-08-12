import { ArrowRight, Check, CircleAlert, Eye, Loader2, Pencil, RotateCcw, Sparkles, WandSparkles, X } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import {
  fetchReviewCandidateRewriteTask,
  startReviewCandidateRewriteTask,
  type ReviewCandidateRewriteTask,
  type ReviewCardContent,
  type ReviewCardRewriteMode,
  type ReviewMaterialCardSnapshot
} from '../../api/reviews';
import { MarkdownText } from '../../components/MarkdownText';

export interface ReviewCandidateCardEditTarget {
  materialId: number;
  segmentId: string;
  segmentIndex: number;
  cardIndex: number;
  card: ReviewMaterialCardSnapshot;
  initialMode: 'MANUAL' | 'AI';
}

interface ReviewCandidateCardEditDialogProps {
  target: ReviewCandidateCardEditTarget | null;
  onClose: () => void;
  onConfirmed: (card: ReviewMaterialCardSnapshot) => void;
}

const REWRITE_MODES: Array<{ value: ReviewCardRewriteMode; label: string; detail: string }> = [
  { value: 'STRICT_SOURCE', label: '严格依赖原文', detail: '答案逐项受引用 evidence 支撑' },
  { value: 'SOURCE_FIRST', label: '尽量以原文为主', detail: '允许重组结构和轻量解释' },
  { value: 'SOURCE_REFERENCE', label: '原文仅参考', detail: '优先按你的修改要求表达' }
];

// 候选卡片的手动与 AI 修改都先停留在对比预览，确认后才写回分段草稿。
export function ReviewCandidateCardEditDialog({ target, onClose, onConfirmed }: ReviewCandidateCardEditDialogProps) {
  const [editMode, setEditMode] = useState<'MANUAL' | 'AI'>('MANUAL');
  const [rewriteMode, setRewriteMode] = useState<ReviewCardRewriteMode>('SOURCE_FIRST');
  const [instruction, setInstruction] = useState('');
  const [draft, setDraft] = useState<ReviewCardContent>({ question: '', answer: '', hint: '' });
  const [preview, setPreview] = useState<ReviewMaterialCardSnapshot | null>(null);
  const [task, setTask] = useState<ReviewCandidateRewriteTask | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const busy = starting || task?.status === 'QUEUED' || task?.status === 'RUNNING';

  useEffect(() => {
    if (!target) return;
    setEditMode(target.initialMode);
    setRewriteMode('SOURCE_FIRST');
    setInstruction('');
    setDraft({ ...target.card.content });
    setPreview(null);
    setTask(null);
    setStarting(false);
    setError('');
  }, [target]);

  useEffect(() => {
    if (!target) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [busy, onClose, target]);

  useEffect(() => {
    if (!target || !task || !['QUEUED', 'RUNNING'].includes(task.status)) return undefined;
    let active = true;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const nextTask = await fetchReviewCandidateRewriteTask(target.materialId, task.taskId);
        if (!active) return;
        setTask(nextTask);
        if (nextTask.status === 'SUCCEEDED' && nextTask.result) {
          setPreview({
            cardId: null,
            content: nextTask.result.proposed,
            evidenceRefs: nextTask.result.evidenceRefs,
            evidenceIds: nextTask.result.evidenceIds
          });
        } else if (nextTask.status === 'FAILED') {
          setError(nextTask.error || '候选卡片 AI 修改失败');
        } else {
          timer = window.setTimeout(() => void poll(), 1200);
        }
      } catch (pollError) {
        if (!active) return;
        setError(pollError instanceof Error ? pollError.message : '候选卡片 AI 修改进度读取失败');
        timer = window.setTimeout(() => void poll(), 2000);
      }
    };
    timer = window.setTimeout(() => void poll(), 300);
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [target?.materialId, task?.taskId, task?.status]);

  if (!target) return null;

  function switchMode(nextMode: 'MANUAL' | 'AI') {
    if (busy) return;
    setEditMode(nextMode);
    setPreview(null);
    setError('');
  }

  function previewManual(event: FormEvent) {
    event.preventDefault();
    if (!draft.question.trim() || !draft.answer.trim()) return;
    setPreview({
      ...target!.card,
      content: {
        question: draft.question.trim(),
        answer: draft.answer.trim(),
        hint: draft.hint?.trim() || null
      }
    });
  }

  async function generateAiPreview(event: FormEvent) {
    event.preventDefault();
    if (!instruction.trim() || busy) return;
    setStarting(true);
    setError('');
    setPreview(null);
    try {
      const nextTask = await startReviewCandidateRewriteTask(target!.materialId, {
        instruction: instruction.trim(),
        mode: rewriteMode,
        candidate: target!.card
      });
      setTask(nextTask);
      if (nextTask.status === 'SUCCEEDED' && nextTask.result) {
        setPreview({
          cardId: null,
          content: nextTask.result.proposed,
          evidenceRefs: nextTask.result.evidenceRefs,
          evidenceIds: nextTask.result.evidenceIds
        });
      }
      if (nextTask.status === 'FAILED') setError(nextTask.error || '候选卡片 AI 修改失败');
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : '候选卡片 AI 修改任务创建失败');
    } finally {
      setStarting(false);
    }
  }

  function confirmPreview() {
    if (!preview) return;
    onConfirmed(preview);
    onClose();
  }

  return (
    <div className="review-delete-overlay review-candidate-edit-overlay" role="dialog" aria-modal="true" aria-labelledby="review-candidate-edit-title" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <section className="review-candidate-edit-dialog" aria-busy={busy}>
        <header className="review-card-editor-header">
          <div><span className={`review-card-editor-icon${editMode === 'AI' ? ' is-ai' : ''}`}>{editMode === 'AI' ? <WandSparkles size={18} /> : <Pencil size={18} />}</span><div><h3 id="review-candidate-edit-title">修改第 {target.segmentIndex} 段 · 候选卡片 {target.cardIndex + 1}</h3><p>修改只生成本地预览；点击确认采用后才会写回当前候选，正式卡片仍需最后合并。</p></div></div>
          <button className="icon-button compact" type="button" onClick={onClose} disabled={busy} aria-label="关闭候选卡片修改"><X size={18} /></button>
        </header>

        <div className="review-candidate-edit-tabs" role="tablist" aria-label="候选卡片修改方式">
          <button type="button" role="tab" aria-selected={editMode === 'MANUAL'} className={editMode === 'MANUAL' ? 'is-active' : ''} onClick={() => switchMode('MANUAL')} disabled={busy}><Pencil size={15} /><span><strong>自己修改</strong><small>手动编辑问题、答案和提示</small></span></button>
          <button type="button" role="tab" aria-selected={editMode === 'AI'} className={editMode === 'AI' ? 'is-active' : ''} onClick={() => switchMode('AI')} disabled={busy}><Sparkles size={15} /><span><strong>AI 修改</strong><small>输入要求，让 AI 基于 evidence 改写</small></span></button>
        </div>

        {error ? <div className="review-alert danger" role="alert"><CircleAlert size={16} />{error}</div> : null}

        {editMode === 'MANUAL' ? <form className="review-candidate-manual-fields" onSubmit={previewManual}>
          <label><span>面试官问题</span><textarea value={draft.question} maxLength={500} rows={3} onChange={(event) => { setDraft((previous) => ({ ...previous, question: event.target.value })); setPreview(null); }} /></label>
          <label><span>参考答案（Markdown）</span><textarea value={draft.answer} maxLength={5000} rows={10} onChange={(event) => { setDraft((previous) => ({ ...previous, answer: event.target.value })); setPreview(null); }} /></label>
          <label><span>作答提示（可选）</span><textarea value={draft.hint || ''} maxLength={1000} rows={3} onChange={(event) => { setDraft((previous) => ({ ...previous, hint: event.target.value })); setPreview(null); }} /></label>
          <button className="primary-action" type="submit" disabled={!draft.question.trim() || !draft.answer.trim()}><Eye size={16} />预览手动修改</button>
        </form> : <form className="review-candidate-ai-controls" onSubmit={(event) => void generateAiPreview(event)}>
          <div className="review-rewrite-mode-group" role="radiogroup" aria-label="AI 修改原文依赖档位">{REWRITE_MODES.map((option) => <button key={option.value} type="button" role="radio" aria-checked={rewriteMode === option.value} className={rewriteMode === option.value ? 'is-active' : ''} onClick={() => { setRewriteMode(option.value); setPreview(null); }} disabled={busy}><strong>{option.label}</strong><span>{option.detail}</span></button>)}</div>
          <label className="review-rewrite-instruction"><span>告诉 AI 你想怎么修改</span><textarea value={instruction} maxLength={2000} rows={4} placeholder="例如：问题改成真实 Java 面试官的追问，答案突出过期策略与淘汰策略的触发条件差异。" onChange={(event) => { setInstruction(event.target.value); setPreview(null); }} disabled={busy} /></label>
          <button className="primary-action" type="submit" disabled={!instruction.trim() || busy}>{busy ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}{busy ? 'AI 后台修改中' : '生成 AI 修改预览'}</button>
        </form>}

        {task?.progress && editMode === 'AI' ? <div className={`missing-knowledge-task-status is-${task.status.toLowerCase()}`} role="status" aria-live="polite"><div className="missing-knowledge-task-heading"><span className="missing-knowledge-task-icon">{task.status === 'FAILED' ? <CircleAlert size={16} /> : task.status === 'SUCCEEDED' ? <Check size={16} /> : <Loader2 className="spin" size={16} />}</span><div><strong>{task.progress.stageLabel}</strong><span>{task.progress.message}</span></div><b>{task.progress.percent}%</b></div><div className="missing-knowledge-task-progress"><i style={{ width: `${task.progress.percent}%` }} /></div></div> : null}

        {preview ? <div className="review-candidate-preview-section"><div className="review-candidate-preview-heading"><Eye size={15} /><span>修改预览</span><small>请核对完整问题、答案和提示后再确认</small></div><div className="review-rewrite-comparison"><CandidatePreviewCard label="修改前" content={target.card.content} tone="original" /><div className="review-rewrite-arrow" aria-hidden="true"><ArrowRight size={20} /></div><CandidatePreviewCard label="修改后" content={preview.content} tone="proposed" /></div></div> : null}

        <footer className="review-card-editor-footer"><span><Check size={14} />当前引用 {preview?.evidenceIds.length ?? target.card.evidenceIds.length} 条 evidence，确认前不会改变候选</span><div className="review-delete-actions"><button className="outline-action" type="button" onClick={() => preview ? setPreview(null) : onClose()} disabled={busy}>{preview ? <RotateCcw size={15} /> : <X size={15} />}{preview ? '返回修改' : '取消'}</button><button className="primary-action" type="button" onClick={confirmPreview} disabled={!preview || busy}><Check size={16} />确认采用修改</button></div></footer>
      </section>
    </div>
  );
}

function CandidatePreviewCard({ label, content, tone }: { label: string; content: ReviewCardContent; tone: 'original' | 'proposed' }) {
  return <article className={`review-question-card is-revealed review-comparison-card is-${tone}`}><div className="review-comparison-label">{label}</div><MarkdownText content={content.question || '暂无问题'} className="review-card-question-markdown" /><div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={content.answer || '暂无答案'} /></div>{content.hint ? <div className="review-hint"><span>提示</span><MarkdownText content={content.hint} /></div> : null}</article>;
}
