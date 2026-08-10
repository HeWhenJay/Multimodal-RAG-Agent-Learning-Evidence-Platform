import type { RagEvidence, Result } from './types';
import { getStoredAuthToken } from './auth';

const jsonHeaders = {
  'Content-Type': 'application/json'
};

export const REVIEW_OVERVIEW_UPDATED_EVENT = 'review-overview-updated';
export const REVIEW_CONTENT_UPDATED_EVENT = 'review-content-updated';

export interface ReviewSettings {
  enabled: boolean;
  desiredRetention: number;
  dailyLimit: number;
  reminderTime: string;
  timezone: string;
}

export interface ReviewCard {
  id: number;
  materialId: number;
  materialTitle: string;
  documentType: string;
  question: string;
  sourceType?: 'RAG' | 'MANUAL';
  answer?: string | null;
  hint?: string | null;
  evidenceRefs?: RagEvidence[];
  dueAt?: string | null;
  retrievability?: number | null;
  reviewCount: number;
  lapseCount: number;
  isUserEdited?: boolean;
  updatedAt?: string | null;
}

export interface ReviewCardContent {
  question: string;
  answer: string;
  hint?: string | null;
}

export type ReviewCardRewriteMode = 'STRICT_SOURCE' | 'SOURCE_FIRST' | 'SOURCE_REFERENCE';

export interface ReviewCardUpdatePayload extends ReviewCardContent {
  rewriteMode?: ReviewCardRewriteMode;
  evidenceIds?: string[];
}

export interface ReviewCardRewritePayload {
  instruction: string;
  mode: ReviewCardRewriteMode;
}

export interface ReviewCardRewritePreview {
  cardId: number;
  mode: ReviewCardRewriteMode;
  original: ReviewCardContent;
  proposed: ReviewCardContent;
  evidenceRefs: RagEvidence[];
  modelName: string;
}

export interface ReviewMaterialCardSnapshot {
  cardId?: number | null;
  content: ReviewCardContent;
  evidenceRefs: RagEvidence[];
  evidenceIds: string[];
}

export interface ReviewMaterialRewritePayload {
  instruction: string;
  mode: ReviewCardRewriteMode;
  targetCardCount?: number | null;
  baseCards?: ReviewMaterialCardSnapshot[];
}

export interface ReviewMaterialRewritePreview {
  materialId: number;
  title: string;
  sourceVersion: number;
  originalFingerprint: string;
  originalCardIds: number[];
  originalCards: ReviewMaterialCardSnapshot[];
  proposedCards: ReviewMaterialCardSnapshot[];
  targetCardCount: number;
  originalSummary?: string | null;
  proposedSummary?: string | null;
  mergeNote?: string | null;
  mode: ReviewCardRewriteMode;
  modelName: string;
}

export interface ReviewRewriteProgressEvent {
  stageCode: string;
  stageLabel: string;
  message: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  percent: number;
  createdAt?: string | null;
}

export interface ReviewRewriteTaskProgress extends ReviewRewriteProgressEvent {
  events: ReviewRewriteProgressEvent[];
}

