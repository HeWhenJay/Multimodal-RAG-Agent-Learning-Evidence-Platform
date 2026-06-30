import {
  Archive,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  History,
  Info,
  Layers3,
  ListChecks,
  Loader2,
  MessageCircle,
  PanelTopOpen,
  RotateCcw,
  Search,
  Send,
  Trash2,
  X,
  XCircle
} from 'lucide-react';
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  archiveAgentMemory,
  confirmAgentMemory,
  createAgentTask,
  decideAgentReview,
  deleteAgentMemory,
  fetchAgentMemories,
  fetchAgentTask,
  fetchAgentTaskMessages,
  fetchAgentTasks,
  rejectAgentMemory,
  subscribeAgentTask,
  undoAgentOperation
} from '../../api/agent';
import type { AgentChatMessage, AgentHumanReview, AgentMemory, AgentOperation, AgentStreamEvent, AgentTask, AgentToolCall } from '../../api/types';
import { MarkdownText } from '../../components/MarkdownText';

const TERMINAL_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELED']);

type AgentWorkspaceMode = 'read' | 'free_explore';
type DetailTab = 'environment' | 'progress' | 'plan' | 'evidence' | 'approval' | 'history';

interface FeatureOption {
  value: AgentWorkspaceMode;
  title: string;
  description: string;
  icon: ReactNode;
  tags: string[];
  defaultGoal: string;
}

const COPY = {
  readTitle: '\u53ea\u8bfb\u95ee\u7b54',
  readDesc: '\u53ea\u8bfb\u53d6\u5f53\u524d\u77e5\u8bc6\u5e93\u548c\u4efb\u52a1\u4e0a\u4e0b\u6587\uff0c\u751f\u6210\u5e26\u8bc1\u636e\u7684\u56de\u7b54\uff0c\u4e0d\u5199\u5165\u4e1a\u52a1\u6570\u636e\u3002',
  freeTitle: '\u81ea\u7531\u63a2\u7d22',
  freeDesc: '\u81ea\u7531\u63d0\u95ee\u3001\u6574\u7406\u60f3\u6cd5\u3001\u62c6\u89e3\u5b66\u4e60\u4e3b\u9898\uff0c\u4f18\u5148\u8054\u7f51\u67e5\u8be2\u83b7\u53d6\u5916\u90e8\u8d44\u6599\uff0cRAG \u4ec5\u4f5c\u4e3a\u672c\u5730\u8bc1\u636e\u8865\u5145\u6216\u964d\u7ea7\u8def\u5f84\u3002',
  emptyTitle: '\u4eca\u5929\u60f3\u8ba9 Agent \u5e2e\u4f60\u5904\u7406\u4ec0\u4e48\uff1f',
  emptyDesc: '\u9009\u62e9\u529f\u80fd\u540e\u76f4\u63a5\u63cf\u8ff0\u76ee\u6807\uff0cAgent \u4f1a\u6574\u7406\u8ba1\u5212\u3001\u68c0\u7d22\u4f9d\u636e\u5e76\u7ed9\u51fa\u53ef\u8ffd\u8e2a\u7ed3\u679c\u3002',
  composerPlaceholder: '\u63cf\u8ff0\u4f60\u7684\u76ee\u6807\uff0cAgent \u4f1a\u5904\u7406\u8ba1\u5212\u3001\u68c0\u7d22\u4f9d\u636e\u548c\u6700\u7ec8\u56de\u7b54',
  taskPanel: '\u4efb\u52a1\u9762\u677f',
  environment: '\u73af\u5883\u4fe1\u606f',
  progress: '\u8fdb\u5ea6',
  plan: '\u8ba1\u5212',
  evidence: '\u4f9d\u636e',
  approval: '\u5ba1\u6279',
  history: '\u5386\u53f2',
  userGoal: '\u7528\u6237\u76ee\u6807',
  executionNote: '\u6267\u884c\u8bf4\u660e',
  reasoningSummary: '\u601d\u8003\u6458\u8981',
  finalAnswer: '\u6700\u7ec8\u56de\u7b54',
  toolCall: '\u5de5\u5177\u8c03\u7528',
  changeOperation: '\u53d8\u66f4\u64cd\u4f5c',
  memoryConfirm: '\u8bb0\u5fc6\u786e\u8ba4',
  contextSummary: '\u4e0a\u4e0b\u6587\u538b\u7f29',
  defaultGoalRead: '\u6211\u7684\u77e5\u8bc6\u5e93\u91cc Redis \u5b66\u5230\u4e86\u4ec0\u4e48\uff1f',
  defaultGoalFree: '\u5e2e\u6211\u6574\u7406\u4e00\u4e2a\u65b0\u7684\u5b66\u4e60\u4e3b\u9898\uff0c\u6216\u7ed3\u5408 JD \u548c\u89c6\u9891\u5b66\u4e60\u8bc1\u636e\u5206\u6790\u4e0b\u4e00\u6b65\u8d44\u6599\u6536\u96c6\u5efa\u8bae\u3002'
};

const FEATURE_OPTIONS: FeatureOption[] = [
  {
    value: 'read',
    title: COPY.readTitle,
    description: COPY.readDesc,
    icon: <Search size={17} />,
    tags: ['\u9ed8\u8ba4', '\u53ea\u8bfb', '\u65e0\u9700\u5ba1\u6279'],
    defaultGoal: COPY.defaultGoalRead
  },
  {
    value: 'free_explore',
    title: COPY.freeTitle,
    description: COPY.freeDesc,
    icon: <MessageCircle size={17} />,
    tags: ['\u63a2\u7d22', '\u53ea\u8bfb\u8fb9\u754c'],
    defaultGoal: COPY.defaultGoalFree
  }
];

const DETAIL_TABS: Array<{ value: DetailTab; label: string; icon: ReactNode }> = [
  { value: 'environment', label: COPY.environment, icon: <Info size={15} /> },
  { value: 'progress', label: COPY.progress, icon: <ListChecks size={15} /> },
  { value: 'plan', label: COPY.plan, icon: <Layers3 size={15} /> },
  { value: 'evidence', label: COPY.evidence, icon: <Search size={15} /> },
  { value: 'approval', label: COPY.approval, icon: <CheckCircle2 size={15} /> },
  { value: 'history', label: COPY.history, icon: <History size={15} /> }
];

