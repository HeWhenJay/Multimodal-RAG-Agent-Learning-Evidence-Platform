import { ArrowRight, Check, Eye, Loader2, Pencil, RotateCcw, Save, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { updateReviewCard, type ReviewCard, type ReviewCardContent } from '../../api/reviews';
import { MarkdownText } from '../../components/MarkdownText';

interface ReviewCardEditDialogProps {
  target: ReviewCard | null;
  onClose: () => void;
  onSaved: (card: ReviewCard) => void | Promise<void>;
}

// 直接编辑卡片正文并实时预览 Markdown，保存时保留原 FSRS 进度。
export function ReviewCardEditDialog({ target, onClose, onSaved }: ReviewCardEditDialogProps) {
  const [draft, setDraft] = useState<ReviewCardContent>({ question: '', answer: '', hint: '' });
  const [saving, setSaving] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState('');
  const questionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!target) return undefined;
    setDraft({ question: target.question, answer: target.answer || '', hint: target.hint || '' });
    setError('');
    setComparing(false);
    const timer = window.setTimeout(() => questionRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [target]);

  useEffect(() => {
    if (!target) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving && !comparing) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, saving, target]);

  if (!target) return null;
  const activeTarget = target;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.question.trim() || !draft.answer.trim() || saving) return;
    setComparing(true);
  }

  async function applyDraft() {
    if (!draft.question.trim() || !draft.answer.trim() || saving) return;
    setSaving(true);
    setError('');
    try {
      const updated = await updateReviewCard(activeTarget.id, {
        question: draft.question.trim(),
        answer: draft.answer.trim(),
        hint: draft.hint?.trim() || null
      });
      await onSaved(updated);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '卡片保存失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="review-delete-overlay review-card-editor-overlay" role="dialog" aria-modal="true" aria-labelledby="review-card-editor-title" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving && !comparing) onClose(); }}>
      <form className="review-card-editor-dialog" onSubmit={submit}>
        <header className="review-card-editor-header">
          <div><span className="review-card-editor-icon"><Pencil size={18} /></span><div><h3 id="review-card-editor-title">编辑复习卡片</h3><p>修改问题、答案与提示，Markdown 会按右侧效果展示。</p></div></div>
          <button className="icon-button compact" type="button" onClick={onClose} disabled={saving} aria-label="关闭卡片编辑"><X size={18} /></button>
        </header>
        {error ? <div className="review-alert danger">{error}</div> : null}
        <div className="review-card-editor-layout">
          <div className="review-card-editor-fields">
            <label><span>问题</span><textarea ref={questionRef} value={draft.question} maxLength={500} rows={3} onChange={(event) => setDraft((previous) => ({ ...previous, question: event.target.value }))} disabled={saving} /></label>
            <label><span>答案（支持 Markdown）</span><textarea value={draft.answer} maxLength={5000} rows={12} onChange={(event) => setDraft((previous) => ({ ...previous, answer: event.target.value }))} disabled={saving} /></label>
            <label><span>提示（可选，支持 Markdown）</span><textarea value={draft.hint || ''} maxLength={1000} rows={4} onChange={(event) => setDraft((previous) => ({ ...previous, hint: event.target.value }))} disabled={saving} /></label>
          </div>
          <div className="review-card-editor-preview">
            <div className="review-card-editor-preview-label"><Eye size={15} />卡片预览</div>
            <article className="review-question-card is-revealed review-card-preview-card">
              <div className="review-card-meta"><div className="review-card-meta-leading"><span>知识点 · 编辑预览</span>{activeTarget.isUserEdited ? <em className="review-card-source-tag">已编辑</em> : null}</div></div>
              <MarkdownText content={draft.question || '请输入问题'} className="review-card-question-markdown" />
              <div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={draft.answer || '请输入答案'} /></div>
              {draft.hint?.trim() ? <div className="review-hint"><span>提示</span><MarkdownText content={draft.hint} /></div> : null}
            </article>
          </div>
        </div>
        {comparing ? <div className="review-rewrite-comparison review-manual-comparison" aria-label="原卡片与编辑后卡片对比">
          <ReviewComparisonCard label="修改前" content={{ question: activeTarget.question, answer: activeTarget.answer || '', hint: activeTarget.hint || '' }} tone="original" />
          <div className="review-rewrite-arrow" aria-hidden="true"><ArrowRight size={20} /></div>
          <ReviewComparisonCard label="修改后" content={draft} tone="proposed" />
        </div> : null}
        <footer className="review-card-editor-footer">
          <span><Check size={14} />复习次数、到期时间和历史评分不会改变</span>
          <div className="review-delete-actions"><button className="outline-action" type="button" onClick={() => comparing ? setComparing(false) : onClose()} disabled={saving}>{comparing ? <RotateCcw size={15} /> : null}{comparing ? '返回编辑' : '取消'}</button><button className="primary-action" type={comparing ? 'button' : 'submit'} onClick={comparing ? () => void applyDraft() : undefined} disabled={saving || !draft.question.trim() || !draft.answer.trim()}>{saving ? <Loader2 className="spin" size={16} /> : comparing ? <Check size={16} /> : <Save size={16} />}{saving ? '保存中' : comparing ? '确认覆盖' : '查看修改对比'}</button></div>
        </footer>
      </form>
    </div>
  );
}

function ReviewComparisonCard({ label, content, tone }: { label: string; content: ReviewCardContent; tone: 'original' | 'proposed' }) {
  return <article className={`review-question-card is-revealed review-comparison-card is-${tone}`}><div className="review-comparison-label">{label}</div><MarkdownText content={content.question || '暂无问题'} className="review-card-question-markdown" /><div className="review-answer-block"><span className="answer-label">答案</span><MarkdownText content={content.answer || '暂无答案'} /></div>{content.hint ? <div className="review-hint"><span>提示</span><MarkdownText content={content.hint} /></div> : null}</article>;
}