export interface ReviewCardRewriteTask {
  taskId: string;
  cardId: number;
  instruction: string;
  mode: ReviewCardRewriteMode;
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  progress: ReviewRewriteTaskProgress;
  result?: ReviewCardRewritePreview | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ReviewMaterialRewriteTask {
  taskId: string;
  materialId: number;
  instruction: string;
  mode: ReviewCardRewriteMode;
  targetCardCount: number;
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  progress: ReviewRewriteTaskProgress;
  result?: ReviewMaterialRewritePreview | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ReviewMaterialRewriteApplyPayload {
  sourceVersion: number;
  originalFingerprint: string;
  originalCardIds: number[];
  proposedCards: ReviewCardUpdatePayload[];
  proposedSummary?: string | null;
}

export interface ReviewMaterialRewriteApplyResult {
  material: ReviewMaterial;
  cards: ReviewCard[];
  replacedCardIds: number[];
}

export interface ReviewEvidenceSegment {
  segmentId: string;
  segmentIndex: number;
  totalSegments: number;
  title: string;
  characterCount: number;
  evidenceCount: number;
  rawContent: string;
  evidenceRefs: RagEvidence[];
}

export interface ReviewSegmentWorkspace {
  materialId: number;
  title: string;
  sourceVersion: number;
  originalFingerprint: string;
  originalCardIds: number[];
  originalCards: ReviewMaterialCardSnapshot[];
  originalSummary?: string | null;
  segments: ReviewEvidenceSegment[];
}

export interface ReviewSegmentGenerationPayload {
  segmentIds: string[];
  prompts: Record<string, string>;
  mode: 'STANDARD' | 'RELAXED';
  forceRestart?: boolean;
}

export interface ReviewSegmentResult {
  segmentId: string;
  segmentIndex: number;
  title: string;
  status: 'SUCCEEDED' | 'FAILED';
  summary?: string | null;
  cards: ReviewMaterialCardSnapshot[];
  qualityFeedback: string[];
  error?: string | null;
}

export interface ReviewSegmentGenerationResult {
  materialId: number;
  sourceVersion: number;
  segments: ReviewSegmentResult[];
}

export interface ReviewSegmentProgressEvent {
  stageCode: string;
  stageLabel: string;
  message: string;
  status: string;
  percent: number;
  currentStep?: number | null;
  totalSteps?: number | null;
  attempt?: number | null;
  maxAttempts?: number | null;
  currentSegmentId?: string | null;
  currentSegmentIndex?: number | null;
  totalSegments?: number | null;
  completedSegments?: number | null;
  detail?: string | null;
  elapsedSeconds?: number | null;
  heartbeatAt?: string | null;
  createdAt?: string | null;
}

export interface ReviewSegmentTaskProgress extends ReviewSegmentProgressEvent {
  events: ReviewSegmentProgressEvent[];
}

export interface ReviewSegmentGenerationTask {
  taskId: string;
  materialId: number;
  mode: 'STANDARD' | 'RELAXED';
  segmentIds: string[];
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  progress: ReviewSegmentTaskProgress;
  result?: ReviewSegmentGenerationResult | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ReviewSegmentMergePayload {
  sourceVersion: number;
  originalFingerprint: string;
  originalCardIds: number[];
  proposedCards: ReviewCardUpdatePayload[];
  proposedSummary?: string | null;
}

export interface ReviewOverview {
  dueCount: number;
  actionableDueCount: number;
  todayReviewedCount: number;
  totalCardCount: number;
  activeMaterialCount: number;
  nextDueAt?: string | null;
  settings: ReviewSettings;
}

export interface ReviewCardGroup {
  materialId: number;
  materialTitle: string;
  materialSummary?: string | null;
  documentType: string;
  folderId?: number | null;
  folderName?: string | null;
  dueCardCount: number;
  cards: ReviewCard[];
}

export interface ReviewDueGroups {
  totalDueCount: number;
  remainingToday: number;
  groups: ReviewCardGroup[];
}

export interface ReviewSyncResult {
  processedMaterialCount: number;
  generatedCardCount: number;
  skippedMaterialCount: number;
  failedMaterialCount: number;
}

export interface ReviewGenerationProgressEvent {
  stageCode: string;
  stageLabel: string;
  message: string;
  status: string;
  currentStep?: number | null;
  totalSteps?: number | null;
  percent: number;
  attempt?: number | null;
  maxAttempts?: number | null;
  detail?: string | null;
  createdAt?: string | null;
}

export interface ReviewGenerationProgress extends ReviewGenerationProgressEvent {
  events: ReviewGenerationProgressEvent[];
}

export interface ReviewMissingKnowledgeMessage {
  role: 'USER' | 'ASSISTANT';
  content: string;
}

export interface ReviewManualCardPayload {
  question: string;
  answer: string;
  hint?: string;
}

export interface ReviewMissingKnowledgeResult {
  materialId: number;
  assistantMessage: string;
  addedCount: number;
  skippedCount: number;
  cards: ReviewCard[];
}

export interface ReviewMissingKnowledgeProgressEvent {
  stageCode: string;
  stageLabel: string;
  message: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  percent: number;
  createdAt?: string | null;
}

export interface ReviewMissingKnowledgeTaskProgress extends ReviewMissingKnowledgeProgressEvent {
  events: ReviewMissingKnowledgeProgressEvent[];
}

export interface ReviewMissingKnowledgeTask {
  taskId: string;
  materialId: number;
  message: string;
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  progress: ReviewMissingKnowledgeTaskProgress;
  result?: ReviewMissingKnowledgeResult | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ReviewMaterial {
  materialId: number;
  id?: number;
  materialTitle?: string | null;
  title: string;
  summary?: string | null;
  documentType: string;
  materialStatus?: string | null;
  isLearningContent?: boolean | null;
  classification?: string | null;
  category?: string | null;
  classificationReason?: string | null;
  reason?: string | null;
  generationStatus?: string | null;
  generationState?: string | null;
  status: 'PENDING' | 'GENERATED' | 'SKIPPED' | 'FAILED' | 'NEEDS_REVIEW' | string;
  cardCount: number;
  generationAttempts?: number;
  qualityFeedback?: string[];
  generationProgress?: ReviewGenerationProgress | null;
  needsManualReview?: boolean;
  folderId?: number | null;
  folderName?: string | null;
  indexRequestVersion?: number;
  syncedIndexRequestVersion?: number | null;
  updatedAt?: string | null;
  errorMessage?: string | null;
}

export type ReviewGenerationAction = 'REGENERATE' | 'KEEP_CURRENT';
export type ReviewGenerationMode = 'STANDARD' | 'RELAXED' | 'SEGMENTED';

export interface ReviewGenerationOptions {
  action?: ReviewGenerationAction;
  mode?: ReviewGenerationMode;
  userFeedback?: string;
}

export interface ReviewFolder {
  id: number;
  name: string;
  materialCount: number;
  cardCount: number;
  dueCardCount: number;
  updatedAt?: string | null;
}

export interface ReviewFolderMaterial {
  materialId: number;
  title: string;
  summary?: string | null;
  documentType: string;
  materialStatus?: string | null;
  category?: string | null;
  status?: string;
  reason?: string | null;
  generationProgress?: ReviewGenerationProgress | null;
  indexRequestVersion?: number;
  cardCount: number;
  cards: ReviewCard[];
}

export interface ReviewFolderDetail {
  folder: ReviewFolder;
  materials: ReviewFolderMaterial[];
}

export interface ReviewCardLibraryMaterial {
  materialId: number;
  title: string;
  summary?: string | null;
  documentType: string;
  folderId?: number | null;
  folderName?: string | null;
  cardCount: number;
  reviewedCardCount: number;
  cards: ReviewCard[];
}

export interface ReviewCardLibrary {
  totalMaterialCount: number;
  totalCardCount: number;
  reviewedCardCount: number;
  materials: ReviewCardLibraryMaterial[];
}

export interface ReviewFolderAssignmentResult {
  folderId?: number | null;
  materialIds: number[];
  movedCount: number;
}

export interface ReviewFolderDeletionResult {
  folderId: number;
  deleted: boolean;
  unfiledMaterialCount: number;
}

export interface ReviewGradePayload {
  rating: 1 | 2 | 3 | 4;
  durationMs?: number;
}

export interface ReviewGradeResult {
  card: ReviewCard;
  previousDueAt?: string | null;
  nextDueAt?: string | null;
  intervalDays: number;
  retrievability: number;
}

export interface ReviewDeletionResult {
  scope: 'CARD' | 'MATERIAL';
  materialId: number;
  cardId?: number | null;
  deleted: boolean;
}

export interface ReviewBatchDeletionResult {
  scope: 'CARD' | 'MATERIAL';
  requestedCount: number;
  deletedCount: number;
  cardIds: number[];
  materialIds: number[];
}

export interface ReviewGroupOrderResult {
  materialIds: number[];
  orderedCount: number;
}

// 统一处理复习接口响应，并自动携带当前登录用户的令牌。
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const token = getStoredAuthToken();
  const headers = new Headers(init?.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    throw new Error(`HTTP 请求失败：${response.status}`);
  }
  const envelope = (await response.json()) as Result<T>;
  if (envelope.code !== 1) {
    throw new Error(envelope.msg || '复习接口请求失败');
  }
  return envelope.data;
}

// 扫描已入库学习资料并生成关键知识点卡片。
export function syncReviewMaterials(limit = 1): Promise<ReviewSyncResult> {
  return request<ReviewSyncResult>(`/api/reviews/sync?limit=${encodeURIComponent(String(limit))}`, {
    method: 'POST'
  });
}

// 读取当前用户的复习概览和提醒设置。
export function fetchReviewOverview(): Promise<ReviewOverview> {
  return request<ReviewOverview>('/api/reviews/overview');
}

// 读取当前已到期的知识点卡片队列。
export function fetchDueReviewCards(limit = 20): Promise<ReviewCard[]> {
  return request<ReviewCard[]>(`/api/reviews/due?limit=${encodeURIComponent(String(limit))}`);
}

// 按上传资料读取每日到期卡片，页面以 group 展示同一份文档的多个知识点。
export function fetchDueReviewGroups(limit = 20): Promise<ReviewDueGroups> {
  return request<ReviewDueGroups>(`/api/reviews/due-groups?limit=${encodeURIComponent(String(limit))}`);
}

// 一次提交当前可见资料组的完整顺序，服务端按用户持久保存优先级。
export function updateDueReviewGroupOrder(materialIds: number[]): Promise<ReviewGroupOrderResult> {
  return request<ReviewGroupOrderResult>('/api/reviews/due-groups/order', {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ materialIds })
  });
}

// 用户主动揭示时读取答案和完整原文 evidence，避免到期列表提前暴露答案。
export function fetchReviewCard(cardId: number): Promise<ReviewCard> {
  return request<ReviewCard>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}`);
}

// 读取当前用户所有文档的全部活动卡片，包括已经复习过的卡片；此接口不提供评分队列。
export function fetchReviewCardLibrary(): Promise<ReviewCardLibrary> {
  return request<ReviewCardLibrary>('/api/reviews/cards/library');
}

// 保存用户编辑后的卡片正文，可选更新经过服务端校验的 evidence 引用。
export function updateReviewCard(cardId: number, payload: ReviewCardUpdatePayload): Promise<ReviewCard> {
  return request<ReviewCard>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}`, {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 请求 LLM 生成单卡片的原文约束改写预览，不会直接写入数据库。
export function previewReviewCardRewrite(cardId: number, payload: ReviewCardRewritePayload): Promise<ReviewCardRewritePreview> {
  return request<ReviewCardRewritePreview>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}/rewrite-preview`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 创建单卡片后台改写任务，调用返回后模型仍会继续生成候选。
export function startReviewCardRewriteTask(cardId: number, payload: ReviewCardRewritePayload): Promise<ReviewCardRewriteTask> {
  return request<ReviewCardRewriteTask>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}/rewrite-tasks`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 重新打开单卡片改写弹窗时恢复该卡片最近一次任务。
export function fetchLatestReviewCardRewriteTask(cardId: number): Promise<ReviewCardRewriteTask | null> {
  return request<ReviewCardRewriteTask | null>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}/rewrite-tasks/latest`);
}

// 轮询一条单卡片后台改写任务。
export function fetchReviewCardRewriteTask(cardId: number, taskId: string): Promise<ReviewCardRewriteTask> {
  return request<ReviewCardRewriteTask>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}/rewrite-tasks/${encodeURIComponent(taskId)}`);
}

