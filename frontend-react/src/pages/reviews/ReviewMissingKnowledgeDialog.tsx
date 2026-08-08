import { Check, CircleAlert, Loader2, MessageCirclePlus, Send, ShieldCheck, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  fetchLatestSupplementReviewMissingKnowledge,
  fetchSupplementReviewMissingKnowledgeTask,
  startSupplementReviewMissingKnowledge,
  type ReviewMissingKnowledgeMessage,
  type ReviewMissingKnowledgeResult,
  type ReviewMissingKnowledgeTask
} from '../../api/reviews';

export interface MissingKnowledgeTarget {
  materialId: number;
  title: string;
  cardCount: number;
}

export function ReviewMissingKnowledgeDialog({
  target,
  onClose,
  onCardsAdded,
  onTaskChanged
}: {
  target: MissingKnowledgeTarget | null;
  onClose: () => void;
  onCardsAdded: (result: ReviewMissingKnowledgeResult) => void | Promise<void>;
  onTaskChanged?: (task: ReviewMissingKnowledgeTask | null) => void;
}) {
  const [messages, setMessages] = useState<ReviewMissingKnowledgeMessage[]>([]);
  const [input, setInput] = useState('');
  const [starting, setStarting] = useState(false);
  const [task, setTask] = useState<ReviewMissingKnowledgeTask | null>(null);
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const activeTarget = target;
  const activeMaterialId = activeTarget?.materialId ?? null;
  const busy = starting || task?.status === 'QUEUED' || task?.status === 'RUNNING';

  useEffect(() => {
    let active = true;
    setMessages([]);
    setInput('');
    setError('');
    setStarting(false);
    setTask(null);
    onTaskChanged?.(null);
    if (!target) return () => {
      active = false;
    };
    window.setTimeout(() => {
      if (active) inputRef.current?.focus();
    }, 0);
    void fetchLatestSupplementReviewMissingKnowledge(target.materialId)
      .then((latest) => {
        if (!active || !latest) return;
        setTask(latest);
        onTaskChanged?.(latest);
        setMessages([
          { role: 'USER', content: latest.message },
          ...(latest.result ? [{ role: 'ASSISTANT' as const, content: latest.result.assistantMessage }] : [])
        ]);
        if (latest.result) void onCardsAdded(latest.result);
      })
      .catch((loadError) => {
        if (active) setError(loadError instanceof Error ? loadError.message : '补漏任务状态读取失败');
      });
    return () => {
      active = false;
    };
  }, [target?.materialId]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy || activeMaterialId === null) return;
    const history = messages.slice(-12);
    const userMessage: ReviewMissingKnowledgeMessage = { role: 'USER', content: message };
    setMessages((previous) => [...previous, userMessage]);
    setInput('');
    setStarting(true);
    setError('');
    try {
      const nextTask = await startSupplementReviewMissingKnowledge(activeMaterialId, message, history);
      setTask(nextTask);
      onTaskChanged?.(nextTask);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '查找遗漏知识点失败');
    } finally {
      setStarting(false);
    }
  }

  useEffect(() => {
    if (activeMaterialId === null || !task || task.status === 'SUCCEEDED' || task.status === 'FAILED') return undefined;
    let active = true;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const nextTask = await fetchSupplementReviewMissingKnowledgeTask(activeMaterialId, task.taskId);
        if (!active) return;
        setTask(nextTask);
        onTaskChanged?.(nextTask);
        if (nextTask.status === 'SUCCEEDED') {
          const result = nextTask.result;
          if (result) {
            setMessages((previous) => {
              if (previous.some((item) => item.role === 'ASSISTANT' && item.content === result.assistantMessage)) return previous;
              return [...previous, { role: 'ASSISTANT', content: result.assistantMessage }];
            });
            await onCardsAdded(result);
          }
        } else if (nextTask.status === 'FAILED') {
          setError(nextTask.error || '查找遗漏知识点失败');
        } else {
          timer = window.setTimeout(() => void poll(), 1200);
        }
      } catch (pollError) {
        if (!active) return;
        setError(pollError instanceof Error ? pollError.message : '补漏进度读取失败');
        timer = window.setTimeout(() => void poll(), 2000);
      }
    };
    timer = window.setTimeout(() => void poll(), 300);
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [activeMaterialId, task?.taskId]);

  if (!activeTarget) return null;
  const progress = task?.progress;
  const cardCount = activeTarget.cardCount + (task?.result?.addedCount || 0);

  return (
    <div className="evidence-dialog-backdrop missing-knowledge-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="missing-knowledge-dialog" role="dialog" aria-modal="true" aria-labelledby="missing-knowledge-title" aria-busy={busy}>
        <header>
          <div className="missing-knowledge-heading-icon"><MessageCirclePlus size={20} /></div>
          <div className="missing-knowledge-heading-copy">
            <span className="page-eyebrow">对话补漏</span>
            <h3 id="missing-knowledge-title">查找遗漏知识点</h3>
            <p title={activeTarget.title}>{activeTarget.title}</p>
          </div>
          <button className="icon-button compact" type="button" title={busy ? '关闭窗口并在后台继续' : '关闭补漏对话'} aria-label={busy ? '关闭窗口并在后台继续' : '关闭补漏对话'} onClick={onClose}><X size={18} /></button>
        </header>

        <div className="missing-knowledge-safety"><ShieldCheck size={17} /><div><strong>现有 {cardCount} 张卡片保持不变</strong><span>只从这份文档的 RAG 原文中追加新卡；评分、到期时间和复习记录不会被修改。</span></div></div>

        {task && progress ? (
          <div className={`missing-knowledge-task-status is-${task.status.toLowerCase()}`} role="status" aria-live="polite">
            <div className="missing-knowledge-task-heading">
              <span className="missing-knowledge-task-icon">
                {task.status === 'FAILED' ? <CircleAlert size={16} /> : task.status === 'SUCCEEDED' ? <Check size={16} /> : <Loader2 className="spin" size={16} />}
              </span>
              <div><strong>{progress.stageLabel}</strong><span>{progress.message}</span></div>
              <b>{progress.percent}%</b>
            </div>
            <div className="missing-knowledge-task-progress"><i style={{ width: `${progress.percent}%` }} /></div>
            {task.status === 'QUEUED' || task.status === 'RUNNING' ? <button className="outline-action small" type="button" onClick={onClose}><X size={14} />关闭并后台运行</button> : null}
            {progress.events.length > 1 ? <div className="missing-knowledge-task-events">{progress.events.slice(-4).map((event, index) => <span key={`${event.stageCode}-${event.createdAt || index}`}>{event.stageLabel}</span>)}</div> : null}
          </div>
        ) : null}

        <div className="missing-knowledge-thread" aria-live="polite">
          {!messages.length ? <div className="missing-knowledge-empty"><MessageCirclePlus size={24} /><strong>告诉我可能漏掉了什么</strong><span>例如：“视频后半段还讲了页缓存和零拷贝，请分别检查。”</span></div> : null}
          {messages.map((message, index) => <div className={`missing-knowledge-message is-${message.role.toLowerCase()}`} key={`${message.role}-${index}`}><span>{message.role === 'USER' ? '你' : <Check size={14} />}</span><p>{message.content}</p></div>)}
        </div>

        {error ? <div className="review-alert danger missing-knowledge-error"><CircleAlert size={15} />{error}</div> : null}
        <form className="missing-knowledge-composer" onSubmit={submit}>
          <label htmlFor="missing-knowledge-input">描述遗漏主题</label>
          <textarea id="missing-knowledge-input" ref={inputRef} value={input} maxLength={2000} rows={3} placeholder="例如：顺序写后面还解释了页缓存的作用，请找出漏掉的卡片" onChange={(event) => setInput(event.target.value)} disabled={busy} />
          <div><span>{input.length}/2000 · 可以继续追问</span><button className="primary-action" type="submit" disabled={busy || !input.trim()}>{busy ? <Loader2 className="spin" size={16} /> : <Send size={16} />}{busy ? '查找中' : '查找并追加'}</button></div>
        </form>
      </section>
    </div>
  );
}
