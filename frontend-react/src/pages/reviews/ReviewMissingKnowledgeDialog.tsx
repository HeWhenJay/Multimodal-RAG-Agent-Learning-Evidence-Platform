import { Check, Loader2, MessageCirclePlus, Send, ShieldCheck, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  supplementReviewMissingKnowledge,
  type ReviewMissingKnowledgeMessage
} from '../../api/reviews';

export interface MissingKnowledgeTarget {
  materialId: number;
  title: string;
  cardCount: number;
}

export function ReviewMissingKnowledgeDialog({
  target,
  onClose,
  onCardsAdded
}: {
  target: MissingKnowledgeTarget | null;
  onClose: () => void;
  onCardsAdded: (addedCount: number) => void | Promise<void>;
}) {
  const [messages, setMessages] = useState<ReviewMissingKnowledgeMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setMessages([]);
    setInput('');
    setError('');
    setBusy(false);
    if (target) window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [target?.materialId]);

  const activeTarget = target;
  if (!activeTarget) return null;
  const activeMaterialId = activeTarget.materialId;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy) return;
    const history = messages.slice(-12);
    const userMessage: ReviewMissingKnowledgeMessage = { role: 'USER', content: message };
    setMessages((previous) => [...previous, userMessage]);
    setInput('');
    setBusy(true);
    setError('');
    try {
      const result = await supplementReviewMissingKnowledge(activeMaterialId, message, history);
      setMessages((previous) => [...previous, { role: 'ASSISTANT', content: result.assistantMessage }]);
      if (result.addedCount > 0) await onCardsAdded(result.addedCount);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '查找遗漏知识点失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="evidence-dialog-backdrop missing-knowledge-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <section className="missing-knowledge-dialog" role="dialog" aria-modal="true" aria-labelledby="missing-knowledge-title">
        <header>
          <div className="missing-knowledge-heading-icon"><MessageCirclePlus size={20} /></div>
          <div className="missing-knowledge-heading-copy">
            <span className="page-eyebrow">对话补漏</span>
            <h3 id="missing-knowledge-title">查找遗漏知识点</h3>
            <p title={activeTarget.title}>{activeTarget.title}</p>
          </div>
          <button className="icon-button compact" type="button" title="关闭补漏对话" aria-label="关闭补漏对话" onClick={onClose} disabled={busy}><X size={18} /></button>
        </header>

        <div className="missing-knowledge-safety"><ShieldCheck size={17} /><div><strong>现有 {activeTarget.cardCount} 张卡片保持不变</strong><span>只从这份文档的 RAG 原文中追加新卡；评分、到期时间和复习记录不会被修改。</span></div></div>

        <div className="missing-knowledge-thread" aria-live="polite">
          {!messages.length ? <div className="missing-knowledge-empty"><MessageCirclePlus size={24} /><strong>告诉我可能漏掉了什么</strong><span>例如：“视频后半段还讲了页缓存和零拷贝，请分别检查。”</span></div> : null}
          {messages.map((message, index) => <div className={`missing-knowledge-message is-${message.role.toLowerCase()}`} key={`${message.role}-${index}`}><span>{message.role === 'USER' ? '你' : <Check size={14} />}</span><p>{message.content}</p></div>)}
          {busy ? <div className="missing-knowledge-message is-assistant is-thinking"><span><Loader2 className="spin" size={14} /></span><p>正在定位相关原文、核对现有卡片并执行 evidence 质量门禁…</p></div> : null}
        </div>

        {error ? <div className="review-alert danger missing-knowledge-error">{error}</div> : null}
        <form className="missing-knowledge-composer" onSubmit={submit}>
          <label htmlFor="missing-knowledge-input">描述遗漏主题</label>
          <textarea id="missing-knowledge-input" ref={inputRef} value={input} maxLength={2000} rows={3} placeholder="例如：顺序写后面还解释了页缓存的作用，请找出漏掉的卡片" onChange={(event) => setInput(event.target.value)} disabled={busy} />
          <div><span>{input.length}/2000 · 可以继续追问</span><button className="primary-action" type="submit" disabled={busy || !input.trim()}>{busy ? <Loader2 className="spin" size={16} /> : <Send size={16} />}{busy ? '查找中' : '查找并追加'}</button></div>
        </form>
      </section>
    </div>
  );
}