// 请求资料级 AI 合并预览，不会直接覆盖任何现有卡片。
export function previewReviewMaterialRewrite(materialId: number, payload: ReviewMaterialRewritePayload): Promise<ReviewMaterialRewritePreview> {
  return request<ReviewMaterialRewritePreview>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/rewrite-preview`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 创建资料级后台合并/重新生成任务，立即返回可查询状态。
export function startReviewMaterialRewriteTask(materialId: number, payload: ReviewMaterialRewritePayload): Promise<ReviewMaterialRewriteTask> {
  return request<ReviewMaterialRewriteTask>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/rewrite-tasks`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 重新打开资料改写弹窗时恢复该资料最近一次任务。
export function fetchLatestReviewMaterialRewriteTask(materialId: number): Promise<ReviewMaterialRewriteTask | null> {
  return request<ReviewMaterialRewriteTask | null>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/rewrite-tasks/latest`);
}

// 轮询一条资料级后台合并/重新生成任务。
export function fetchReviewMaterialRewriteTask(materialId: number, taskId: string): Promise<ReviewMaterialRewriteTask> {
  return request<ReviewMaterialRewriteTask>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/rewrite-tasks/${encodeURIComponent(taskId)}`);
}

// 应用用户确认后的资料级候选，服务端会校验原卡片版本并原子替换。
export function applyReviewMaterialRewrite(materialId: number, payload: ReviewMaterialRewriteApplyPayload): Promise<ReviewMaterialRewriteApplyResult> {
  return request<ReviewMaterialRewriteApplyResult>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/rewrite-apply`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 读取资料当前索引版本对应的原始分段，供用户先查看再决定生成范围。
export function fetchReviewSegmentWorkspace(materialId: number): Promise<ReviewSegmentWorkspace> {
  return request<ReviewSegmentWorkspace>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/segments`);
}