export function AgentWorkspace() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [goal, setGoal] = useState('');
  const [workspaceMode, setWorkspaceMode] = useState<AgentWorkspaceMode>('read');
  const [featurePanelOpen, setFeaturePanelOpen] = useState(false);
  const [detailPanelOpen, setDetailPanelOpen] = useState(false);
  const [detailTab, setDetailTab] = useState<DetailTab>('progress');
  const [task, setTask] = useState<AgentTask | null>(null);
  const [historyTasks, setHistoryTasks] = useState<AgentTask[]>([]);
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [memoryAction, setMemoryAction] = useState('');
  const [memoryError, setMemoryError] = useState('');
  const [conversationMessages, setConversationMessages] = useState<AgentChatMessage[]>([]);
  const [hasMoreBefore, setHasMoreBefore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [reviewing, setReviewing] = useState('');
  const [undoing, setUndoing] = useState('');
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState('');
  const featureMenuRef = useRef<HTMLDivElement | null>(null);
  const detailPanelRef = useRef<HTMLDivElement | null>(null);
  const streamRef = useRef<EventSource | null>(null);

  const activeFeature = findFeature(workspaceMode);
  const hasConversation = Boolean(task);
  const visibleMessages = conversationMessages.length ? conversationMessages : (task?.messages || []);
  const hasServerMessages = Boolean(visibleMessages.length);
  const pendingReviews = (task?.reviews || []).filter((review) => review.status === 'PENDING');
  const pendingMemoryCandidates = memories.filter((item) => item.status === 'PENDING_REVIEW');
  const finalAnswer = stringValue(task?.final?.answer);
  const draftSummary = stringValue(task?.draft?.matchSummary) || stringValue(task?.draft?.answer);
  const backendNotice = task ? buildBackendNotice(task) : '';
  const evidenceIds = useMemo(() => uniqueList([...normalizeStringList(task?.final?.evidenceIds), ...normalizeStringList(task?.draft?.evidenceIds)]), [task?.final, task?.draft]);
  const expandedQueries = useMemo(() => normalizeStringList(task?.final?.expandedQueries || task?.draft?.expandedQueries), [task?.final, task?.draft]);
  const progressSteps = useMemo(() => buildProgressSteps(task), [task]);
  const routeTaskId = searchParams.get('taskId') || '';

  useEffect(() => {
    void loadMemories();
    void loadHistory();
    return () => {
      streamRef.current?.close();
    };
  }, []);

  useEffect(() => {
    if (!routeTaskId || routeTaskId === task?.id) return;
    void openHistoryTask(routeTaskId);
  }, [routeTaskId, task?.id]);

  useEffect(() => {
    function closeOnOutside(event: MouseEvent) {
      const target = event.target as Node;
      if (featureMenuRef.current && !featureMenuRef.current.contains(target)) {
        setFeaturePanelOpen(false);
      }
      if (detailPanelRef.current && !detailPanelRef.current.contains(target)) {
        setDetailPanelOpen(false);
      }
    }
    document.addEventListener('mousedown', closeOnOutside);
    return () => document.removeEventListener('mousedown', closeOnOutside);
  }, []);

  useEffect(() => {
    if (!task?.id || task.status === 'CREATING' || TERMINAL_STATUSES.has(task.status)) {
      setPolling(false);
      return undefined;
    }
    setPolling(true);
    const timer = window.setInterval(() => {
      void refreshTask(task.id);
    }, 1600);
    return () => window.clearInterval(timer);
  }, [task?.id, task?.status]);

  function chooseFeature(feature: FeatureOption) {
    setWorkspaceMode(feature.value);
    setFeaturePanelOpen(false);
    setError('');
    if (!goal.trim() || FEATURE_OPTIONS.some((item) => item.defaultGoal === goal)) {
      setGoal(feature.defaultGoal);
    }
  }

  async function submit() {
    const trimmedGoal = goal.trim();
    if (!trimmedGoal) {
      setError('\u8bf7\u8f93\u5165\u8981\u4ea4\u7ed9 Agent \u7684\u76ee\u6807');
      return;
    }
    const taskType = workspaceMode === 'read' ? 'pure_read_query' : 'planning_task';
    const enableWebSearch = workspaceMode === 'free_explore';
    const input = {
      goal: buildGoalForMode(workspaceMode, trimmedGoal),
      workspaceMode,
      topK: 5,
      candidateMultiplier: 4,
      enableWebSearch,
      webSearchQuery: enableWebSearch ? trimmedGoal : undefined,
      toolHints: buildToolHints(workspaceMode)
    };
    const optimisticTask: AgentTask = {
      id: `local-pending-${Date.now()}`,
      taskType,
      status: 'CREATING',
      title: trimmedGoal.slice(0, 48),
      input,
      plan: {},
      draft: {
        message: '\u6b63\u5728\u521b\u5efa Agent \u4efb\u52a1\uff0c\u5efa\u7acb\u4e0e\u540e\u7aef\u7684\u4e8b\u4ef6\u6d41\u8fde\u63a5\u3002'
      },
      final: {},
      toolCalls: [],
      reviews: [],
      operations: []
    };
    try {
      streamRef.current?.close();
      streamRef.current = null;
      setTask(optimisticTask);
      setDetailTab('progress');
      setSubmitting(true);
      setError('');
      const created = await createAgentTask({
        taskType,
        title: trimmedGoal.slice(0, 48),
        input
      });
      setTask(created);
      applyTaskMessages(created);
      setSearchParams({ taskId: created.id });
      setDetailTab('progress');
      setPolling(true);
      setGoal(trimmedGoal);
      connectTaskStream(created.id);
      void refreshTask(created.id);
      void loadHistory();
      notifyConversationTreeChanged();
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : 'Agent \u4efb\u52a1\u521b\u5efa\u5931\u8d25';
      setTask({
        ...optimisticTask,
        status: 'FAILED',
        errorCode: 'AGENT_CREATE_FAILED',
        errorMessage: message
      });
      setError('');
    } finally {
      setSubmitting(false);
    }
  }

  async function loadHistory() {
    try {
      setHistoryLoading(true);
      const items = await fetchAgentTasks(24);
      setHistoryTasks(items);
    } catch {
      setHistoryTasks([]);
    } finally {
      setHistoryLoading(false);
    }
  }

  async function openHistoryTask(taskId: string) {
    try {
      setError('');
      const latest = await fetchAgentTask(taskId);
      setTask(latest);
      applyTaskMessages(latest);
      setSearchParams({ taskId: latest.id });
      setGoal(displayUserGoal(latest.input?.goal || latest.title || ''));
      setWorkspaceMode(normalizeMode(latest.input?.workspaceMode));
      setDetailTab('progress');
      setDetailPanelOpen(false);
      connectTaskStream(latest.id);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '\u5386\u53f2\u4f1a\u8bdd\u8bfb\u53d6\u5931\u8d25');
    }
  }

  async function loadMemories() {
    try {
      setMemoryError('');
      const items = await fetchAgentMemories();
      setMemories(items);
    } catch (loadError) {
      setMemoryError(loadError instanceof Error ? loadError.message : 'Agent \u8bb0\u5fc6\u52a0\u8f7d\u5931\u8d25');
    }
  }

  async function actOnMemory(memory: AgentMemory, action: 'confirm' | 'reject' | 'archive' | 'delete') {
    if (action === 'delete' && !window.confirm('\u786e\u8ba4\u5220\u9664\u8fd9\u6761 Agent \u8bb0\u5fc6\uff1f\u5220\u9664\u540e\u6b63\u6587\u4f1a\u88ab\u64e6\u9664\u3002')) return;
    try {
      setMemoryAction(`${memory.id}-${action}`);
      setMemoryError('');
      if (action === 'confirm') await confirmAgentMemory(memory.id);
      else if (action === 'reject') await rejectAgentMemory(memory.id);
      else if (action === 'archive') await archiveAgentMemory(memory.id);
      else await deleteAgentMemory(memory.id);
      await loadMemories();
    } catch (actionError) {
      setMemoryError(actionError instanceof Error ? actionError.message : 'Agent \u8bb0\u5fc6\u64cd\u4f5c\u5931\u8d25');
    } finally {
      setMemoryAction('');
    }
  }

  async function refreshTask(taskId: string) {
    try {
      const latest = await fetchAgentTask(taskId);
      setTask(latest);
      applyTaskMessages(latest);
      notifyConversationTreeChanged();
      if (TERMINAL_STATUSES.has(latest.status)) {
        setPolling(false);
        void loadMemories();
        void loadHistory();
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Agent \u4efb\u52a1\u5237\u65b0\u5931\u8d25');
      setPolling(false);
    }
  }

  function connectTaskStream(taskId: string) {
    streamRef.current?.close();
    const source = subscribeAgentTask(taskId, {
      onTask: (latest) => {
        setTask(latest);
        applyTaskMessages(latest);
        notifyConversationTreeChanged();
        if (TERMINAL_STATUSES.has(latest.status)) {
          setPolling(false);
          void loadMemories();
          void loadHistory();
        } else {
          setPolling(true);
        }
      },
      onAgentEvent: (event) => {
        mergeStreamEvent(event);
      },
      onDone: () => {
        setPolling(false);
        void loadMemories();
        void loadHistory();
        notifyConversationTreeChanged();
      },
      onError: () => {
        streamRef.current?.close();
        streamRef.current = null;
      }
    });
    streamRef.current = source;
  }

  function applyTaskMessages(latest: AgentTask) {
    setConversationMessages((current) => mergeMessages(current, latest.messages || []));
    setHasMoreBefore(Boolean(latest.hasMoreMessagesBefore));
  }

  async function loadOlderMessages() {
    if (!task?.id || loadingOlder || !hasMoreBefore) return;
    const oldest = oldestMessageSequence(conversationMessages);
    if (!oldest) return;
    try {
      setLoadingOlder(true);
      const page = await fetchAgentTaskMessages(task.id, { beforeSequenceNo: oldest, limit: 30 });
      setConversationMessages((current) => mergeMessages(page.messages || [], current));
      setHasMoreBefore(Boolean(page.hasMoreBefore));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '更早消息加载失败');
    } finally {
      setLoadingOlder(false);
    }
  }

  function mergeStreamEvent(event: AgentStreamEvent) {
    if (!task || event.taskId !== task.id) return;
    const draft = normalizeRecord(event.draft);
    const message = stringValue(draft.message) || stringValue(event.errorMessage) || streamEventTitle(event.eventType);
    if (!message) return;
    const sourceKey = streamSourceKey(event, draft);
    const dedupeKey = streamDedupeKey(event, draft, message);
    const synthetic: AgentChatMessage = {
      id: `stream-${dedupeKey}`,
      taskId: event.taskId,
      sequenceNo: null,
      role: event.eventType === 'CONTEXT_COMPRESSED' ? 'SYSTEM' : 'ASSISTANT',
      messageType: event.eventType === 'TOOL_CALL_STARTED' || event.eventType === 'TOOL_CALL_COMPLETED' ? 'TOOL_OBSERVATION' : event.eventType === 'CONTEXT_COMPRESSED' ? 'CONTEXT_SUMMARY' : 'STATUS',
      content: message,
      payload: {
        ...draft,
        eventType: event.eventType,
        status: event.status,
        toolName: event.toolName,
        toolCallId: event.toolCallId,
        toolStatus: event.toolStatus,
        errorCode: event.errorCode,
        errorMessage: event.errorMessage
      },
      sourceEventType: event.eventType,
      sourceId: sourceKey,
      dedupeKey,
      createdAt: event.createdAt || new Date().toISOString(),
      updatedAt: event.createdAt || new Date().toISOString()
    };
    setConversationMessages((current) => mergeMessages(current, [synthetic]));
  }

  async function decide(review: AgentHumanReview, decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED') {
    if (!task) return;
    try {
      setReviewing(`${review.id}-${decision}`);
      setError('');
      const latest = await decideAgentReview(task.id, review.id, {
        decision,
        comment: decision === 'APPROVED' ? '\u540c\u610f\u7ee7\u7eed' : decision === 'REJECTED' ? '\u62d2\u7edd\u8be5\u5ba1\u6279' : '\u8bf7\u8c03\u6574\u8ba1\u5212\u6216\u8f93\u51fa',
        changes: {}
      });
      setTask(latest);
      if (!TERMINAL_STATUSES.has(latest.status)) void refreshTask(latest.id);
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : '\u5ba1\u6279\u63d0\u4ea4\u5931\u8d25');
    } finally {
      setReviewing('');
    }
  }

  async function undoOperation(operation: AgentOperation) {
    if (!task) return;
    try {
      setUndoing(operation.id);
      setError('');
      await undoAgentOperation(operation.id, {
        idempotencyKey: `undo-${operation.id}`,
        reason: '\u7528\u6237\u5728 Agent \u5de5\u4f5c\u53f0\u64a4\u9500'
      });
      await refreshTask(task.id);
    } catch (undoError) {
      setError(undoError instanceof Error ? undoError.message : '\u64a4\u9500\u64cd\u4f5c\u5931\u8d25');
    } finally {
      setUndoing('');
    }
  }

  return (
    <div className={`agent-page-v2 ${hasConversation ? 'has-conversation' : 'is-empty'}`}>
      <div className="agent-context-anchor" ref={detailPanelRef}>
        <button className={detailPanelOpen ? 'agent-context-button is-open' : 'agent-context-button'} type="button" onClick={() => setDetailPanelOpen((open) => !open)}>
          <PanelTopOpen size={17} />
          <span>{COPY.taskPanel}</span>
          {pendingReviews.length ? <em>{pendingReviews.length}</em> : null}
        </button>
        {detailPanelOpen ? (
          <DetailPanel
            activeFeature={activeFeature}
            detailTab={detailTab}
            evidenceIds={evidenceIds}
            expandedQueries={expandedQueries}
            historyLoading={historyLoading}
            historyTasks={historyTasks}
            memories={memories}
            memoryAction={memoryAction}
            memoryError={memoryError}
            pendingMemoryCandidates={pendingMemoryCandidates}
            pendingReviews={pendingReviews}
            progressSteps={progressSteps}
            reviewing={reviewing}
            task={task}
            undoing={undoing}
            onClose={() => setDetailPanelOpen(false)}
            onDecide={(review, decision) => void decide(review, decision)}
            onMemoryAction={(memory, action) => void actOnMemory(memory, action)}
            onOpenHistory={(taskId) => void openHistoryTask(taskId)}
            onRefreshHistory={() => void loadHistory()}
            onSetTab={setDetailTab}
            onUndo={(operation) => void undoOperation(operation)}
          />
        ) : null}
      </div>

      {!hasConversation ? (
        <section className="agent-empty-center">
          <div className="agent-empty-copy">
            <h2>{COPY.emptyTitle}</h2>
            <p>{COPY.emptyDesc}</p>
          </div>
          <Composer
            activeFeature={activeFeature}
            error={error}
            featurePanelOpen={featurePanelOpen}
            goal={goal}
            submitting={submitting}
            workspaceMode={workspaceMode}
            featureMenuRef={featureMenuRef}
            onChooseFeature={chooseFeature}
            onGoalChange={setGoal}
            onSubmit={() => void submit()}
            onToggleFeature={() => setFeaturePanelOpen((open) => !open)}
          />
        </section>
      ) : (
        <main className="agent-conversation-main">
          <div className="agent-chat-stream">
            {hasServerMessages && task ? (
              <ServerMessageStream
                activeFeature={activeFeature}
                error={error}
                hasMoreBefore={hasMoreBefore}
                loadingOlder={loadingOlder}
                messages={visibleMessages}
                polling={submitting || polling}
                status={task.status}
                onLoadOlder={() => void loadOlderMessages()}
              />
            ) : (
              <LegacyTaskStream
                activeFeature={activeFeature}
                backendNotice={backendNotice}
                draftSummary={draftSummary}
                error={error}
                evidenceIds={evidenceIds}
                expandedQueries={expandedQueries}
                finalAnswer={finalAnswer}
                goal={goal}
                polling={submitting || polling}
                task={task}
              />
            )}
            {pendingReviews.map((review) => (
              <ReviewMessage key={review.id} review={review} reviewing={reviewing} onDecide={(decision) => void decide(review, decision)} />
            ))}
            {(task?.operations || []).map((operation) => (
              <OperationMessage key={operation.id} operation={operation} undoing={undoing === operation.id} onUndo={() => void undoOperation(operation)} />
            ))}
            {pendingMemoryCandidates.map((memory) => (
              <ChatMessage key={memory.id} role="assistant" title={COPY.memoryConfirm}>
                <MemoryItem memory={memory} busyAction={memoryAction} onAction={(action) => void actOnMemory(memory, action)} />
              </ChatMessage>
            ))}
            {task ? <FinalAnswer task={task} finalAnswer={finalAnswer} draftSummary={draftSummary} /> : null}
          </div>
          <div className="agent-bottom-composer">
            <Composer
              activeFeature={activeFeature}
              error={error}
              featurePanelOpen={featurePanelOpen}
              goal={goal}
              submitting={submitting}
              workspaceMode={workspaceMode}
              featureMenuRef={featureMenuRef}
              onChooseFeature={chooseFeature}
              onGoalChange={setGoal}
              onSubmit={() => void submit()}
              onToggleFeature={() => setFeaturePanelOpen((open) => !open)}
            />
          </div>
        </main>
      )}
    </div>
  );
}

