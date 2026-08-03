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
  answer?: string | null;
  hint?: string | null;
  evidenceRefs?: RagEvidence[];
  dueAt?: string | null;
  retrievability?: number | null;
  reviewCount: number;
  lapseCount: number;
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

export interface ReviewMissingKnowledgeResult {
  materialId: number;
  assistantMessage: string;
  addedCount: number;
  skippedCount: number;
  cards: ReviewCard[];
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
  cardCount: number;
  cards: ReviewCard[];
}

export interface ReviewFolderDetail {
  folder: ReviewFolder;
  materials: ReviewFolderMaterial[];
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
export function generateReviewMaterial(materialId: number, userFeedback?: string): Promise<ReviewMaterial> {
  const feedback = userFeedback?.trim();
  return request<ReviewMaterial>(`/api/reviews/materials/${encodeURIComponent(String(materialId))}/generate`, {
    method: 'POST',
    ...(feedback ? { headers: jsonHeaders, body: JSON.stringify({ userFeedback: feedback }) } : {})
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