// 只为用户勾选的分段创建后台任务，每段提示词互相独立。
export function startReviewSegmentTask(materialId: number, payload: ReviewSegmentGenerationPayload): Promise<ReviewSegmentGenerationTask> {
  return request<ReviewSegmentGenerationTask>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/segment-tasks`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 重新打开工作台时恢复该资料最近一次后台分段任务。
export function fetchLatestReviewSegmentTask(materialId: number): Promise<ReviewSegmentGenerationTask | null> {
  return request<ReviewSegmentGenerationTask | null>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/segment-tasks/latest`);
}

// 轮询指定分段任务，不阻塞页面上的原文浏览和候选编辑。
export function fetchReviewSegmentTask(materialId: number, taskId: string): Promise<ReviewSegmentGenerationTask> {
  return request<ReviewSegmentGenerationTask>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/segment-tasks/${encodeURIComponent(taskId)}`);
}

// 将用户确认参与的候选一次性发布为正式复习卡片。
export function mergeReviewSegments(materialId: number, payload: ReviewSegmentMergePayload): Promise<ReviewMaterialRewriteApplyResult> {
  return request<ReviewMaterialRewriteApplyResult>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/segments/merge`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 读取资料分类、卡片生成状态和卡片数量。
export function fetchReviewMaterials(): Promise<ReviewMaterial[]> {
  return request<ReviewMaterial[]>('/api/reviews/materials');
}

// 读取当前用户的复习文件夹及文档、卡片和到期统计。
export function fetchReviewFolders(): Promise<ReviewFolder[]> {
  return request<ReviewFolder[]>('/api/reviews/folders');
}

// 进入文件夹后按文档读取全部活动卡片，列表阶段不预加载答案。
export function fetchReviewFolder(folderId: number): Promise<ReviewFolderDetail> {
  return request<ReviewFolderDetail>(`/api/reviews/folders/${encodeURIComponent(String(folderId))}`);
}

// 一次提交当前文件夹内文档的完整顺序，服务端按文件夹独立保存优先级。
export function updateReviewFolderMaterialOrder(folderId: number, materialIds: number[]): Promise<ReviewGroupOrderResult> {
  return request<ReviewGroupOrderResult>(`/api/reviews/folders/${encodeURIComponent(String(folderId))}/materials/order`, {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ materialIds })
  });
}

