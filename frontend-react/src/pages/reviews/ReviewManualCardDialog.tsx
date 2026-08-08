import { CircleAlert, Loader2, PenLine, Save, ShieldCheck, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createManualReviewCard, type ReviewCard } from '../../api/reviews';

export interface ManualCardTarget {
  materialId: number;
  title: string;
  cardCount: number;
}

export function ReviewManualCardDialog({
  target,
  onClose,
  onCreated
}: {
  target: ManualCardTarget | null;
  onClose: () => void;
  onCreated: (card: ReviewCard) => void | Promise<void>;
}) {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [hint, setHint] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const questionRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setQuestion('');
    setAnswer('');
    setHint('');
    setError('');
    setSaving(false);
    if (!target) return undefined;
    const timer = window.setTimeout(() => questionRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [target?.materialId]);

  useEffect(() => {
    if (!target) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [target?.materialId, saving, onClose]);

  if (!target) return null;
  const activeTarget = target;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    const normalizedAnswer = answer.trim();
    if (!normalizedQuestion || !normalizedAnswer || saving) return;
    setSaving(true);
    setError('');
    try {
      const card = await createManualReviewCard(activeTarget.materialId, {
        question: normalizedQuestion,
        answer: normalizedAnswer,
        ...(hint.trim() ? { hint: hint.trim() } : {})
      });
      await onCreated(card);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '手动卡片保存失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="review-delete-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
      <form className="review-manual-card-dialog" role="dialog" aria-modal="true" aria-labelledby="review-manual-card-title" onSubmit={submit}>
        <header className="review-manual-card-dialog-header">
          <div className="review-manual-card-dialog-icon"><PenLine size={20} /></div>
          <div>
            <span className="page-eyebrow">人工建卡</span>
            <h3 id="review-manual-card-title">创建自己的复习卡片</h3>
            <p title={activeTarget.title}>{activeTarget.title}</p>
          </div>
          <button className="icon-button compact" type="button" title="关闭手动建卡" aria-label="关闭手动建卡" onClick={onClose} disabled={saving}><X size={18} /></button>
        </header>

        <div className="review-manual-card-notice"><ShieldCheck size={17} /><div><strong>已有 {activeTarget.cardCount} 张卡片不会被修改</strong><span>这张卡会立即进入复习队列，作为“手动卡片”保存，不伪造 RAG 原文证据。</span></div></div>

        <label className="review-manual-card-field"><span>问题</span><input ref={questionRef} value={question} maxLength={500} required placeholder="例如：类方法如何定义和调用？" onChange={(event) => setQuestion(event.target.value)} disabled={saving} /></label>
        <label className="review-manual-card-field"><span>答案</span><textarea value={answer} maxLength={5000} required rows={7} placeholder="写下你希望复习时主动回忆的答案，可使用 Markdown。" onChange={(event) => setAnswer(event.target.value)} disabled={saving} /></label>
        <label className="review-manual-card-field"><span>提示（可选）</span><input value={hint} maxLength={1000} placeholder="例如：回忆装饰器 @classmethod 与 cls 参数" onChange={(event) => setHint(event.target.value)} disabled={saving} /></label>

        {error ? <div className="review-alert danger review-manual-card-error" role="alert"><CircleAlert size={15} />{error}</div> : null}
        <div className="review-delete-actions"><button className="outline-action" type="button" onClick={onClose} disabled={saving}>取消</button><button className="primary-action" type="submit" disabled={saving || !question.trim() || !answer.trim()}>{saving ? <Loader2 className="spin" size={16} /> : <Save size={16} />}{saving ? '保存中' : '保存卡片'}</button></div>
      </form>
    </div>
  );
}
