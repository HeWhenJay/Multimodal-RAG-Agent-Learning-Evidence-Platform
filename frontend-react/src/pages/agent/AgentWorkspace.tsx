import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from '@headlessui/react';
import { Archive, BookOpenCheck, Bot, Check, CheckCircle2, ChevronDown, Clock3, Database, Download, ExternalLink, FileText, Highlighter, Loader2, MessageCircle, RotateCcw, Save, Search, Send, ShieldCheck, Sparkles, ThumbsUp, Trash2, TriangleAlert, Upload, XCircle } from 'lucide-react';
import { type ReactNode, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { archiveAgentMemory, confirmAgentMemory, createAgentTask, decideAgentReview, deleteAgentMemory, fetchAgentMemories, fetchAgentTask, fetchAgentTools, rejectAgentMemory, undoAgentOperation } from '../../api/agent';
import { deleteResumeTemplate, exportResumeTemplate, fetchMaterial, fetchMaterials, fetchResumeTemplates, generateResumePatches, uploadResumeTemplate, validateResumePatches } from '../../api/rag';
import type { AgentHumanReview, AgentMemory, AgentOperation, AgentTask, AgentToolCall, AgentToolDefinition, LearningMaterial, ResumeContentPatch, ResumePatchDraft, ResumeTemplate, ResumeTemplateExport } from '../../api/types';
import { MarkdownText } from '../../components/MarkdownText';

const TERMINAL_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELED']);

type AgentWorkspaceMode = 'read' | 'planning' | 'general';
type ReadToolMode = 'rag' | 'coverage';

interface AgentOption<T extends string> {
  value: T;
  label: string;
  description: string;
  badge?: string;
  icon?: ReactNode;
}

const MODE_OPTIONS: Array<AgentOption<AgentWorkspaceMode> & { features: string[] }> = [
  {
    value: 'read',
    label: '只读问答',
    description: '从当前知识库检索并回答，保留 evidence 引用，不写入查询历史或业务数据。',
    badge: '无需审批',
    icon: <Search size={18} />,
    features: ['RAG 非持久化探针', '检索覆盖诊断', '只读工具观察']
  },
  {
    value: 'planning',
    label: 'JD 适配规划',
    description: '分析岗位 JD、简历摘要和学习证据，生成证据对齐、能力缺口、学习建议和可审批草稿。',
    badge: '计划审批',
    icon: <BookOpenCheck size={18} />,
    features: ['JD/简历证据对齐', '能力缺口分析', '输出确认与保存审批']
  },
  {
    value: 'general',
    label: '通用探索',
    description: '用于闲聊式提问、整理松散学习资料、生成资料入库建议；真正写入 RAG 库仍走资料上传入口。',
    badge: '探索模式',
    icon: <MessageCircle size={18} />,
    features: ['松散问题整理', '学习资料建议', '入库路径提示']
  }
];

const TOOL_MODE_OPTIONS: Array<AgentOption<ReadToolMode>> = [
  {
    value: 'rag',
    label: 'RAG 非持久化探针',
    description: '临时检索当前用户知识库，返回答案、引用和扩展查询，不写 rag_query_history。',
    badge: '推荐'
  },
  {
    value: 'coverage',
    label: '检索覆盖诊断',
    description: '查看召回分布、资料类型覆盖和证据数量，适合判断资料是否需要补充。',
    badge: '诊断'
  }
];

const DOCUMENT_TYPE_OPTIONS: Array<AgentOption<string>> = [
  { value: '', label: '全部资料', description: '不限制资料类型，按当前用户知识库统一检索。' },
  { value: 'markdown', label: 'Markdown', description: '优先检索 Markdown 笔记、课程整理和技术文档。' },
  { value: 'pdf', label: 'PDF', description: '优先检索 PDF 课件、论文、书籍和报告。' },
  { value: 'video', label: '视频', description: '优先检索视频切片、字幕、OCR 和摘要证据。' },
  { value: 'text', label: '文本', description: '优先检索纯文本资料和粘贴内容。' }
];

// Agent 工作台负责创建只读任务和规划类任务，并展示审批闭环。
export function AgentWorkspace() {
  const [goal, setGoal] = useState('我的知识库里 Redis 学到了什么？');
  const [workspaceMode, setWorkspaceMode] = useState<AgentWorkspaceMode>('read');
  const [toolMode, setToolMode] = useState<ReadToolMode>('rag');
  const [topK, setTopK] = useState(5);
  const [documentType, setDocumentType] = useState('');
  const [jobDescription, setJobDescription] = useState('岗位要求熟悉 Java、Spring Boot、Redis、MySQL，有 RAG 项目经验优先。');
  const [resumeText, setResumeText] = useState('');
  const [resumeMaterials, setResumeMaterials] = useState<LearningMaterial[]>([]);
  const [selectedResumeMaterialId, setSelectedResumeMaterialId] = useState<number | null>(null);
  const [resumeMaterialsLoading, setResumeMaterialsLoading] = useState(false);
  const [resumeMaterialDetailLoadingId, setResumeMaterialDetailLoadingId] = useState<number | null>(null);
  const [resumeMaterialError, setResumeMaterialError] = useState('');
  const [saveDraft, setSaveDraft] = useState(false);
  const [enableWebSearch, setEnableWebSearch] = useState(false);
  const [webSearchQuery, setWebSearchQuery] = useState('');
  const [selectedResumeTemplateId, setSelectedResumeTemplateId] = useState('');
  const [resumeTemplates, setResumeTemplates] = useState<ResumeTemplate[]>([]);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [templateUploading, setTemplateUploading] = useState(false);
  const [templateDeleting, setTemplateDeleting] = useState('');
  const [templateError, setTemplateError] = useState('');
  const [resumePatchDraft, setResumePatchDraft] = useState<ResumePatchDraft | null>(null);
  const [resumePatchExport, setResumePatchExport] = useState<ResumeTemplateExport | null>(null);
  const [resumePatchBusy, setResumePatchBusy] = useState(false);
  const [resumePatchMessage, setResumePatchMessage] = useState('');
  const [resumePatchError, setResumePatchError] = useState('');
  const [task, setTask] = useState<AgentTask | null>(null);
  const [tools, setTools] = useState<AgentToolDefinition[]>([]);
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryAction, setMemoryAction] = useState('');
  const [memoryError, setMemoryError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reviewing, setReviewing] = useState('');
  const [undoing, setUndoing] = useState('');
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    void fetchAgentTools()
      .then(setTools)
      .catch((toolError) => setError(toolError instanceof Error ? toolError.message : '工具能力加载失败'));
    void loadMemories();
  }, []);

  useEffect(() => {
    if (workspaceMode !== 'planning') {
      setTemplateError('');
      setResumeMaterialError('');
      clearResumePatchState();
      return;
    }
    void loadResumeTemplateHistory();
    void loadResumeMaterials();
  }, [workspaceMode]);

  useEffect(() => {
    if (!task?.id || TERMINAL_STATUSES.has(task.status)) {
      setPolling(false);
      return undefined;
    }
    setPolling(true);
    const timer = window.setInterval(() => {
      void refreshTask(task.id);
    }, 1600);
    return () => window.clearInterval(timer);
  }, [task?.id, task?.status]);

  const finalAnswer = stringValue(task?.final?.answer);
  const evidenceIds = useMemo(() => normalizeStringList(task?.final?.evidenceIds), [task?.final]);
  const draftEvidenceIds = useMemo(() => normalizeStringList(task?.draft?.evidenceIds), [task?.draft]);
  const expandedQueries = useMemo(() => normalizeStringList(task?.final?.expandedQueries || task?.draft?.expandedQueries), [task?.final, task?.draft]);
  const readTools = tools.filter((item) => item.toolType === 'READ');
  const mutationTools = tools.filter((item) => item.toolType === 'MUTATION');
  const pendingReviews = (task?.reviews || []).filter((review) => review.status === 'PENDING');
  const usedMemoryContext = useMemo(
    () => normalizeRecordList(task?.final?.memoryContext || task?.draft?.memoryContext),
    [task?.final, task?.draft]
  );
  const pendingMemoryCandidates = memories.filter((item) => item.status === 'PENDING_REVIEW');
  const visibleMemories = memories.slice(0, 8);
  const taskType = workspaceMode === 'planning' ? 'planning_task' : 'pure_read_query';
  const activeMode = MODE_OPTIONS.find((item) => item.value === workspaceMode) || MODE_OPTIONS[0];
  const selectedResumeTemplate = useMemo(
    () => resumeTemplates.find((item) => item.templateId === selectedResumeTemplateId) || null,
    [resumeTemplates, selectedResumeTemplateId]
  );
  const selectedResumeMaterial = useMemo(
    () => resumeMaterials.find((item) => item.id === selectedResumeMaterialId) || null,
    [resumeMaterials, selectedResumeMaterialId]
  );
  const resumePatchConfirmedCount = useMemo(
    () => (resumePatchDraft?.patches || []).filter((patch) => patch.status === 'CONFIRMED' || patch.status === 'VALIDATED').length,
    [resumePatchDraft]
  );

  // 读取当前用户上传过的 DOCX 简历模板历史。
  async function loadResumeTemplateHistory() {
    try {
      setTemplateLoading(true);
      setTemplateError('');
      const templates = await fetchResumeTemplates(12);
      setResumeTemplates(templates);
      if (selectedResumeTemplateId && !templates.some((item) => item.templateId === selectedResumeTemplateId)) {
        setSelectedResumeTemplateId('');
      }
    } catch (loadError) {
      setTemplateError(loadError instanceof Error ? loadError.message : '简历模板历史加载失败');
    } finally {
      setTemplateLoading(false);
    }
  }

  // 读取用户已上传的资料，从中筛选可作为简历来源的文档。
  async function loadResumeMaterials() {
    try {
      setResumeMaterialsLoading(true);
      setResumeMaterialError('');
      const materials = await fetchMaterials();
      const candidates = rankResumeMaterials(materials);
      setResumeMaterials(candidates);
      if (selectedResumeMaterialId && !candidates.some((item) => item.id === selectedResumeMaterialId)) {
        setSelectedResumeMaterialId(null);
        setResumeText('');
      }
    } catch (loadError) {
      setResumeMaterialError(loadError instanceof Error ? loadError.message : '已上传简历资料加载失败');
    } finally {
      setResumeMaterialsLoading(false);
    }
  }

  // 选择一份已上传简历，读取解析摘要作为 Agent 的简历摘要输入。
  async function selectResumeMaterial(material: LearningMaterial) {
    setSelectedResumeMaterialId(material.id);
    setResumeMaterialError('');
    setResumeText((material.documentSummary || '').trim());
    if ((material.documentSummary || '').trim()) {
      clearResumePatchState();
      return;
    }
    try {
      setResumeMaterialDetailLoadingId(material.id);
      const detail = await fetchMaterial(material.id);
      setResumeMaterials((previous) => rankResumeMaterials(previous.map((item) => item.id === detail.id ? detail : item)));
      setResumeText((detail.documentSummary || '').trim());
      if (!(detail.documentSummary || '').trim()) {
        setResumeMaterialError('选中的简历资料尚未生成摘要，请等待解析完成或重新上传后再创建 JD 适配任务');
      }
    } catch (detailError) {
      setResumeMaterialError(detailError instanceof Error ? detailError.message : '简历资料摘要读取失败');
    } finally {
      setResumeMaterialDetailLoadingId(null);
      clearResumePatchState();
    }
  }

  // 选择历史模板，供后续进入预览确认页或生成补丁草稿使用。
  function selectResumeTemplate(template: ResumeTemplate) {
    if (!templateCanFill(template)) {
      setTemplateError('该模板尚未解析完成，请等待解析成功后再使用');
      return;
    }
    setTemplateError('');
    setSelectedResumeTemplateId(template.templateId);
    clearResumePatchState();
  }

  // 上传新的 DOCX 模板并立即加入历史模板列表。
  async function submitResumeTemplate(file: File | null) {
    if (!file) return;
    try {
      setTemplateUploading(true);
      setTemplateError('');
      const uploaded = await uploadResumeTemplate(file);
      setResumeTemplates((previous) => [
        uploaded,
        ...previous.filter((item) => item.templateId !== uploaded.templateId)
      ]);
      setSelectedResumeTemplateId(uploaded.templateId);
      clearResumePatchState();
    } catch (uploadError) {
      setTemplateError(uploadError instanceof Error ? uploadError.message : '简历模板上传解析失败');
    } finally {
      setTemplateUploading(false);
    }
  }

  // 基于 Agent 页面的岗位 JD 生成字段级简历补丁草稿。
  async function generateResumePatchDraft(useConfirmedAnnotations: boolean) {
    if (!selectedResumeTemplate) {
      setResumePatchError('请先选择简历模板');
      return;
    }
    if (!jobDescription.trim()) {
      setResumePatchError('请先填写岗位 JD');
      return;
    }
    if (!selectedResumeMaterial || !resumeText.trim()) {
      setResumePatchError('请先选择已上传简历资料，并等待系统读取到解析摘要');
      return;
    }
    try {
      setResumePatchBusy(true);
      setResumePatchError('');
      setResumePatchMessage('');
      setResumePatchExport(null);
      const draft = await generateResumePatches(selectedResumeTemplate.templateId, {
        version: selectedResumeTemplate.version,
        jobDescription,
        resumeText,
        resumeMaterialId: selectedResumeMaterial.id,
        resumeMaterialTitle: selectedResumeMaterial.title,
        topK,
        useConfirmedAnnotations
      });
      setResumePatchDraft(draft);
      setResumePatchMessage(draft.validationErrors.length ? '补丁草稿已生成，但仍有校验提示需要处理' : '补丁草稿已生成，请逐条确认或拒绝');
    } catch (generateError) {
      setResumePatchError(generateError instanceof Error ? generateError.message : '简历补丁草稿生成失败');
    } finally {
      setResumePatchBusy(false);
    }
  }

  // 更新单条补丁状态或内容，并清除旧导出结果。
  function updateResumePatch(fieldId: string, updater: (patch: ResumeContentPatch) => ResumeContentPatch, feedback = '') {
    setResumePatchDraft((previous) => {
      if (!previous) return previous;
      return {
        ...previous,
        status: 'DRAFT',
        validationErrors: [],
        patches: previous.patches.map((patch) => patch.fieldId === fieldId ? updater(patch) : patch)
      };
    });
    setResumePatchExport(null);
    setResumePatchMessage(feedback);
    setResumePatchError('');
  }

  // 校验用户确认后的字段补丁。
  async function validateResumePatchDraft() {
    if (!selectedResumeTemplate || !resumePatchDraft) return;
    try {
      setResumePatchBusy(true);
      setResumePatchError('');
      setResumePatchMessage('');
      const result = await validateResumePatches(selectedResumeTemplate.templateId, {
        version: selectedResumeTemplate.version,
        patchDraftId: resumePatchDraft.patchDraftId,
        patches: resumePatchDraft.patches
      });
      setResumePatchDraft(result);
      setResumePatchMessage(result.validationErrors.length ? '补丁仍有校验问题，请处理后再次校验' : '补丁已通过校验，可以导出 DOCX');
    } catch (validateError) {
      setResumePatchError(validateError instanceof Error ? validateError.message : '简历补丁校验失败');
    } finally {
      setResumePatchBusy(false);
    }
  }

  // 导出确认后的 DOCX 新版本。
  async function exportResumePatchDraft() {
    if (!selectedResumeTemplate || !resumePatchDraft) return;
    try {
      setResumePatchBusy(true);
      setResumePatchError('');
      setResumePatchMessage('');
      const result = await exportResumeTemplate(selectedResumeTemplate.templateId, {
        version: selectedResumeTemplate.version,
        patchDraftId: resumePatchDraft.patchDraftId,
        idempotencyKey: `${selectedResumeTemplate.templateId}-${resumePatchDraft.patchDraftId}-${resumePatchConfirmedCount}`
      });
      setResumePatchExport(result);
      setResumePatchMessage('确认后的简历 DOCX 已导出');
    } catch (exportError) {
      setResumePatchError(exportError instanceof Error ? exportError.message : '简历 DOCX 导出失败');
    } finally {
      setResumePatchBusy(false);
    }
  }

  function clearResumePatchState() {
    setResumePatchDraft(null);
    setResumePatchExport(null);
    setResumePatchBusy(false);
    setResumePatchMessage('');
    setResumePatchError('');
  }

  // 创建 Agent 任务。
  async function submit() {
    const trimmedGoal = goal.trim();
    if (!trimmedGoal) {
      setError('请输入 Agent 目标');
      return;
    }
    if (taskType === 'planning_task' && !selectedResumeMaterialId) {
      setError('请选择已上传的简历资料，系统会使用该资料的解析摘要进行 JD 适配');
      return;
    }
    if (taskType === 'planning_task' && !resumeText.trim()) {
      setError('选中的简历资料暂无可用摘要，请等待解析完成、重新上传或先在资料库修复解析');
      return;
    }
    try {
      setSubmitting(true);
      setError('');
      const metadataFilter: Record<string, unknown> = {};
      if (documentType) {
        metadataFilter.documentType = documentType;
      }
      const created = await createAgentTask({
        taskType,
        title: trimmedGoal.slice(0, 48),
        input: {
          goal: buildGoalForMode(workspaceMode, trimmedGoal),
          topK,
          candidateMultiplier: 4,
          jobDescription: taskType === 'planning_task' ? jobDescription : undefined,
          resumeText: taskType === 'planning_task' ? resumeText : undefined,
          toolHints: taskType === 'planning_task'
            ? [
                'resume_evidence_aligner',
                'gap_analyzer',
                ...(saveDraft ? ['resume_revision_save'] : []),
                ...(enableWebSearch ? ['web_search_probe'] : [])
              ]
            : [toolMode === 'coverage' ? 'retrieval_coverage_probe' : 'rag_query_probe_non_persistent'],
          workspaceMode,
          saveDraft: taskType === 'planning_task' ? saveDraft : undefined,
          enableWebSearch: taskType === 'planning_task' ? enableWebSearch : undefined,
          webSearchQuery: taskType === 'planning_task' && webSearchQuery.trim() ? webSearchQuery.trim() : undefined,
          webSearchMaxResults: taskType === 'planning_task' && enableWebSearch ? 5 : undefined,
          resumeMaterialId: taskType === 'planning_task' && selectedResumeMaterial ? selectedResumeMaterial.id : undefined,
          resumeMaterialTitle: taskType === 'planning_task' && selectedResumeMaterial ? selectedResumeMaterial.title : undefined,
          resumeTemplateId: taskType === 'planning_task' && selectedResumeTemplateId ? selectedResumeTemplateId : undefined,
          metadataFilter
        }
      });
      setTask(created);
      void refreshTask(created.id);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Agent 任务创建失败');
    } finally {
      setSubmitting(false);
    }
  }

  // 加载当前用户可管理的 Agent 记忆。
  async function loadMemories() {
    try {
      setMemoryLoading(true);
      setMemoryError('');
      const items = await fetchAgentMemories();
      setMemories(items);
    } catch (loadError) {
      setMemoryError(loadError instanceof Error ? loadError.message : 'Agent 记忆加载失败');
    } finally {
      setMemoryLoading(false);
    }
  }

  // 执行记忆确认、拒绝、归档或删除，并刷新列表。
  async function actOnMemory(memory: AgentMemory, action: 'confirm' | 'reject' | 'archive' | 'delete') {
    if (action === 'delete' && !window.confirm('确认删除这条 Agent 记忆？删除后正文会被擦除。')) {
      return;
    }
    try {
      setMemoryAction(`${memory.id}-${action}`);
      setMemoryError('');
      if (action === 'confirm') {
        await confirmAgentMemory(memory.id);
      } else if (action === 'reject') {
        await rejectAgentMemory(memory.id);
      } else if (action === 'archive') {
        await archiveAgentMemory(memory.id);
      } else {
        await deleteAgentMemory(memory.id);
      }
      await loadMemories();
    } catch (actionError) {
      setMemoryError(actionError instanceof Error ? actionError.message : 'Agent 记忆操作失败');
    } finally {
      setMemoryAction('');
    }
  }

  // 删除当前用户上传的历史模板，并同步清理页面选中状态。
  async function removeResumeTemplate(templateId: string) {
    if (!templateId || !window.confirm('确认删除这份简历模板及其预览、草稿和导出记录？')) {
      return;
    }
    try {
      setTemplateDeleting(templateId);
      setTemplateError('');
      await deleteResumeTemplate(templateId);
      setResumeTemplates((previous) => previous.filter((item) => item.templateId !== templateId));
      if (selectedResumeTemplateId === templateId) {
        setSelectedResumeTemplateId('');
        clearResumePatchState();
      }
    } catch (deleteError) {
      setTemplateError(deleteError instanceof Error ? deleteError.message : '简历模板删除失败');
    } finally {
      setTemplateDeleting('');
    }
  }

  // 轮询 Java 任务详情。
  async function refreshTask(taskId: string) {
    try {
      const latest = await fetchAgentTask(taskId);
      setTask(latest);
      if (TERMINAL_STATUSES.has(latest.status)) {
        setPolling(false);
        void loadMemories();
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Agent 任务刷新失败');
      setPolling(false);
    }
  }

  // 提交计划或输出审批。
  async function decide(review: AgentHumanReview, decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED') {
    if (!task) return;
    try {
      setReviewing(`${review.id}-${decision}`);
      setError('');
      const latest = await decideAgentReview(task.id, review.id, {
        decision,
        comment: decision === 'APPROVED' ? '同意继续' : decision === 'REJECTED' ? '拒绝该审批' : '请调整计划或输出',
        changes: {}
      });
      setTask(latest);
      if (!TERMINAL_STATUSES.has(latest.status)) {
        void refreshTask(latest.id);
      }
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : '审批提交失败');
    } finally {
      setReviewing('');
    }
  }

  // 撤销窗口内恢复 Agent 操作前状态。
  async function undoOperation(operation: AgentOperation) {
    if (!task) return;
    try {
      setUndoing(operation.id);
      setError('');
      await undoAgentOperation(operation.id, {
        idempotencyKey: `undo-${operation.id}`,
        reason: '用户在 Agent 工作台撤销'
      });
      await refreshTask(task.id);
    } catch (undoError) {
      setError(undoError instanceof Error ? undoError.message : '撤销操作失败');
    } finally {
      setUndoing('');
    }
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>Agent 任务</h2>
          <p>只读检索、计划审批、JD/简历证据对齐和输出确认</p>
        </div>
        <div className={`status-pill ${taskStatusClass(task?.status)}`}>
          {statusIcon(task?.status, submitting || polling)}
          {task ? statusLabel(task.status) : '待创建'}
        </div>
      </section>

      <section className="agent-workspace-grid">
        <article className="panel agent-compose-panel">
          <div className="panel-title">
            <h3><Bot size={20} />创建任务</h3>
            <span className="status-pill"><ShieldCheck size={14} />Java 权限边界</span>
          </div>
          <div className="agent-mode-cards" aria-label="任务模式">
            {MODE_OPTIONS.map((mode) => (
              <button
                className={workspaceMode === mode.value ? 'agent-mode-card is-active' : 'agent-mode-card'}
                key={mode.value}
                onClick={() => setWorkspaceMode(mode.value)}
                type="button"
              >
                <span className="agent-mode-card-head">
                  <span className="agent-mode-icon">{mode.icon}</span>
                  <span>
                    <strong>{mode.label}</strong>
                    <small>{mode.badge}</small>
                  </span>
                </span>
                <span className="agent-mode-card-copy">{mode.description}</span>
                <span className="agent-mode-feature-row">
                  {mode.features.map((feature) => <span key={feature}>{feature}</span>)}
                </span>
              </button>
            ))}
          </div>
          <div className="agent-mode-guidance">
            <div>
              <strong>{activeMode.label}能做什么</strong>
              <p>{activeMode.description}</p>
            </div>
            {workspaceMode === 'general' ? (
              <Link className="agent-inline-link" to="/materials">
                <Database size={15} />
                <span>需要入库时去资料库上传</span>
              </Link>
            ) : null}
          </div>
          <label className="agent-field">
            <span>目标</span>
            <textarea value={goal} onChange={(event) => setGoal(event.target.value)} />
          </label>
          {taskType === 'planning_task' ? (
            <div className="agent-planning-inputs">
              <label className="agent-field">
                <span>岗位 JD</span>
                <textarea value={jobDescription} onChange={(event) => setJobDescription(event.target.value)} />
              </label>
              <ResumeMaterialSelector
                error={resumeMaterialError}
                loading={resumeMaterialsLoading}
                loadingDetailId={resumeMaterialDetailLoadingId}
                materials={resumeMaterials}
                onRefresh={() => void loadResumeMaterials()}
                onSelect={(material) => void selectResumeMaterial(material)}
                resumeText={resumeText}
                selectedMaterial={selectedResumeMaterial}
                selectedMaterialId={selectedResumeMaterialId}
              />
            </div>
          ) : null}
          {taskType === 'planning_task' ? (
            <label className="agent-check-row">
              <input type="checkbox" checked={saveDraft} onChange={(event) => setSaveDraft(event.target.checked)} />
              <span><Save size={16} />输出确认后进入保存审批</span>
            </label>
          ) : null}
          {taskType === 'planning_task' ? (
            <div className="agent-web-search-box">
              <label className="agent-check-row">
                <input type="checkbox" checked={enableWebSearch} onChange={(event) => setEnableWebSearch(event.target.checked)} />
                <span><Search size={16} />联网补充公司背景和技能趋势</span>
              </label>
              {enableWebSearch ? (
                <label className="agent-field">
                  <span>联网检索词</span>
                  <input value={webSearchQuery} onChange={(event) => setWebSearchQuery(event.target.value)} placeholder="默认由目标和 JD 自动生成" />
                </label>
              ) : null}
            </div>
          ) : null}
          {taskType === 'planning_task' ? (
            <ResumeTemplateSelector
              error={templateError}
              loading={templateLoading}
              onDelete={(templateId) => void removeResumeTemplate(templateId)}
              onSelect={selectResumeTemplate}
              onUpload={(file) => void submitResumeTemplate(file)}
              selectedTemplate={selectedResumeTemplate}
              selectedTemplateId={selectedResumeTemplateId}
              deletingTemplateId={templateDeleting}
              templates={resumeTemplates}
              uploading={templateUploading}
            />
          ) : null}
          {taskType === 'planning_task' ? (
            <ResumePatchPanel
              busy={resumePatchBusy}
              confirmedCount={resumePatchConfirmedCount}
              draft={resumePatchDraft}
              error={resumePatchError}
              exportResult={resumePatchExport}
              message={resumePatchMessage}
              onExport={() => void exportResumePatchDraft()}
              onGenerateAllFields={() => void generateResumePatchDraft(false)}
              onGenerateConfirmedRegions={() => void generateResumePatchDraft(true)}
              onUpdatePatch={updateResumePatch}
              onValidate={() => void validateResumePatchDraft()}
              selectedTemplate={selectedResumeTemplate}
            />
          ) : null}
          <div className="agent-control-grid">
            <AgentListbox
              label={taskType === 'planning_task' ? '只读检索基线' : '工具路线'}
              value={toolMode}
              onChange={setToolMode}
              options={TOOL_MODE_OPTIONS}
              disabled={taskType === 'planning_task'}
              disabledHint="JD 适配规划会固定执行证据对齐、能力缺口和输出审批。"
            />
            <AgentListbox
              label="资料类型"
              value={documentType}
              onChange={setDocumentType}
              options={DOCUMENT_TYPE_OPTIONS}
            />
            <label className="agent-field">
              <span>topK</span>
              <input type="number" min={1} max={20} value={topK} onChange={(event) => setTopK(clamp(Number(event.target.value), 1, 20))} />
            </label>
          </div>
          {error ? <p className="form-message danger">{error}</p> : null}
          <button className="primary-action agent-submit" onClick={submit} disabled={submitting}>
            {submitting ? <Loader2 className="spin" size={17} /> : <Send size={17} />}
            <span>{submitting ? '创建中' : '创建任务'}</span>
          </button>
        </article>

        <article className="panel agent-side-panel">
          <div className="panel-title">
            <h3><Sparkles size={20} />开放工具</h3>
          </div>
          <div className="agent-tool-list">
            {readTools.map((tool) => (
              <div className="agent-tool-item" key={tool.toolName}>
                <strong>{tool.toolName}</strong>
                <span>阶段 {tool.stage} · {tool.description}</span>
              </div>
            ))}
            {mutationTools.map((tool) => (
              <div className="agent-tool-item mutation" key={tool.toolName}>
                <strong>{tool.toolName}</strong>
                <span>阶段 {tool.stage} · 需 {tool.approvalType} 审批 · {tool.description}</span>
              </div>
            ))}
            {!readTools.length ? <p className="agent-empty">暂无工具能力</p> : null}
          </div>
        </article>
      </section>

      {task ? (
        <section className="agent-result-grid">
          <article className="panel">
            <div className="panel-title">
              <h3><Clock3 size={20} />任务状态</h3>
              <span className={`status-pill ${taskStatusClass(task.status)}`}>{statusLabel(task.status)}</span>
            </div>
            <div className="agent-task-meta">
              <MetaItem label="任务 ID" value={task.id} />
              <MetaItem label="线程 ID" value={task.pythonThreadId || task.id} />
              <MetaItem label="更新时间" value={formatTime(task.updatedAt)} />
              {task.errorCode ? <MetaItem label="错误码" value={task.errorCode} /> : null}
            </div>
            {task.errorMessage ? <p className="form-message danger">{task.errorMessage}</p> : null}
          </article>

          <article className="panel">
            <div className="panel-title">
              <h3><Search size={20} />工具观察</h3>
              <span className="status-pill">{task.toolCalls?.length || 0} 次调用</span>
            </div>
            <div className="agent-timeline">
              {(task.toolCalls || []).map((call) => <ToolCallItem key={call.id} call={call} />)}
              {!(task.toolCalls || []).length ? <p className="agent-empty">等待工具观察回写</p> : null}
            </div>
          </article>
        </section>
      ) : null}

      {task || memories.length || memoryLoading || memoryError ? (
        <section className="agent-memory-grid">
          <article className="panel agent-memory-panel">
            <div className="panel-title">
              <h3><Database size={20} />本次使用的记忆</h3>
              <span className="status-pill">{usedMemoryContext.length} 条上下文</span>
            </div>
            <div className="agent-memory-context-list">
              {usedMemoryContext.map((item) => (
                <div className="agent-memory-context-row" key={String(item.memoryId || item.subjectKey)}>
                  <div>
                    <strong>{String(item.subjectKey || item.namespace || 'Agent 记忆')}</strong>
                    <p>{String(item.summary || '无摘要')}</p>
                  </div>
                  <div className="query-tags agent-tags">
                    {item.memoryType ? <span>{String(item.memoryType)}</span> : null}
                    {item.scope ? <span>{String(item.scope)}</span> : null}
                    {item.score !== undefined ? <span>分数 {String(item.score)}</span> : null}
                  </div>
                </div>
              ))}
              {!usedMemoryContext.length ? <p className="agent-empty">本次任务没有注入 ACTIVE 记忆。</p> : null}
            </div>
          </article>

          <article className="panel agent-memory-panel">
            <div className="panel-title">
              <h3><ShieldCheck size={20} />待确认记忆</h3>
              <button className="chip-button" onClick={() => void loadMemories()} disabled={memoryLoading} type="button">
                {memoryLoading ? <Loader2 className="spin" size={15} /> : <RotateCcw size={15} />}
                刷新
              </button>
            </div>
            {memoryError ? <p className="form-message danger">{memoryError}</p> : null}
            <div className="agent-memory-list">
              {pendingMemoryCandidates.map((memory) => (
                <MemoryItem
                  key={memory.id}
                  memory={memory}
                  busyAction={memoryAction}
                  onAction={(action) => void actOnMemory(memory, action)}
                />
              ))}
              {!pendingMemoryCandidates.length ? <p className="agent-empty">暂无待确认候选。任务完成后候选会先停留在这里。</p> : null}
            </div>
          </article>

          <article className="panel agent-memory-panel wide">
            <div className="panel-title">
              <h3><Archive size={20} />记忆管理</h3>
              <span className="status-pill">{memories.length} 条未删除记忆</span>
            </div>
            <div className="agent-memory-list compact">
              {visibleMemories.map((memory) => (
                <MemoryItem
                  key={memory.id}
                  memory={memory}
                  busyAction={memoryAction}
                  onAction={(action) => void actOnMemory(memory, action)}
                />
              ))}
              {!visibleMemories.length ? <p className="agent-empty">当前还没有 Agent 记忆。</p> : null}
            </div>
          </article>
        </section>
      ) : null}

      {task && pendingReviews.length > 0 ? (
        <section className="agent-review-grid">
          {pendingReviews.map((review) => (
            <article className="panel agent-review-card" key={review.id}>
              <div className="panel-title">
                <h3><ThumbsUp size={20} />{reviewTitle(review.reviewType)}</h3>
                <span className="status-pill running">待审批</span>
              </div>
              <ReviewProposal review={review} />
              <div className="agent-review-actions">
                <button className="primary-action" onClick={() => void decide(review, 'APPROVED')} disabled={Boolean(reviewing)}>
                  {reviewing === `${review.id}-APPROVED` ? <Loader2 className="spin" size={16} /> : <CheckCircle2 size={16} />}
                  <span>同意</span>
                </button>
                <button className="ghost-action" onClick={() => void decide(review, 'CHANGES_REQUESTED')} disabled={Boolean(reviewing)}>
                  <Clock3 size={16} />
                  <span>要求调整</span>
                </button>
                <button className="ghost-action" onClick={() => void decide(review, 'REJECTED')} disabled={Boolean(reviewing)}>
                  <XCircle size={16} />
                  <span>拒绝</span>
                </button>
              </div>
            </article>
          ))}
        </section>
      ) : null}

      {task && (task.operations || []).length > 0 ? (
        <section className="panel agent-operation-panel">
          <div className="panel-title">
            <h3><RotateCcw size={20} />变更操作</h3>
            <span className="status-pill">{task.operations?.length || 0} 条记录</span>
          </div>
          <div className="agent-operation-list">
            {(task.operations || []).map((operation) => (
              <OperationItem
                key={operation.id}
                operation={operation}
                undoing={undoing === operation.id}
                onUndo={() => void undoOperation(operation)}
              />
            ))}
          </div>
        </section>
      ) : null}

      {task ? (
        <section className="panel agent-final-panel">
          <div className="panel-title">
            <h3><FileText size={20} />最终结果</h3>
            <span className="status-pill">{Math.max(evidenceIds.length, draftEvidenceIds.length)} 条 evidence</span>
          </div>
          {finalAnswer || task.draft?.matchSummary ? (
            <>
              <MarkdownText className="answer-copy" content={finalAnswer || stringValue(task.draft?.matchSummary)} />
              <PlanningResult task={task} />
            </>
          ) : (
            <p className="agent-empty">{task.status === 'CREATED' ? '任务已创建，等待 Python Agent 接收。' : '等待最终结果回写。'}</p>
          )}
          <div className="query-tags agent-tags">
            {[...evidenceIds, ...draftEvidenceIds].map((id) => <span key={id}>{id}</span>)}
            {expandedQueries.map((query) => <span key={query}>{query}</span>)}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function AgentListbox<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled = false,
  disabledHint
}: {
  label: string;
  value: T;
  options: Array<AgentOption<T>>;
  onChange: (value: T) => void;
  disabled?: boolean;
  disabledHint?: string;
}) {
  const selected = options.find((item) => item.value === value) || options[0];
  return (
    <div className="agent-field agent-select-field">
      <span>{label}</span>
      <Listbox value={selected.value} onChange={onChange} disabled={disabled}>
        <div className="agent-select">
          <ListboxButton className={disabled ? 'agent-select-button is-disabled' : 'agent-select-button'}>
            <span className="agent-select-value">
              <strong>{selected.label}</strong>
              <small>{disabled && disabledHint ? disabledHint : selected.description}</small>
            </span>
            <span className="agent-select-end">
              {selected.badge ? <em>{selected.badge}</em> : null}
              <ChevronDown size={16} />
            </span>
          </ListboxButton>
          <ListboxOptions className="agent-select-options">
            {options.map((option) => (
              <ListboxOption
                className={({ focus, selected: isSelected }) =>
                  ['agent-select-option', focus ? 'is-focused' : '', isSelected ? 'is-selected' : ''].filter(Boolean).join(' ')
                }
                key={option.value}
                value={option.value}
              >
                {({ selected: isSelected }) => (
                  <>
                    <span>
                      <strong>{option.label}</strong>
                      <small>{option.description}</small>
                    </span>
                    {option.badge ? <em>{option.badge}</em> : null}
                    {isSelected ? <Check className="agent-select-check" size={16} /> : null}
                  </>
                )}
              </ListboxOption>
            ))}
          </ListboxOptions>
        </div>
      </Listbox>
    </div>
  );
}

function ResumeMaterialSelector({
  materials,
  selectedMaterialId,
  selectedMaterial,
  resumeText,
  loading,
  loadingDetailId,
  error,
  onSelect,
  onRefresh
}: {
  materials: LearningMaterial[];
  selectedMaterialId: number | null;
  selectedMaterial: LearningMaterial | null;
  resumeText: string;
  loading: boolean;
  loadingDetailId: number | null;
  error: string;
  onSelect: (material: LearningMaterial) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="agent-resume-material-box">
      <div className="agent-template-box-head">
        <div>
          <strong>选择已上传简历</strong>
          <span>系统读取资料解析摘要作为 Agent 简历摘要，再结合岗位 JD 和所选模板生成修改建议</span>
        </div>
        <button className="chip-button" onClick={onRefresh} disabled={loading} type="button">
          {loading ? <Loader2 className="spin" size={15} /> : <RotateCcw size={15} />}
          刷新
        </button>
      </div>

      <div className="agent-resume-material-grid">
        {loading ? (
          <div className="agent-template-state">
            <Loader2 className="spin" size={17} />
            <span>正在读取已上传简历</span>
          </div>
        ) : materials.length ? (
          materials.map((material) => {
            const active = material.id === selectedMaterialId;
            const usable = materialCanUseAsResume(material);
            const summary = (material.documentSummary || '').trim();
            const loadingDetail = loadingDetailId === material.id;
            return (
              <button
                className={[
                  'agent-resume-material-card',
                  active ? 'is-active' : '',
                  usable ? '' : 'is-disabled'
                ].filter(Boolean).join(' ')}
                disabled={!usable || Boolean(loadingDetailId)}
                key={material.id}
                onClick={() => onSelect(material)}
                type="button"
              >
                <span className="agent-resume-material-top">
                  <FileText size={18} />
                  <em>{formatMaterialStatus(material.status)}</em>
                </span>
                <strong>{material.originalFilename || material.title}</strong>
                <small>{material.documentType} · {formatTime(material.updatedAt || material.createdAt)}</small>
                <span className="agent-resume-material-summary">
                  {loadingDetail ? '正在读取摘要...' : summary || '暂无摘要，选择后会尝试读取详情'}
                </span>
              </button>
            );
          })
        ) : (
          <div className="empty-state compact">暂无可用简历资料，请先到资料库上传并等待解析完成</div>
        )}
      </div>

      {selectedMaterial ? (
        <div className="agent-resume-summary-card">
          <div>
            <span>当前摘要来源</span>
            <strong>{selectedMaterial.originalFilename || selectedMaterial.title}</strong>
          </div>
          <p>{resumeText || '这份资料暂未生成摘要，无法用于 JD 适配任务。'}</p>
        </div>
      ) : null}

      {error ? <p className="form-message danger agent-template-message">{error}</p> : null}
    </div>
  );
}

function ResumeTemplateSelector({
  templates,
  selectedTemplateId,
  selectedTemplate,
  loading,
  uploading,
  deletingTemplateId,
  error,
  onSelect,
  onDelete,
  onUpload
}: {
  templates: ResumeTemplate[];
  selectedTemplateId: string;
  selectedTemplate: ResumeTemplate | null;
  loading: boolean;
  uploading: boolean;
  deletingTemplateId: string;
  error: string;
  onSelect: (template: ResumeTemplate) => void;
  onDelete: (templateId: string) => void;
  onUpload: (file: File | null) => void;
}) {
  return (
    <div className="agent-template-box">
      <div className="agent-template-box-head">
        <div>
          <strong>简历 DOCX 模板</strong>
          <span>选择历史模板后，先到简历模板页确认可修改区域；Agent 不再绕过确认直接改 DOCX</span>
        </div>
        <span className="status-pill">{selectedTemplate ? '已选择' : `${templates.length} 个历史模板`}</span>
      </div>
      <Link className="agent-inline-link" to="/resume-template">
        <ExternalLink size={15} />
        <span>打开图片预览确认页</span>
      </Link>

      <div className="agent-template-history-grid">
        {loading ? (
          <div className="agent-template-state">
            <Loader2 className="spin" size={17} />
            <span>正在读取历史模板</span>
          </div>
        ) : templates.length ? (
          templates.map((template) => {
            const active = template.templateId === selectedTemplateId;
            const canFill = templateCanFill(template);
            return (
              <div className="agent-template-history-item" key={template.templateId}>
                <button
                  className={[
                    'agent-template-history-card',
                    active ? 'is-active' : '',
                    canFill ? '' : 'is-disabled'
                  ].filter(Boolean).join(' ')}
                  disabled={!canFill}
                  onClick={() => onSelect(template)}
                  type="button"
                >
                  <span className="agent-template-card-top">
                    <FileText size={18} />
                    <span>{formatTemplateStatus(template.status)}</span>
                  </span>
                  <strong>{template.filename}</strong>
                  <small>版本 {template.version} · {template.unsupportedRegionCount} 个复杂区域</small>
                  <span className="agent-template-card-time">{formatTime(template.updatedAt || template.createdAt)}</span>
                </button>
                <button
                  className="agent-template-delete"
                  disabled={deletingTemplateId === template.templateId}
                  onClick={() => onDelete(template.templateId)}
                  type="button"
                >
                  {deletingTemplateId === template.templateId ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
                  <span>删除</span>
                </button>
              </div>
            );
          })
        ) : (
          <div className="empty-state compact">暂无上传过的简历模板，先在下方上传 DOCX 并提取字段</div>
        )}
      </div>

      {selectedTemplate ? (
        <div className="agent-template-selected">
          <span>当前模板</span>
          <strong>{selectedTemplate.filename}</strong>
          <small>{selectedTemplate.currentFilePath || '模板文件路径已由后端托管，前端不再直连本地文件。'}</small>
          <div className="agent-template-selected-actions">
            <Link className="chip-button" to="/resume-template">
              <ExternalLink size={15} />确认可修改区域
            </Link>
            <button className="chip-button danger" onClick={() => onDelete(selectedTemplate.templateId)} disabled={deletingTemplateId === selectedTemplate.templateId} type="button">
              {deletingTemplateId === selectedTemplate.templateId ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
              删除模板
            </button>
          </div>
        </div>
      ) : null}

      {error ? <p className="form-message danger agent-template-message">{error}</p> : null}

      <label className={uploading ? 'agent-template-upload is-busy' : 'agent-template-upload'}>
        {uploading ? <Loader2 className="spin" size={17} /> : <Upload size={17} />}
        <span>{uploading ? '正在解析模板' : '上传简历提取模板'}</span>
        <input
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          disabled={uploading}
          onChange={(event) => {
            const file = event.target.files?.[0] || null;
            event.target.value = '';
            onUpload(file);
          }}
        />
      </label>
    </div>
  );
}

function ResumePatchPanel({
  selectedTemplate,
  draft,
  exportResult,
  confirmedCount,
  busy,
  message,
  error,
  onGenerateConfirmedRegions,
  onGenerateAllFields,
  onUpdatePatch,
  onValidate,
  onExport
}: {
  selectedTemplate: ResumeTemplate | null;
  draft: ResumePatchDraft | null;
  exportResult: ResumeTemplateExport | null;
  confirmedCount: number;
  busy: boolean;
  message: string;
  error: string;
  onGenerateConfirmedRegions: () => void;
  onGenerateAllFields: () => void;
  onUpdatePatch: (fieldId: string, updater: (patch: ResumeContentPatch) => ResumeContentPatch, feedback?: string) => void;
  onValidate: () => void;
  onExport: () => void;
}) {
  const canExport = Boolean(draft && ['CONFIRMED', 'VALIDATED'].includes(draft.status) && confirmedCount > 0);
  return (
    <div className="agent-resume-patch-box">
      <div className="agent-template-box-head">
        <div>
          <strong>按岗位 JD 修改简历模板</strong>
          <span>先生成字段补丁草稿，逐条确认或拒绝，再校验并导出 DOCX</span>
        </div>
        <span className={`status-pill ${canExport ? 'indexed' : draft ? 'running' : ''}`}>
          {draft ? `${confirmedCount}/${draft.patches.length} 已确认` : '等待生成'}
        </span>
      </div>
      <div className="agent-resume-patch-actions">
        <button className="primary-action" disabled={busy || !selectedTemplate} onClick={onGenerateConfirmedRegions} type="button">
          {busy ? <Loader2 className="spin" size={16} /> : <Highlighter size={16} />}
          <span>按已保存图片区域生成</span>
        </button>
        <button className="ghost-action" disabled={busy || !selectedTemplate} onClick={onGenerateAllFields} type="button">
          <FileText size={16} />
          <span>按全部安全字段生成</span>
        </button>
      </div>
      {!selectedTemplate ? <div className="empty-state compact">先选择一份已解析的简历模板</div> : null}
      {message ? <p className="form-message agent-template-message">{message}</p> : null}
      {error ? <p className="form-message danger agent-template-message">{error}</p> : null}
      {draft ? (
        <div className="resume-patch-list agent-resume-patch-list">
          {draft.patches.map((patch) => {
            return (
              <div className={`resume-patch-row ${patchRowClass(patch.status)}`} key={patch.fieldId}>
                <div className="resume-patch-head">
                  <div>
                    <strong>待确认改写项</strong>
                    <span>{patch.confidence ? `${Math.round(patch.confidence * 100)}%` : '待评估'} · 字段内容由后端托管</span>
                  </div>
                  <span className={`evidence-status ${patchStatusClass(patch.status)}`}>
                    {patch.status === 'REJECTED' ? <XCircle size={15} /> : <CheckCircle2 size={15} />}
                    {formatPatchStatus(patch.status)}
                  </span>
                </div>
                <div className="resume-patch-compare">
                  <label>
                    <span>新文本</span>
                    <textarea
                      value={patch.newText}
                      onChange={(event) => onUpdatePatch(patch.fieldId, (current) => ({ ...current, newText: event.target.value, status: 'DRAFT' }))}
                    />
                  </label>
                </div>
                <p className="resume-patch-reason">{patch.rewriteReason}</p>
                <div className="resume-risk-row">
                  {patch.riskFlags.map((flag) => <span key={flag} className={flag === 'NONE' ? 'risk-ok' : 'risk-warn'}>{formatRisk(flag)}</span>)}
                  {patch.evidenceIds.map((id) => <span key={id}>证据 {id}</span>)}
                </div>
                <div className="resume-patch-actions agent-patch-row-actions">
                  <span className={`resume-patch-decision-note ${patchRowClass(patch.status)}`}>
                    {patch.status === 'CONFIRMED' ? '本条已确认，校验后会参与导出' : patch.status === 'REJECTED' ? '本条已拒绝，导出时不会应用' : '等待人工确认'}
                  </span>
                  <div>
                    <button
                      className={patch.status === 'CONFIRMED' ? 'chip-button is-active' : 'chip-button'}
                      disabled={busy}
                      onClick={() => onUpdatePatch(
                        patch.fieldId,
                        (current) => ({ ...current, status: 'CONFIRMED' }),
                        '该改写项已确认，校验后会参与导出'
                      )}
                      type="button"
                    >
                      <CheckCircle2 size={16} />{patch.status === 'CONFIRMED' ? '已确认' : '确认'}
                    </button>
                    <button
                      className={patch.status === 'REJECTED' ? 'chip-button danger is-active' : 'chip-button danger'}
                      disabled={busy}
                      onClick={() => onUpdatePatch(
                        patch.fieldId,
                        (current) => ({ ...current, status: 'REJECTED' }),
                        '该改写项已拒绝，导出时不会应用'
                      )}
                      type="button"
                    >
                      <XCircle size={16} />{patch.status === 'REJECTED' ? '已拒绝' : '拒绝'}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
      {draft?.validationErrors.length ? (
        <div className="resume-validation-errors">
          {draft.validationErrors.map((item) => <span key={item}><TriangleAlert size={15} />{item}</span>)}
        </div>
      ) : null}
      <div className="resume-template-actions agent-resume-export-actions">
        <button className="ghost-action" onClick={onValidate} disabled={busy || !draft} type="button">
          <ShieldCheck size={17} />校验补丁
        </button>
        <button className="primary-action" onClick={onExport} disabled={busy || !canExport} type="button">
          <Download size={17} />导出 DOCX
        </button>
      </div>
      {exportResult ? (
        <div className="resume-export-result agent-export-result">
          <strong>{exportResult.filename}</strong>
          <span>版本 {exportResult.baseVersion} → {exportResult.exportVersion}</span>
          <p>{exportResult.publicUrl || exportResult.filePath}</p>
          <p>{String(exportResult.layoutValidation?.message || 'XML 结构 fingerprint 已通过校验')}</p>
        </div>
      ) : null}
    </div>
  );
}

function ReviewProposal({ review }: { review: AgentHumanReview }) {
  const proposal = review.proposal || {};
  return (
    <div className="agent-review-proposal">
      <strong>{stringValue(proposal.title) || stringValue(proposal.summary) || '待确认内容'}</strong>
      {review.reviewType === 'CRUD' ? (
        <div className="agent-crud-summary">
          <MetaItem label="操作类型" value={String(proposal.operationType || '未返回')} />
          <MetaItem label="资源类型" value={String(proposal.resourceType || '未返回')} />
          <MetaItem label="幂等键" value={String(proposal.idempotencyKey || '未返回')} />
          <MetaItem label="撤销窗口" value={proposal.undoWindowMinutes ? `${String(proposal.undoWindowMinutes)} 分钟` : '未返回'} />
        </div>
      ) : null}
      {Array.isArray(proposal.steps) ? (
        <ul>
          {proposal.steps.map((step) => <li key={String(step)}>{String(step)}</li>)}
        </ul>
      ) : null}
      <div className="query-tags agent-tags">
        {normalizeStringList(proposal.tools).map((tool) => <span key={tool}>{tool}</span>)}
        {proposal.toolName ? <span>{String(proposal.toolName)}</span> : null}
        {proposal.riskLevel ? <span>风险 {String(proposal.riskLevel)}</span> : null}
        {proposal.evidenceCount !== undefined ? <span>证据 {String(proposal.evidenceCount)}</span> : null}
        {proposal.undoable ? <span>可撤销</span> : null}
      </div>
      {proposal.summary ? <p>{String(proposal.summary)}</p> : null}
    </div>
  );
}

function OperationItem({ operation, undoing, onUndo }: { operation: AgentOperation; undoing: boolean; onUndo: () => void }) {
  const undoable = operation.status === 'APPLIED_UNDOABLE' && !undoExpired(operation.undoDeadline);
  return (
    <div className="agent-operation-row">
      <div>
        <div className="agent-timeline-head">
          <strong>{operation.operationType}</strong>
          <span className={`status-pill ${operationStatusClass(operation.status)}`}>{operationStatusLabel(operation.status)}</span>
        </div>
        <p>{operation.resourceType} · {operation.resourceId}</p>
        <div className="query-tags agent-tags">
          <span>{operation.idempotencyKey}</span>
          {operation.undoDeadline ? <span>撤销截止 {formatTime(operation.undoDeadline)}</span> : null}
        </div>
        {operation.errorMessage ? <small>{operation.errorCode}：{operation.errorMessage}</small> : null}
      </div>
      <button className="ghost-action" onClick={onUndo} disabled={!undoable || undoing} type="button">
        {undoing ? <Loader2 className="spin" size={16} /> : <RotateCcw size={16} />}
        <span>{undoable ? '撤销' : '不可撤销'}</span>
      </button>
    </div>
  );
}

function MemoryItem({
  memory,
  busyAction,
  onAction
}: {
  memory: AgentMemory;
  busyAction: string;
  onAction: (action: 'confirm' | 'reject' | 'archive' | 'delete') => void;
}) {
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
        <p>{memory.content || '无正文'}</p>
        <div className="query-tags agent-tags">
          <span>{memory.memoryType}</span>
          <span>{memory.namespace}</span>
          <span>{memory.scopeType}{memory.scopeId ? `:${memory.scopeId}` : ''}</span>
          {memory.sourceTaskId ? <span>任务 {memory.sourceTaskId}</span> : null}
          {memory.importance !== undefined && memory.importance !== null ? <span>重要度 {Math.round(memory.importance * 100)}%</span> : null}
        </div>
      </div>
      <div className="agent-memory-actions">
        {isPending ? (
          <>
            <button className="chip-button is-active" onClick={() => onAction('confirm')} disabled={Boolean(busyAction)} type="button">
              {busy('confirm') ? <Loader2 className="spin" size={15} /> : <CheckCircle2 size={15} />}
              确认
            </button>
            <button className="chip-button danger" onClick={() => onAction('reject')} disabled={Boolean(busyAction)} type="button">
              {busy('reject') ? <Loader2 className="spin" size={15} /> : <XCircle size={15} />}
              拒绝
            </button>
          </>
        ) : null}
        {canArchive ? (
          <button className="chip-button" onClick={() => onAction('archive')} disabled={Boolean(busyAction)} type="button">
            {busy('archive') ? <Loader2 className="spin" size={15} /> : <Archive size={15} />}
            归档
          </button>
        ) : null}
        {memory.status !== 'DELETED' ? (
          <button className="chip-button danger" onClick={() => onAction('delete')} disabled={Boolean(busyAction)} type="button">
            {busy('delete') ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
            删除
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
  const resumeTemplateFill = source?.resumeTemplateFill && typeof source.resumeTemplateFill === 'object'
    ? source.resumeTemplateFill as Record<string, unknown>
    : null;
  if (!alignment.length && !gaps.length && !webReferences.length && !resumeTemplateFill) return null;
  return (
    <div className="agent-planning-result">
      {alignment.length ? (
        <div>
          <h4>证据对齐</h4>
          <div className="agent-alignment-list">
            {alignment.map((item) => {
              const entry = item as Record<string, unknown>;
              const status = stringValue(entry.status);
              return (
                <div className="agent-alignment-row" key={`${String(entry.requirement)}-${status}`}>
                  <span className={`evidence-status ${alignmentStatusClass(status)}`}>{alignmentStatusLabel(status)}</span>
                  <strong>{String(entry.requirement || '未命名要求')}</strong>
                  <small>{String(entry.reason || '')}</small>
                  <div className="query-tags agent-tags">
                    {normalizeStringList(entry.evidenceIds).map((id) => <span key={id}>{id}</span>)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      {gaps.length ? (
        <div>
          <h4>能力缺口</h4>
          <div className="agent-gap-list">
            {gaps.map((item) => {
              const gap = item as Record<string, unknown>;
              return (
                <div className="agent-gap-row" key={String(gap.skill)}>
                  <strong>{String(gap.skill || '待补充能力')}</strong>
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
          <h4>联网参考</h4>
          <div className="agent-web-reference-list">
            {webReferences.map((item) => {
              const reference = item as Record<string, unknown>;
              return (
                <a className="agent-web-reference-row" href={String(reference.sourceUrl || '#')} target="_blank" rel="noreferrer" key={String(reference.sourceUrl || reference.title)}>
                  <strong>{String(reference.title || '外部参考')}</strong>
                  <span>{String(reference.confidence || 'LOW')} · {String(reference.score ?? '')}</span>
                  <p>{String(reference.summary || '')}</p>
                </a>
              );
            })}
          </div>
        </div>
      ) : null}
      {resumeTemplateFill ? (
        <div className="agent-template-fill-section">
          <h4>简历模板填充</h4>
          <div className="agent-template-fill-card">
            <strong>{String(resumeTemplateFill.status || 'UNKNOWN')}</strong>
            {resumeTemplateFill.outputPath ? <p>{String(resumeTemplateFill.outputPath)}</p> : null}
            {resumeTemplateFill.errorMessage ? <p>{String(resumeTemplateFill.errorMessage)}</p> : null}
            <div className="query-tags agent-tags">
              {normalizeStringList(resumeTemplateFill.placeholders).map((placeholder) => <span key={placeholder}>{placeholder}</span>)}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ToolCallItem({ call }: { call: AgentToolCall }) {
  return (
    <div className="agent-timeline-item">
      <span className={`agent-timeline-dot ${taskStatusClass(call.status)}`} />
      <div>
        <div className="agent-timeline-head">
          <strong>{call.toolName}</strong>
          <span className={`status-pill ${taskStatusClass(call.status)}`}>{statusLabel(call.status)}</span>
        </div>
        <p>{formatToolResponse(call.response)}</p>
        {call.errorMessage ? <small>{call.errorCode}：{call.errorMessage}</small> : null}
      </div>
    </div>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    CREATED: '已创建',
    RUNNING: '运行中',
    WAITING_TOOL_RESULT: '等待工具',
    WAITING_PLAN_REVIEW: '等待计划审批',
    WAITING_CRUD_REVIEW: '等待变更审批',
    WAITING_OUTPUT_REVIEW: '等待输出确认',
    COMPLETED: '已完成',
    CANCELED: '已取消',
    FAILED: '失败',
    SUCCEEDED: '成功',
    REJECTED: '已拒绝',
    PENDING: '等待中',
    APPLIED_UNDOABLE: '可撤销',
    UNDONE: '已撤销',
    UNDO_EXPIRED: '撤销过期'
  };
  return labels[status || ''] || status || '未知';
}

function formatTemplateStatus(status: string) {
  if (status === 'READY') return '已解析';
  if (status === 'PARSING') return '解析中';
  if (status === 'EXPORTED') return '已导出';
  if (status === 'FAILED') return '解析失败';
  return status || '未知状态';
}

function formatPatchStatus(status?: string) {
  if (status === 'CONFIRMED') return '已确认';
  if (status === 'VALIDATED') return '已校验';
  if (status === 'REJECTED') return '已拒绝';
  if (status === 'EXPORTED') return '已导出';
  return '待确认';
}

function patchStatusClass(status?: string) {
  if (status === 'CONFIRMED' || status === 'VALIDATED' || status === 'EXPORTED') return 'indexed';
  if (status === 'REJECTED') return 'danger';
  return 'running';
}

function patchRowClass(status?: string) {
  if (status === 'CONFIRMED' || status === 'VALIDATED' || status === 'EXPORTED') return 'is-confirmed';
  if (status === 'REJECTED') return 'is-rejected';
  return 'is-draft';
}

function formatRisk(flag: string) {
  const labels: Record<string, string> = {
    NONE: '无风险',
    MISSING_EVIDENCE: '缺少证据',
    LOW_CONFIDENCE: '低置信度',
    OVER_LENGTH: '长度风险',
    LAYOUT_RISK: '版式风险',
    SENSITIVE_INFO: '敏感信息',
    UNSUPPORTED_REGION: '不支持区域',
    INJECTION_RISK: '注入风险'
  };
  return labels[flag] || flag;
}

function templateCanFill(template: ResumeTemplate) {
  return ['READY', 'EXPORTED'].includes(template.status);
}

function rankResumeMaterials(materials: LearningMaterial[]) {
  return [...materials]
    .filter((material) => materialCanShowAsResumeCandidate(material))
    .sort((left, right) => {
      const scoreDelta = resumeMaterialScore(right) - resumeMaterialScore(left);
      if (scoreDelta !== 0) return scoreDelta;
      return timeValue(right.updatedAt || right.createdAt) - timeValue(left.updatedAt || left.createdAt);
    });
}

function materialCanShowAsResumeCandidate(material: LearningMaterial) {
  const documentType = (material.documentType || '').toLowerCase();
  const name = `${material.title || ''} ${material.originalFilename || ''}`.toLowerCase();
  const typeAllowed = ['docx', 'doc', 'pdf', 'markdown', 'text', 'txt'].includes(documentType);
  const nameLooksLikeResume = name.includes('简历') || name.includes('resume') || /\bcv\b/.test(name);
  return typeAllowed || nameLooksLikeResume || Boolean((material.documentSummary || '').trim());
}

function resumeMaterialScore(material: LearningMaterial) {
  const name = `${material.title || ''} ${material.originalFilename || ''}`.toLowerCase();
  const documentType = (material.documentType || '').toLowerCase();
  let score = 0;
  if (name.includes('简历') || name.includes('resume') || /\bcv\b/.test(name)) score += 100;
  if ((material.documentSummary || '').trim()) score += 30;
  if (material.status === 'READY' || material.status === 'PARTIAL') score += 20;
  if (['docx', 'doc', 'pdf'].includes(documentType)) score += 10;
  return score;
}

function materialCanUseAsResume(material: LearningMaterial) {
  return ['READY', 'PARTIAL'].includes(material.status) || Boolean((material.documentSummary || '').trim());
}

function formatMaterialStatus(status: string) {
  if (status === 'READY') return '已入库';
  if (status === 'PARTIAL') return '部分可用';
  if (status === 'PARSING') return '解析中';
  if (status === 'REINDEXING') return '重建索引';
  if (status === 'PROCESSING') return '处理中';
  if (status === 'FAILED') return '解析失败';
  return status || '未知状态';
}

function timeValue(value?: string | null) {
  const parsed = value ? Date.parse(value) : 0;
  return Number.isFinite(parsed) ? parsed : 0;
}

function statusIcon(status: string | undefined, active: boolean) {
  if (active) return <Loader2 className="spin" size={15} />;
  if (status === 'COMPLETED') return <CheckCircle2 size={15} />;
  if (status === 'FAILED' || status === 'REJECTED') return <XCircle size={15} />;
  return <Clock3 size={15} />;
}

function taskStatusClass(status?: string) {
  if (status === 'COMPLETED' || status === 'SUCCEEDED') return 'indexed';
  if (status === 'FAILED' || status === 'REJECTED') return 'danger';
  if (status === 'RUNNING' || status === 'WAITING_TOOL_RESULT' || status === 'WAITING_PLAN_REVIEW' || status === 'WAITING_CRUD_REVIEW' || status === 'WAITING_OUTPUT_REVIEW') return 'running';
  return '';
}

function reviewTitle(reviewType: string) {
  if (reviewType === 'OUTPUT') return '输出确认';
  if (reviewType === 'CRUD') return '变更确认';
  return '计划确认';
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
    PENDING_REVIEW: '待确认',
    PENDING_INDEX: '待索引',
    ACTIVE: '已激活',
    INDEX_FAILED: '索引失败',
    ARCHIVED: '已归档',
    SUPERSEDED: '已替换',
    REJECTED: '已拒绝',
    DELETED: '已删除'
  };
  return labels[status] || status || '未知';
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
  if (status === 'supported') return '已支持';
  if (status === 'weak') return '证据偏弱';
  return '缺证据';
}

function formatToolResponse(response?: Record<string, unknown>) {
  if (!response) return '暂无观察摘要';
  const parts = [
    response.evidenceCount !== undefined ? `证据 ${response.evidenceCount}` : '',
    response.answerLength !== undefined ? `回答长度 ${response.answerLength}` : '',
    response.expandedQueryCount !== undefined ? `扩展查询 ${response.expandedQueryCount}` : '',
    response.operationId !== undefined ? `操作 ${response.operationId}` : '',
    response.undoDeadline !== undefined ? `撤销 ${formatTime(String(response.undoDeadline))}` : '',
    Array.isArray(response.diagnosticKeys) ? `诊断 ${response.diagnosticKeys.length}` : ''
  ].filter(Boolean);
  if (parts.length) return parts.join(' · ');
  return JSON.stringify(response);
}

function normalizeStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
}

function normalizeRecordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : [];
}

function buildGoalForMode(mode: AgentWorkspaceMode, goal: string) {
  if (mode !== 'general') return goal;
  return [
    goal,
    '',
    '请按通用探索模式处理：可以闲聊式解释、整理松散学习资料、给出推荐的资料标题、标签、摘要和入库步骤。',
    '如果用户想把内容写入 RAG 数据库，只给出资料库上传/粘贴入库建议，不执行任何写入或状态变更。'
  ].join('\n');
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : '';
}

function formatTime(value?: string | null) {
  if (!value) return '未返回';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function clamp(value: number, min: number, max: number) {
  if (Number.isNaN(value)) return min;
  return Math.max(min, Math.min(max, value));
}

function undoExpired(value?: string | null) {
  if (!value) return true;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) || date.getTime() <= Date.now();
}