// 创建当前用户的复习文件夹。
export function createReviewFolder(name: string): Promise<ReviewFolder> {
  return request<ReviewFolder>('/api/reviews/folders', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ name })
  });
}

// 重命名文件夹并保留既有文档归属。
export function renameReviewFolder(folderId: number, name: string): Promise<ReviewFolder> {
  return request<ReviewFolder>(`/api/reviews/folders/${encodeURIComponent(String(folderId))}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify({ name })
  });
}

// 删除文件夹只解除文档归档，不删除资料、卡片或评分记录。
export function deleteReviewFolder(folderId: number): Promise<ReviewFolderDeletionResult> {
  return request<ReviewFolderDeletionResult>(`/api/reviews/folders/${encodeURIComponent(String(folderId))}`, {
    method: 'DELETE'
  });
}

// 以整份文档为单位批量移动；folderId 为空时恢复未归档。
export function assignReviewMaterialsToFolder(
  materialIds: number[],
  folderId: number | null
): Promise<ReviewFolderAssignmentResult> {
  return request<ReviewFolderAssignmentResult>('/api/reviews/materials/folder', {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ materialIds, folderId })
  });
}

// 对单条学习资料重新分类并生成关键知识点卡片。
export function generateReviewMaterial(
  materialId: number,
  options: ReviewGenerationOptions = {},
): Promise<ReviewMaterial> {
  const feedback = options.userFeedback?.trim();
  const body = {
    action: options.action || 'REGENERATE',
    mode: options.mode || 'STANDARD',
    ...(feedback ? { userFeedback: feedback } : {})
  };
  return request<ReviewMaterial>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/generate`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(body)
  });
}