function Composer({
  activeFeature,
  error,
  featureMenuRef,
  featurePanelOpen,
  goal,
  submitting,
  workspaceMode,
  onChooseFeature,
  onGoalChange,
  onSubmit,
  onToggleFeature
}: {
  activeFeature: FeatureOption;
  error: string;
  featureMenuRef: { current: HTMLDivElement | null };
  featurePanelOpen: boolean;
  goal: string;
  submitting: boolean;
  workspaceMode: AgentWorkspaceMode;
  onChooseFeature: (feature: FeatureOption) => void;
  onGoalChange: (goal: string) => void;
  onSubmit: () => void;
  onToggleFeature: () => void;
}) {
  return (
    <section className="agent-composer-v2">
      <div className="agent-composer-box">
        <textarea
          value={goal}
          onChange={(event) => onGoalChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              event.preventDefault();
              onSubmit();
            }
          }}
          placeholder={COPY.composerPlaceholder}
          rows={3}
        />
        <div className="agent-composer-actions">
          <div className="agent-feature-menu" ref={(node) => { featureMenuRef.current = node; }}>
            <button className={featurePanelOpen ? 'agent-feature-trigger is-open' : 'agent-feature-trigger'} onClick={onToggleFeature} type="button">
              {activeFeature.icon}
              <span>{activeFeature.title}</span>
              <ChevronDown size={15} />
            </button>
            {featurePanelOpen ? (
              <div className="agent-feature-waterfall-v2" role="menu">
                {FEATURE_OPTIONS.map((feature) => (
                  <button className={workspaceMode === feature.value ? 'is-active' : ''} key={feature.value} onClick={() => onChooseFeature(feature)} type="button" role="menuitem">
                    <span className="agent-feature-icon">{feature.icon}</span>
                    <span className="agent-feature-copy">
                      <strong>{feature.title}</strong>
                      <small>{feature.description}</small>
                      <span className="agent-feature-tags">
                        {feature.tags.map((tag) => <em key={tag}>{tag}</em>)}
                      </span>
                    </span>
                    {workspaceMode === feature.value ? <Check size={17} /> : null}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <button className="send-button agent-send-v2" onClick={onSubmit} disabled={submitting} type="button" aria-label="\u53d1\u9001\u7ed9 Agent">
            {submitting ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
          </button>
        </div>
      </div>
      {error ? <p className="form-message danger">{error}</p> : null}
    </section>
  );
}

function DetailPanel(props: {
  activeFeature: FeatureOption;
  detailTab: DetailTab;
  evidenceIds: string[];
  expandedQueries: string[];
  historyLoading: boolean;
  historyTasks: AgentTask[];
  memories: AgentMemory[];
  memoryAction: string;
  memoryError: string;
  pendingMemoryCandidates: AgentMemory[];
  pendingReviews: AgentHumanReview[];
  progressSteps: Array<{ label: string; done: boolean; active: boolean }>;
  reviewing: string;
  task: AgentTask | null;
  undoing: string;
  onClose: () => void;
  onDecide: (review: AgentHumanReview, decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED') => void;
  onMemoryAction: (memory: AgentMemory, action: 'confirm' | 'reject' | 'archive' | 'delete') => void;
  onOpenHistory: (taskId: string) => void;
  onRefreshHistory: () => void;
  onSetTab: (tab: DetailTab) => void;
  onUndo: (operation: AgentOperation) => void;
}) {
  return (
    <div className="agent-detail-popover" role="dialog" aria-label="Agent task panel">
      <header className="agent-detail-popover-head">
        <div>
          <strong>{COPY.taskPanel}</strong>
          <span>{props.task ? props.task.title : '\u5c1a\u672a\u521b\u5efa\u4efb\u52a1'}</span>
        </div>
        <button className="icon-button tiny" type="button" onClick={props.onClose} aria-label="\u5173\u95ed\u4efb\u52a1\u9762\u677f">
          <X size={16} />
        </button>
      </header>
      <div className="agent-detail-tabs" role="tablist">
        {DETAIL_TABS.map((tab) => (
          <button className={props.detailTab === tab.value ? 'is-active' : ''} key={tab.value} type="button" onClick={() => props.onSetTab(tab.value)}>
            {tab.icon}
            <span>{tab.label}</span>
          </button>
        ))}
      </div>
      <div className="agent-detail-body">
        {props.detailTab === 'environment' ? <EnvironmentTab activeFeature={props.activeFeature} memories={props.memories} task={props.task} /> : null}
        {props.detailTab === 'progress' ? <ProgressTab steps={props.progressSteps} task={props.task} /> : null}
        {props.detailTab === 'plan' ? <PlanTab activeFeature={props.activeFeature} task={props.task} /> : null}
        {props.detailTab === 'evidence' ? <EvidenceTab evidenceIds={props.evidenceIds} expandedQueries={props.expandedQueries} task={props.task} /> : null}
        {props.detailTab === 'approval' ? (
          <ApprovalTab
            memoryAction={props.memoryAction}
            memoryError={props.memoryError}
            pendingMemoryCandidates={props.pendingMemoryCandidates}
            pendingReviews={props.pendingReviews}
            reviewing={props.reviewing}
            task={props.task}
            undoing={props.undoing}
            onDecide={props.onDecide}
            onMemoryAction={props.onMemoryAction}
            onUndo={props.onUndo}
          />
        ) : null}
        {props.detailTab === 'history' ? <HistoryTab historyLoading={props.historyLoading} historyTasks={props.historyTasks} onOpenHistory={props.onOpenHistory} onRefreshHistory={props.onRefreshHistory} /> : null}
      </div>
    </div>
  );
}

function EnvironmentTab({ activeFeature, memories, task }: { activeFeature: FeatureOption; memories: AgentMemory[]; task: AgentTask | null }) {
  const summaryCount = task?.summaries?.length || task?.summaryCount || 0;
  return (
    <div className="agent-detail-section">
      <MetaItem label="\u5f53\u524d\u529f\u80fd" value={activeFeature.title} />
      <MetaItem label="\u4efb\u52a1\u72b6\u6001" value={statusLabel(task?.status)} />
      <MetaItem label="\u66f4\u65b0\u65f6\u95f4" value={formatTime(task?.updatedAt)} />
      <MetaItem label="\u6700\u8fd1\u8bb0\u5fc6" value={`${memories.length} \u6761`} />
      <MetaItem label="\u5df2\u538b\u7f29\u4e0a\u4e0b\u6587" value={summaryCount ? `${summaryCount} \u6bb5` : '\u6682\u65e0'} />
      {summaryCount ? <p className="agent-detail-note">{`\u65e9\u671f\u4e0a\u4e0b\u6587\u5df2\u538b\u7f29\uff0c\u4fdd\u7559\u6700\u8fd1 ${task?.messages?.length || 0} \u6761\u539f\u6587\u548c\u53ef\u6062\u590d\u6458\u8981\u3002`}</p> : null}
      <p className="agent-detail-note">{'\u9875\u9762\u53ea\u5c55\u793a\u601d\u8003\u6458\u8981\u3001\u6267\u884c\u8bf4\u660e\u548c\u5224\u65ad\u4f9d\u636e\uff0c\u4e0d\u5c55\u793a\u9690\u85cf\u63a8\u7406\u94fe\u3002'}</p>
    </div>
  );
}

function ProgressTab({ steps, task }: { steps: Array<{ label: string; done: boolean; active: boolean }>; task: AgentTask | null }) {
  return (
    <div className="agent-progress-list">
      {steps.map((step) => (
        <div className={step.done ? 'done' : step.active ? 'active' : ''} key={step.label}>
          {step.done ? <CheckCircle2 size={16} /> : step.active ? <Loader2 className="spin" size={16} /> : <Circle size={16} />}
          <span>{step.label}</span>
        </div>
      ))}
      {task?.errorMessage ? <p className="form-message danger">{task.errorMessage}</p> : null}
    </div>
  );
}

function PlanTab({ activeFeature, task }: { activeFeature: FeatureOption; task: AgentTask | null }) {
  const steps = normalizePlanSteps(task?.plan?.steps);
  return (
    <div className="agent-detail-section">
      <strong>{activeFeature.title}</strong>
      {steps.length ? (
        <ol className="agent-compact-list agent-plan-step-list">
          {steps.map((step, index) => (
            <li key={`${index}-${planStepKey(step)}`}>
              <PlanStepItem step={step} />
            </li>
          ))}
        </ol>
      ) : <p className="agent-empty">{'\u540e\u7aef\u5c1a\u672a\u56de\u5199\u8ba1\u5212\u3002'}</p>}
    </div>
  );
}

function EvidenceTab({ evidenceIds, expandedQueries, task }: { evidenceIds: string[]; expandedQueries: string[]; task: AgentTask | null }) {
  return (
    <div className="agent-detail-section">
      <EvidenceSummary evidenceIds={evidenceIds} expandedQueries={expandedQueries} task={task} />
    </div>
  );
}

function ApprovalTab({
  memoryAction,
  memoryError,
  pendingMemoryCandidates,
  pendingReviews,
  reviewing,
  task,
  undoing,
  onDecide,
  onMemoryAction,
  onUndo
}: {
  memoryAction: string;
  memoryError: string;
  pendingMemoryCandidates: AgentMemory[];
  pendingReviews: AgentHumanReview[];
  reviewing: string;
  task: AgentTask | null;
  undoing: string;
  onDecide: (review: AgentHumanReview, decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED') => void;
  onMemoryAction: (memory: AgentMemory, action: 'confirm' | 'reject' | 'archive' | 'delete') => void;
  onUndo: (operation: AgentOperation) => void;
}) {
  return (
    <div className="agent-detail-section">
      {pendingReviews.map((review) => <ReviewControls key={review.id} review={review} reviewing={reviewing} onDecide={(decision) => onDecide(review, decision)} />)}
      {pendingMemoryCandidates.map((memory) => <MemoryItem key={memory.id} memory={memory} busyAction={memoryAction} onAction={(action) => onMemoryAction(memory, action)} />)}
      {(task?.operations || []).map((operation) => <OperationInline key={operation.id} operation={operation} undoing={undoing === operation.id} onUndo={() => onUndo(operation)} />)}
      {!pendingReviews.length && !pendingMemoryCandidates.length && !(task?.operations || []).length ? <p className="agent-empty">{'\u6682\u65e0\u9700\u8981\u5904\u7406\u7684\u5ba1\u6279\u6216\u64a4\u9500\u9879\u3002'}</p> : null}
      {memoryError ? <p className="form-message danger">{memoryError}</p> : null}
    </div>
  );
}

function HistoryTab({ historyLoading, historyTasks, onOpenHistory, onRefreshHistory }: { historyLoading: boolean; historyTasks: AgentTask[]; onOpenHistory: (taskId: string) => void; onRefreshHistory: () => void }) {
  return (
    <div className="agent-detail-section">
      <div className="agent-history-head">
        <strong>{'\u5386\u53f2\u4f1a\u8bdd'}</strong>
        <button className="chip-button" type="button" onClick={onRefreshHistory}>{historyLoading ? '\u5237\u65b0\u4e2d' : '\u5237\u65b0'}</button>
      </div>
      <div className="agent-history-list">
        {historyTasks.map((item) => (
          <button key={item.id} type="button" onClick={() => onOpenHistory(item.id)}>
            <strong>{item.title || '\u672a\u547d\u540d Agent \u4efb\u52a1'}</strong>
            <span>{statusLabel(item.status)} · {formatTime(item.updatedAt)}</span>
          </button>
        ))}
        {!historyTasks.length ? <p className="agent-empty">{historyLoading ? '\u6b63\u5728\u8bfb\u53d6\u5386\u53f2\u4f1a\u8bdd...' : '\u6682\u65e0\u5386\u53f2\u4f1a\u8bdd\u3002'}</p> : null}
      </div>
    </div>
  );
}

function ChatMessage({ role, title, children }: { role: 'user' | 'assistant' | 'system'; title: string; children: ReactNode }) {
  return (
    <section className={`agent-chat-message ${role}`}>
      <div className="agent-avatar">{role === 'user' ? '\u6211' : <Bot size={16} />}</div>
      <div className="agent-message-body">
        <strong>{title}</strong>
        {children}
      </div>
    </section>
  );
}

function ServerMessageStream({
  activeFeature,
  error,
  hasMoreBefore,
  loadingOlder,
  messages,
  polling,
  status,
  onLoadOlder
}: {
  activeFeature: FeatureOption;
  error: string;
  hasMoreBefore: boolean;
  loadingOlder: boolean;
  messages: AgentChatMessage[];
  polling: boolean;
  status?: string;
  onLoadOlder: () => void;
}) {
  return (
    <>
      {hasMoreBefore ? (
        <div className="agent-load-older-row">
          <button className="chip-button" type="button" onClick={onLoadOlder} disabled={loadingOlder}>
            {loadingOlder ? <Loader2 className="spin" size={15} /> : <History size={15} />}
            <span>{loadingOlder ? '正在加载更早消息' : '加载更早消息'}</span>
          </button>
        </div>
      ) : null}
      {messages.map((message) => <ServerMessage key={message.id || `${message.messageType}-${message.dedupeKey}`} activeFeature={activeFeature} message={message} polling={polling} status={status} />)}
      {error ? (
        <ChatMessage role="assistant" title={COPY.executionNote}>
          <p className="form-message danger">{error}</p>
        </ChatMessage>
      ) : null}
    </>
  );
}

function ServerMessage({ activeFeature, message, polling, status }: { activeFeature: FeatureOption; message: AgentChatMessage; polling: boolean; status?: string }) {
  const role = normalizeMessageRole(message.role);
  const title = messageTitle(message);
  if (message.messageType === 'USER_GOAL') {
    return (
      <ChatMessage role="user" title={COPY.userGoal}>
        <p>{displayUserGoal(message.content)}</p>
        <div className="query-tags agent-tags">
          <span>{activeFeature.title}</span>
          {activeFeature.tags.map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      </ChatMessage>
    );
  }
  if (message.messageType === 'TOOL_OBSERVATION') {
    const payload = normalizeRecord(message.payload);
    return (
      <ChatMessage role="assistant" title={title}>
        <div className="agent-tool-call-card">
          <div className="agent-timeline-head">
            <strong>{String(payload.toolName || message.sourceId || '\u5de5\u5177')}</strong>
            {payload.status ? <span className={`status-pill ${taskStatusClass(String(payload.status))}`}>{statusLabel(String(payload.status))}</span> : null}
          </div>
          <p>{message.content}</p>
          {payload.errorMessage ? <small>{String(payload.errorCode || '')}: {String(payload.errorMessage)}</small> : null}
        </div>
      </ChatMessage>
    );
  }
  if (message.messageType === 'FINAL_ANSWER') {
    return (
      <ChatMessage role="assistant" title={title}>
        <MarkdownText className="answer-copy" content={message.content} />
      </ChatMessage>
    );
  }
  if (message.messageType === 'ERROR') {
    return (
      <ChatMessage role="assistant" title={title}>
        <p className="form-message danger">{message.content}</p>
      </ChatMessage>
    );
  }
  if (message.messageType === 'CONTEXT_SUMMARY') {
    const payload = normalizeRecord(message.payload);
    return (
      <ChatMessage role="system" title={COPY.contextSummary}>
        <div className="agent-status-line">
          <span className="status-pill neutral">{String(payload.status || '\u5df2\u4fdd\u5b58')}</span>
          <span>{message.content}</span>
        </div>
      </ChatMessage>
    );
  }
  return (
    <ChatMessage role={role} title={title}>
      <div className="agent-status-line">
        {message.messageType === 'STATUS' ? <span className={`status-pill ${taskStatusClass(status)}`}>{statusIcon(status, polling)}{statusLabel(status)}</span> : null}
        <span>{message.content}</span>
      </div>
    </ChatMessage>
  );
}

function LegacyTaskStream({
  activeFeature,
  backendNotice,
  draftSummary,
  error,
  evidenceIds,
  expandedQueries,
  finalAnswer,
  goal,
  polling,
  task
}: {
  activeFeature: FeatureOption;
  backendNotice: string;
  draftSummary: string;
  error: string;
  evidenceIds: string[];
  expandedQueries: string[];
  finalAnswer: string;
  goal: string;
  polling: boolean;
  task: AgentTask | null;
}) {
  return (
    <>
      <ChatMessage role="user" title={COPY.userGoal}>
        <p>{displayUserGoal(task?.input?.goal || goal)}</p>
        <div className="query-tags agent-tags">
          <span>{activeFeature.title}</span>
          {activeFeature.tags.map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      </ChatMessage>
      {backendNotice || error ? (
        <ChatMessage role="assistant" title={COPY.executionNote}>
          <div className="agent-status-line">
            <span className={`status-pill ${taskStatusClass(task?.status)}`}>{statusIcon(task?.status, polling)}{statusLabel(task?.status)}</span>
            {backendNotice ? <span>{backendNotice}</span> : null}
          </div>
          {error ? <p className="form-message danger">{error}</p> : null}
        </ChatMessage>
      ) : null}
      {(task?.toolCalls || []).map((call) => <ToolCallMessage key={call.id} call={call} />)}
      {evidenceIds.length || expandedQueries.length ? (
        <ChatMessage role="assistant" title={COPY.evidence}>
          <EvidenceSummary evidenceIds={evidenceIds} expandedQueries={expandedQueries} task={task} />
        </ChatMessage>
      ) : null}
      {task ? <PlanningResult task={task} /> : null}
      {task ? <FinalAnswer task={task} finalAnswer={finalAnswer} draftSummary={draftSummary} /> : null}
    </>
  );
}

function ToolCallMessage({ call }: { call: AgentToolCall }) {
  return (
    <ChatMessage role="assistant" title={COPY.toolCall}>
      <div className="agent-tool-call-card">
        <div className="agent-timeline-head">
          <strong>{call.toolName}</strong>
          <span className={`status-pill ${taskStatusClass(call.status)}`}>{statusLabel(call.status)}</span>
        </div>
        <p>{formatToolResponse(call.response)}</p>
        {call.errorMessage ? <small>{call.errorCode}: {call.errorMessage}</small> : null}
      </div>
    </ChatMessage>
  );
}

function EvidenceSummary({ evidenceIds, expandedQueries, task }: { evidenceIds: string[]; expandedQueries: string[]; task: AgentTask | null }) {
  const diagnostics = normalizeRecord(task?.final?.diagnostics || task?.draft?.diagnostics);
  return (
    <div className="agent-evidence-panel">
      <div className="query-tags agent-tags">
        {evidenceIds.map((id) => <span key={id}>Evidence {id}</span>)}
        {!evidenceIds.length ? <span>{'\u6682\u65e0 evidence \u56de\u5199'}</span> : null}
      </div>
      {expandedQueries.length ? (
        <div className="query-tags agent-tags">
          {expandedQueries.map((query) => <span key={query}>{query}</span>)}
        </div>
      ) : null}
      {Object.keys(diagnostics).length ? <p>{formatToolResponse(diagnostics)}</p> : null}
    </div>
  );
}

function ReviewMessage({ review, reviewing, onDecide }: { review: AgentHumanReview; reviewing: string; onDecide: (decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED') => void }) {
  return (
    <ChatMessage role="assistant" title={reviewTitle(review.reviewType)}>
      <ReviewControls review={review} reviewing={reviewing} onDecide={onDecide} />
    </ChatMessage>
  );
}

function ReviewControls({ review, reviewing, onDecide }: { review: AgentHumanReview; reviewing: string; onDecide: (decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED') => void }) {
  return (
    <div className="agent-review-card inline">
      <ReviewProposal review={review} />
      <div className="agent-review-actions">
        <button className="primary-action" onClick={() => onDecide('APPROVED')} disabled={Boolean(reviewing)} type="button">
          {reviewing === `${review.id}-APPROVED` ? <Loader2 className="spin" size={16} /> : <CheckCircle2 size={16} />}<span>{'\u6279\u51c6'}</span>
        </button>
        <button className="ghost-action" onClick={() => onDecide('CHANGES_REQUESTED')} disabled={Boolean(reviewing)} type="button">
          <Clock3 size={16} /><span>{'\u8981\u6c42\u4fee\u6539'}</span>
        </button>
        <button className="ghost-action" onClick={() => onDecide('REJECTED')} disabled={Boolean(reviewing)} type="button">
          <XCircle size={16} /><span>{'\u62d2\u7edd'}</span>
        </button>
      </div>
    </div>
  );
}

function FinalAnswer({ task, finalAnswer, draftSummary }: { task: AgentTask; finalAnswer: string; draftSummary: string }) {
  const content = finalAnswer || draftSummary;
  if (!content && !task.errorMessage) return null;
  return (
    <ChatMessage role="assistant" title={COPY.finalAnswer}>
      {content ? <MarkdownText className="answer-copy" content={content} /> : <p className="form-message danger">{formatTaskError(task)}</p>}
    </ChatMessage>
  );
}

function ReviewProposal({ review }: { review: AgentHumanReview }) {
  const proposal = review.proposal || {};
  return (
    <div className="agent-review-proposal">
      <strong>{stringValue(proposal.title) || stringValue(proposal.summary) || '\u5f85\u786e\u8ba4\u5185\u5bb9'}</strong>
      {Array.isArray(proposal.steps) ? (
        <ol className="agent-plan-step-list">
          {proposal.steps.map((step, index) => (
            <li key={`${index}-${planStepKey(step)}`}>
              <PlanStepItem step={step} />
            </li>
          ))}
        </ol>
      ) : null}
      <div className="query-tags agent-tags">
        {normalizeStringList(proposal.tools).map((tool) => <span key={tool}>{tool}</span>)}
        {normalizeStringList(proposal.internalSubgraphs).map((subgraph) => <span key={subgraph}>{'\u5b50\u56fe'} {subgraph}</span>)}
        {proposal.toolName ? <span>{String(proposal.toolName)}</span> : null}
        {proposal.riskLevel ? <span>{'\u98ce\u9669'} {String(proposal.riskLevel)}</span> : null}
        {proposal.evidenceCount !== undefined ? <span>{'\u8bc1\u636e'} {String(proposal.evidenceCount)}</span> : null}
        {proposal.undoable ? <span>{'\u53ef\u64a4\u9500'}</span> : null}
      </div>
      {proposal.summary ? <p>{String(proposal.summary)}</p> : null}
    </div>
  );
}

function PlanStepItem({ step }: { step: unknown }) {
  if (!step || typeof step !== 'object' || Array.isArray(step)) {
    return <span>{String(step || '\u5f85\u6267\u884c\u6b65\u9aa4')}</span>;
  }
  const item = step as Record<string, unknown>;
  const description = stringValue(item.description) || stringValue(item.title) || '\u5f85\u6267\u884c\u6b65\u9aa4';
  return (
    <div className="agent-plan-step">
      <strong>{description}</strong>
      <div className="query-tags agent-tags">
        {item.toolName ? <span>{String(item.toolName)}</span> : null}
        {item.expectedOutput ? <span>{String(item.expectedOutput)}</span> : null}
        {item.riskLevel ? <span>{'\u98ce\u9669'} {String(item.riskLevel)}</span> : null}
      </div>
    </div>
  );
}

function OperationMessage({ operation, undoing, onUndo }: { operation: AgentOperation; undoing: boolean; onUndo: () => void }) {
  return (
    <ChatMessage role="assistant" title={COPY.changeOperation}>
      <OperationInline operation={operation} undoing={undoing} onUndo={onUndo} />
    </ChatMessage>
  );
}

function OperationInline({ operation, undoing, onUndo }: { operation: AgentOperation; undoing: boolean; onUndo: () => void }) {
  const undoable = operation.status === 'APPLIED_UNDOABLE' && !undoExpired(operation.undoDeadline);
  return (
    <div className="agent-operation-row">
      <div>
        <div className="agent-timeline-head">
          <strong>{operation.operationType}</strong>
          <span className={`status-pill ${operationStatusClass(operation.status)}`}>{operationStatusLabel(operation.status)}</span>
        </div>
        <p>{operation.resourceType} · {operation.resourceId}</p>
        {operation.errorMessage ? <small>{operation.errorCode}: {operation.errorMessage}</small> : null}
      </div>
      <button className="ghost-action" onClick={onUndo} disabled={!undoable || undoing} type="button">
        {undoing ? <Loader2 className="spin" size={16} /> : <RotateCcw size={16} />}<span>{undoable ? '\u64a4\u9500' : '\u4e0d\u53ef\u64a4\u9500'}</span>
      </button>
    </div>
  );
}

function MemoryItem({ memory, busyAction, onAction }: { memory: AgentMemory; busyAction: string; onAction: (action: 'confirm' | 'reject' | 'archive' | 'delete') => void }) {
  const isPending = memory.status === 'PENDING_REVIEW' || memory.status === 'INDEX_FAILED';
  const canArchive = !['ARCHIVED', 'DELETED', 'REJECTED', 'SUPERSEDED'].includes(memory.status);
  const busy = (action: string) => busyAction === `${memory.id}-${action}`;
  return (
    <div className={`agent-memory-row ${memoryStatusClass(memory.status)}`}>
      <div className="agent-memory-row-main">
        <div className="agent-timeline-head">
          <strong>{memory.summary || memory.subjectKey}</strong>
          <span className={`status-pill ${memoryStatusClass(memory.status)}`}>{memoryStatusLabel(memory.status)}</span>
        </div>
        <p>{memory.content || '\u65e0\u6b63\u6587'}</p>
      </div>
      <div className="agent-memory-actions">
        {isPending ? (
          <>
            <button className="chip-button is-active" onClick={() => onAction('confirm')} disabled={Boolean(busyAction)} type="button">
              {busy('confirm') ? <Loader2 className="spin" size={15} /> : <CheckCircle2 size={15} />}{'\u786e\u8ba4'}
            </button>
            <button className="chip-button danger" onClick={() => onAction('reject')} disabled={Boolean(busyAction)} type="button">
              {busy('reject') ? <Loader2 className="spin" size={15} /> : <XCircle size={15} />}{'\u62d2\u7edd'}
            </button>
          </>
        ) : null}
        {canArchive ? (
          <button className="chip-button" onClick={() => onAction('archive')} disabled={Boolean(busyAction)} type="button">
            {busy('archive') ? <Loader2 className="spin" size={15} /> : <Archive size={15} />}{'\u5f52\u6863'}
          </button>
        ) : null}
        {memory.status !== 'DELETED' ? (
          <button className="chip-button danger" onClick={() => onAction('delete')} disabled={Boolean(busyAction)} type="button">
            {busy('delete') ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}{'\u5220\u9664'}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function PlanningResult({ task }: { task: AgentTask }) {
  const source = task.final?.alignment ? task.final : task.draft;
  const alignment = Array.isArray(source?.alignment) ? source.alignment : [];
  const gaps = Array.isArray(source?.gaps) ? source.gaps : [];
  const webReferences = Array.isArray(source?.webReferences) ? source.webReferences : [];
  if (!alignment.length && !gaps.length && !webReferences.length) return null;
  return (
    <ChatMessage role="assistant" title={COPY.reasoningSummary}>
      <div className="agent-planning-result">
        {alignment.length ? (
          <div>
            <h4>{'\u8bc1\u636e\u5bf9\u9f50'}</h4>
            <div className="agent-alignment-list">
              {alignment.map((item) => {
                const entry = item as Record<string, unknown>;
                const status = stringValue(entry.status);
                return (
                  <div className="agent-alignment-row" key={`${String(entry.requirement)}-${status}`}>
                    <span className={`evidence-status ${alignmentStatusClass(status)}`}>{alignmentStatusLabel(status)}</span>
                    <strong>{String(entry.requirement || '\u672a\u547d\u540d\u8981\u6c42')}</strong>
                    <small>{String(entry.reason || '')}</small>
                    <div className="query-tags agent-tags">{normalizeStringList(entry.evidenceIds).map((id) => <span key={id}>{id}</span>)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
        {gaps.length ? (
          <div>
            <h4>{'\u80fd\u529b\u7f3a\u53e3'}</h4>
            <div className="agent-gap-list">
              {gaps.map((item) => {
                const gap = item as Record<string, unknown>;
                return (
                  <div className="agent-gap-row" key={String(gap.skill)}>
                    <strong>{String(gap.skill || '\u5f85\u8865\u5145\u80fd\u529b')}</strong>
                    <span>{String(gap.priority || 'MEDIUM')}</span>
                    <p>{String(gap.suggestion || '')}</p>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
        {webReferences.length ? (
          <div className="agent-web-reference-section">
            <h4>{'\u8054\u7f51\u53c2\u8003'}</h4>
            <div className="agent-web-reference-list">
              {webReferences.map((item) => {
                const reference = item as Record<string, unknown>;
                return (
                  <a className="agent-web-reference-row" href={String(reference.sourceUrl || '#')} target="_blank" rel="noreferrer" key={String(reference.sourceUrl || reference.title)}>
                    <strong>{String(reference.title || '\u5916\u90e8\u53c2\u8003')}</strong>
                    <span>{String(reference.confidence || 'LOW')} · {String(reference.score ?? '')}</span>
                    <p>{String(reference.summary || '')}</p>
                  </a>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>
    </ChatMessage>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="agent-meta-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function findFeature(mode: AgentWorkspaceMode) {
  return FEATURE_OPTIONS.find((item) => item.value === mode) || FEATURE_OPTIONS[0];
}

function normalizeMode(value: unknown): AgentWorkspaceMode {
  if (value === 'jd_video') return 'free_explore';
  return value === 'free_explore' ? 'free_explore' : 'read';
}

function buildToolHints(mode: AgentWorkspaceMode) {
  if (mode === 'free_explore') return ['web_search_probe', 'rag_query_probe_non_persistent'];
  return ['rag_query_probe_non_persistent'];
}

function buildGoalForMode(mode: AgentWorkspaceMode, goal: string) {
  if (mode === 'free_explore') {
    return [
      goal,
      '',
      '\u8bf7\u6309\u81ea\u7531\u63a2\u7d22\u6a21\u5f0f\u5904\u7406\uff1a\u6570\u636e\u6765\u6e90\u4f18\u5148\u4f7f\u7528\u8054\u7f51\u67e5\u8be2\uff0cRAG \u53ea\u4f5c\u4e3a\u672c\u5730 evidence \u8865\u5145\u6216\u8054\u7f51\u4e0d\u53ef\u7528\u65f6\u7684\u964d\u7ea7\u8def\u5f84\u3002',
      '\u53ef\u4ee5\u89e3\u91ca\u3001\u6574\u7406\u677e\u6563\u8d44\u6599\u3001\u7ed3\u5408 JD \u548c\u89c6\u9891\u5b66\u4e60\u8bc1\u636e\uff0c\u7ed9\u51fa\u8d44\u6599\u6807\u9898\u3001\u6807\u7b7e\u3001\u6458\u8981\u548c\u4e0b\u4e00\u6b65\u5efa\u8bae\u3002',
      '\u5982\u9700\u5199\u5165\u77e5\u8bc6\u5e93\uff0c\u53ea\u63d0\u4f9b\u4e0a\u4f20\u6216\u5165\u5e93\u5efa\u8bae\uff0c\u4e0d\u76f4\u63a5\u6267\u884c\u5199\u5165\u6216\u72b6\u6001\u53d8\u66f4\u3002'
    ].join('\n');
  }
  return goal;
}

function buildProgressSteps(task: AgentTask | null) {
  const status = task?.status || 'IDLE';
  const hasTools = Boolean(task?.toolCalls?.length);
  const hasReview = Boolean(task?.reviews?.length);
  const hasFinal = Boolean(task?.final && Object.keys(task.final).length);
  const hasPlan = Boolean(task?.plan && Object.keys(task.plan).length);
  return [
    { label: '\u521b\u5efa\u4efb\u52a1', done: Boolean(task) && status !== 'CREATING', active: !task || status === 'CREATING' },
    { label: '\u6574\u7406\u8ba1\u5212', done: hasPlan, active: status === 'CREATED' || (status === 'RUNNING' && !hasPlan) },
    { label: '\u8c03\u7528\u5de5\u5177', done: hasTools, active: status === 'WAITING_TOOL_RESULT' },
    { label: '\u786e\u8ba4\u5ba1\u6279', done: hasReview && !task?.reviews?.some((review) => review.status === 'PENDING'), active: status.includes('REVIEW') },
    { label: '\u751f\u6210\u6700\u7ec8\u56de\u7b54', done: hasFinal || status === 'COMPLETED', active: status === 'RUNNING' && hasTools }
  ];
}

function buildBackendNotice(task: AgentTask) {
  if (task.errorMessage) return formatTaskError(task);
  if (task.status === 'COMPLETED') return '';
  if (task.status === 'CANCELED') return '\u4efb\u52a1\u5df2\u53d6\u6d88\u3002';
  const draft = normalizeRecord(task.draft);
  const draftMessage = stringValue(draft.message);
  if (draftMessage) return draftMessage;
  if (task.status === 'CREATING') return '\u6b63\u5728\u521b\u5efa Agent \u4efb\u52a1\uff0c\u4efb\u52a1 ID \u8fd4\u56de\u540e\u4f1a\u81ea\u52a8\u63a5\u5165\u4e8b\u4ef6\u6d41\u3002';
  if (task.status === 'CREATED') return '\u4efb\u52a1\u5df2\u521b\u5efa\uff0c\u6b63\u5728\u542f\u52a8 Python Agent\u3002';
  if (task.status === 'RUNNING') {
    if (!task.plan || !Object.keys(task.plan).length) {
      return '\u540e\u7aef\u5df2\u63a5\u6536\u4efb\u52a1\uff0cAgent \u6b63\u5728\u751f\u6210\u8ba1\u5212\u548c\u5de5\u5177\u8def\u7ebf\u3002';
    }
    if (!task.toolCalls?.length) {
      return '\u8ba1\u5212\u5df2\u56de\u5199\uff0cAgent \u6b63\u5728\u51c6\u5907\u6267\u884c\u5de5\u5177\u3002';
    }
    return '\u5de5\u5177\u89c2\u5bdf\u5df2\u56de\u5199\uff0cAgent \u6b63\u5728\u6574\u5408\u7ed3\u679c\u3002';
  }
  if (task.status === 'WAITING_TOOL_RESULT') return '\u5de5\u5177\u8bf7\u6c42\u5df2\u53d1\u8d77\uff0c\u6b63\u5728\u7b49\u5f85 Java Tool Gateway \u56de\u5199\u89c2\u5bdf\u7ed3\u679c\u3002';
  if (task.status === 'WAITING_PLAN_REVIEW') return '\u89c4\u5212\u5668\u5df2\u751f\u6210\u6267\u884c\u8def\u7ebf\uff0c\u7b49\u5f85\u4f60\u6279\u51c6\u6216\u8981\u6c42\u4fee\u6539\u3002';
  if (task.status === 'WAITING_OUTPUT_REVIEW') return '\u8f93\u51fa\u8349\u7a3f\u5df2\u751f\u6210\uff0c\u7b49\u5f85\u4f60\u786e\u8ba4\u540e\u518d\u5b8c\u6210\u4efb\u52a1\u3002';
  if (task.status === 'WAITING_CRUD_REVIEW') return '\u68c0\u6d4b\u5230\u53d8\u66f4\u610f\u56fe\uff0c\u6b63\u7b49\u5f85\u4f60\u5ba1\u6279\u5177\u4f53\u5199\u64cd\u4f5c\u3002';
  return '';
}

function formatTaskError(task: AgentTask) {
  const code = stringValue(task.errorCode);
  const message = stringValue(task.errorMessage);
  if (code && message) return `${code}: ${message}`;
  return message || code || '后端未返回错误详情';
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    IDLE: '\u672a\u521b\u5efa',
    CREATING: '\u521b\u5efa\u4e2d',
    CREATED: '\u5df2\u521b\u5efa',
    RUNNING: '\u8fd0\u884c\u4e2d',
    WAITING_TOOL_RESULT: '\u7b49\u5f85\u5de5\u5177',
    WAITING_PLAN_REVIEW: '\u7b49\u5f85\u8ba1\u5212\u5ba1\u6279',
    WAITING_CRUD_REVIEW: '\u7b49\u5f85\u53d8\u66f4\u5ba1\u6279',
    WAITING_OUTPUT_REVIEW: '\u7b49\u5f85\u8f93\u51fa\u786e\u8ba4',
    COMPLETED: '\u5df2\u5b8c\u6210',
    CANCELED: '\u5df2\u53d6\u6d88',
    FAILED: '\u5931\u8d25',
    SUCCEEDED: '\u6210\u529f',
    REJECTED: '\u5df2\u62d2\u7edd',
    PENDING: '\u7b49\u5f85\u4e2d',
    APPLIED_UNDOABLE: '\u53ef\u64a4\u9500',
    UNDONE: '\u5df2\u64a4\u9500',
    UNDO_EXPIRED: '\u64a4\u9500\u8fc7\u671f'
  };
  return labels[status || 'IDLE'] || status || '\u672a\u77e5';
}

function statusIcon(status: string | undefined, active: boolean) {
  if (active) return <Loader2 className="spin" size={15} />;
  if (status === 'COMPLETED') return <CheckCircle2 size={15} />;
  if (status === 'FAILED' || status === 'REJECTED') return <XCircle size={15} />;
  return <Clock3 size={15} />;
}

function normalizeMessageRole(role?: string): 'user' | 'assistant' | 'system' {
  if (role === 'USER') return 'user';
  if (role === 'SYSTEM') return 'system';
  return 'assistant';
}

function messageTitle(message: AgentChatMessage) {
  const titles: Record<string, string> = {
    USER_GOAL: COPY.userGoal,
    STATUS: COPY.executionNote,
    TOOL_OBSERVATION: COPY.toolCall,
    CONTEXT_SUMMARY: COPY.contextSummary,
    PLAN_REVIEW: '\u8ba1\u5212\u786e\u8ba4',
    OUTPUT_REVIEW: '\u8f93\u51fa\u786e\u8ba4',
    FINAL_ANSWER: COPY.finalAnswer,
    ERROR: '\u6267\u884c\u5931\u8d25',
    REVIEW_DECISION: '\u5ba1\u6279\u51b3\u7b56',
    OPERATION_UNDO: '\u64a4\u9500\u64cd\u4f5c'
  };
  return titles[message.messageType] || COPY.executionNote;
}

function taskStatusClass(status?: string) {
  if (status === 'COMPLETED' || status === 'SUCCEEDED') return 'indexed';
  if (status === 'FAILED' || status === 'REJECTED') return 'danger';
  if (status === 'CREATING' || status === 'RUNNING' || status === 'WAITING_TOOL_RESULT' || status === 'WAITING_PLAN_REVIEW' || status === 'WAITING_CRUD_REVIEW' || status === 'WAITING_OUTPUT_REVIEW' || status === 'PENDING') return 'running';
  return '';
}

function reviewTitle(reviewType: string) {
  if (reviewType === 'OUTPUT') return '\u8f93\u51fa\u786e\u8ba4';
  if (reviewType === 'CRUD') return '\u53d8\u66f4\u786e\u8ba4';
  return '\u8ba1\u5212\u786e\u8ba4';
}

function operationStatusLabel(status: string) {
  return statusLabel(status);
}

function operationStatusClass(status: string) {
  if (status === 'APPLIED_UNDOABLE' || status === 'UNDONE') return 'indexed';
  if (status === 'FAILED' || status === 'UNDO_EXPIRED') return 'danger';
  return 'running';
}

function memoryStatusLabel(status: string) {
  const labels: Record<string, string> = {
    PENDING_REVIEW: '\u5f85\u786e\u8ba4',
    PENDING_INDEX: '\u5f85\u7d22\u5f15',
    ACTIVE: '\u5df2\u6fc0\u6d3b',
    INDEX_FAILED: '\u7d22\u5f15\u5931\u8d25',
    ARCHIVED: '\u5df2\u5f52\u6863',
    SUPERSEDED: '\u5df2\u66ff\u6362',
    REJECTED: '\u5df2\u62d2\u7edd',
    DELETED: '\u5df2\u5220\u9664'
  };
  return labels[status] || status || '\u672a\u77e5';
}

function memoryStatusClass(status: string) {
  if (status === 'ACTIVE') return 'indexed';
  if (status === 'PENDING_REVIEW' || status === 'PENDING_INDEX' || status === 'INDEX_FAILED') return 'running';
  if (status === 'DELETED' || status === 'REJECTED') return 'danger';
  return '';
}

function alignmentStatusClass(status: string) {
  if (status === 'supported') return 'supported';
  if (status === 'weak') return 'weak';
  return 'missing';
}

function alignmentStatusLabel(status: string) {
  if (status === 'supported') return '\u5df2\u652f\u6301';
  if (status === 'weak') return '\u8bc1\u636e\u504f\u5f31';
  return '\u7f3a\u8bc1\u636e';
}

function formatToolResponse(response?: Record<string, unknown> | null) {
  if (!response) return '\u6682\u65e0\u8f93\u51fa\u6458\u8981';
  const parts = [
    response.evidenceCount !== undefined ? `\u8bc1\u636e ${response.evidenceCount}` : '',
    response.answerLength !== undefined ? `\u56de\u7b54\u957f\u5ea6 ${response.answerLength}` : '',
    response.expandedQueryCount !== undefined ? `\u6269\u5c55\u67e5\u8be2 ${response.expandedQueryCount}` : '',
    response.operationId !== undefined ? `\u64cd\u4f5c ${response.operationId}` : '',
    response.undoDeadline !== undefined ? `\u64a4\u9500 ${formatTime(String(response.undoDeadline))}` : '',
    Array.isArray(response.diagnosticKeys) ? `\u8bca\u65ad ${response.diagnosticKeys.length}` : '',
    response.summary ? String(response.summary) : ''
  ].filter(Boolean);
  if (parts.length) return parts.join(' · ');
  const keys = Object.keys(response).slice(0, 5);
  return keys.length ? `\u8fd4\u56de\u5b57\u6bb5\uff1a${keys.join('\u3001')}` : '\u5de5\u5177\u5df2\u8fd4\u56de\u7ed3\u679c';
}

function normalizePlanSteps(value: unknown): unknown[] {
  return Array.isArray(value) ? value.filter((item) => item !== null && item !== undefined && String(item).trim() !== '') : [];
}

function normalizeStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

function uniqueList(items: string[]) {
  return Array.from(new Set(items.filter(Boolean)));
}

function mergeMessages(first: AgentChatMessage[], second: AgentChatMessage[]) {
  const merged = new Map<string, AgentChatMessage>();
  [...first, ...second].forEach((message) => {
    const key = message.dedupeKey || `${message.sourceEventType || ''}-${message.sourceId || ''}-${message.content}` || message.id;
    const existing = merged.get(key);
    if (!existing || (message.sequenceNo !== null && message.sequenceNo !== undefined)) {
      merged.set(key, message);
    }
  });
  return Array.from(merged.values()).sort((left, right) => {
    const leftSeq = left.sequenceNo ?? Number.MAX_SAFE_INTEGER;
    const rightSeq = right.sequenceNo ?? Number.MAX_SAFE_INTEGER;
    if (leftSeq !== rightSeq) return leftSeq - rightSeq;
    return new Date(left.createdAt || 0).getTime() - new Date(right.createdAt || 0).getTime();
  });
}

function oldestMessageSequence(messages: AgentChatMessage[]) {
  const sequences = messages.map((message) => message.sequenceNo).filter((value): value is number => typeof value === 'number');
  return sequences.length ? Math.min(...sequences) : null;
}

function streamDedupeKey(event: AgentStreamEvent, draft: Record<string, unknown>, message: string) {
  if (event.toolCallId) return `tool_${event.toolCallId}`;
  const reviewRequest = normalizeRecord(event.reviewRequest);
  const reviewId = stringValue(reviewRequest.id);
  if (reviewId) return `review_${reviewId}`;
  if (event.eventType === 'TASK_COMPLETED') return 'final_answer';
  if (event.eventType === 'TASK_FAILED') return 'task_failed';
  const source = streamSourceKey(event, draft);
  return `event_${event.eventType.toLowerCase()}_${javaStringHashHex(`${source}|${message}`)}`;
}

function streamSourceKey(event: AgentStreamEvent, draft: Record<string, unknown>) {
  if (event.toolCallId) return event.toolCallId;
  const reviewRequest = normalizeRecord(event.reviewRequest);
  const reviewId = stringValue(reviewRequest.id);
  if (reviewId) return reviewId;
  return [
    event.eventType || 'event',
    stringValue(draft.node) || 'node',
    stringValue(draft.phase) || 'phase',
    stringValue(draft.progressStatus) || 'status'
  ].join(':');
}

function javaStringHashHex(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash * 31) + value.charCodeAt(index)) | 0;
  }
  return (hash >>> 0).toString(16);
}

function streamEventTitle(eventType: string) {
  const titles: Record<string, string> = {
    AGENT_NODE_STARTED: '节点开始执行',
    AGENT_NODE_DELTA: '节点输出更新',
    AGENT_NODE_COMPLETED: '节点执行完成',
    TOOL_CALL_STARTED: '工具调用开始',
    TOOL_CALL_COMPLETED: '工具调用完成',
    CONTEXT_COMPRESSED: '上下文已压缩',
    CONTEXT_RECALLED: '上下文已回捞',
    TASK_FAILED: '任务执行失败'
  };
  return titles[eventType] || '';
}

function normalizeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : '';
}

function displayUserGoal(value: unknown) {
  return stringValue(value).split('\n\n')[0] || '\u672a\u8fd4\u56de\u7528\u6237\u76ee\u6807';
}

function planStepKey(step: unknown) {
  if (!step || typeof step !== 'object' || Array.isArray(step)) return String(step || 'step');
  const item = step as Record<string, unknown>;
  return [item.description, item.title, item.toolName, item.expectedOutput].map((value) => String(value || '')).join('-') || 'step';
}

function formatTime(value?: string | null) {
  if (!value) return '\u672a\u8fd4\u56de';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function undoExpired(value?: string | null) {
  if (!value) return true;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) || date.getTime() <= Date.now();
}

function notifyConversationTreeChanged() {
  window.dispatchEvent(new Event('agent-conversations-updated'));
}