// 由用户直接创建一张当前资料的复习卡片，不依赖模型补漏。
export function createManualReviewCard(
  materialId: number,
  payload: ReviewManualCardPayload
): Promise<ReviewCard> {
  return request<ReviewCard>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/cards`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 用户指出遗漏主题后，只从当前文档 evidence 中追加新卡片，不重建既有卡片。
export function supplementReviewMissingKnowledge(
  materialId: number,
  message: string,
  conversation: ReviewMissingKnowledgeMessage[] = []
): Promise<ReviewMissingKnowledgeResult> {
  return request<ReviewMissingKnowledgeResult>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/missing-knowledge`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ message, conversation })
  });
}

// 创建后台补漏任务，提交后允许用户关闭对话窗口。
export function startSupplementReviewMissingKnowledge(
  materialId: number,
  message: string,
  conversation: ReviewMissingKnowledgeMessage[] = []
): Promise<ReviewMissingKnowledgeTask> {
  return request<ReviewMissingKnowledgeTask>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/missing-knowledge/tasks`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ message, conversation })
  });
}

// 读取当前资料最近一次补漏任务，重新打开弹窗时恢复状态。
export function fetchLatestSupplementReviewMissingKnowledge(
  materialId: number
): Promise<ReviewMissingKnowledgeTask | null> {
  return request<ReviewMissingKnowledgeTask | null>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/missing-knowledge/tasks/latest`);
}

// 轮询后台补漏任务的阶段、进度和最终结果。
export function fetchSupplementReviewMissingKnowledgeTask(
  materialId: number,
  taskId: string
): Promise<ReviewMissingKnowledgeTask> {
  return request<ReviewMissingKnowledgeTask>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/missing-knowledge/tasks/${encodeURIComponent(taskId)}`);
}

// 将整份资料永久移出复习中心，原始 RAG 文件和索引保持不变。
export function deleteReviewMaterial(materialId: number): Promise<ReviewDeletionResult> {
  return request<ReviewDeletionResult>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}`, {
    method: 'DELETE'
  });
}

// 批量将资料移出复习中心，服务端在单个事务中去重并校验用户归属。
export function deleteReviewMaterials(materialIds: number[]): Promise<ReviewBatchDeletionResult> {
  return request<ReviewBatchDeletionResult>('/api/reviews/materials/batch-delete', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ materialIds })
  });
}

// 提交回忆评分，让服务端使用 FSRS 计算下一次到期时间。
export function gradeReviewCard(cardId: number, payload: ReviewGradePayload): Promise<ReviewGradeResult> {
  return request<ReviewGradeResult>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}/grade`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 删除单张卡片并持久保存来源排除记录，后续生成不会恢复同一卡片。
export function deleteReviewCard(cardId: number): Promise<ReviewDeletionResult> {
  return request<ReviewDeletionResult>(`/api/reviews/cards/${encodeURIComponent(String(cardId))}`, {
    method: 'DELETE'
  });
}

// 批量删除复习卡片，并为每个来源键保存永久排除记录。
export function deleteReviewCards(cardIds: number[]): Promise<ReviewBatchDeletionResult> {
  return request<ReviewBatchDeletionResult>('/api/reviews/cards/batch-delete', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ cardIds })
  });
}

// 更新当前用户的复习提醒设置。
export function updateReviewSettings(payload: ReviewSettings): Promise<ReviewSettings> {
  return request<ReviewSettings>('/api/reviews/settings', {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}
