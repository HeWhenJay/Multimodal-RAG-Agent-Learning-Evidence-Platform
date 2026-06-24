import type {
  LearningMaterial,
  MaterialPreview,
  MaterialUploadChunk,
  RagOverview,
  RagProgress,
  RagQueryHistory,
  RagQueryPayload,
  RagQueryResult,
  RagQueryTask,
  Result,
  ResumeContentPatch,
  ResumePatchDraft,
  ResumeTemplate,
  ResumeTemplateExport,
  ResumeTemplatePreview,
  ResumeTemplateRegionAnnotation
} from './types';
import { getStoredAuthToken } from './auth';

const jsonHeaders = {
  'Content-Type': 'application/json'
};

// 统一处理 RAG 接口响应和业务错误。
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
    throw new Error(envelope.msg || '请求失败');
  }
  return envelope.data;
}

// 获取 RAG 概览数据。
export function fetchOverview(): Promise<RagOverview> {
  return request<RagOverview>('/api/rag/overview');
}

// 获取最近学习资料列表。
export function fetchMaterials(): Promise<LearningMaterial[]> {
  return request<LearningMaterial[]>('/api/rag/materials');
}

// 获取单个学习资料详情。
export function fetchMaterial(id: number): Promise<LearningMaterial> {
  return request<LearningMaterial>(`/api/rag/materials/${id}`);
}

// 获取单个学习资料的 evidence 片段。
export function fetchMaterialEvidences(id: number, limit = 20): Promise<RagQueryResult['evidences']> {
  return request<RagQueryResult['evidences']>(`/api/rag/materials/${id}/evidences?limit=${limit}`);
}

// 读取文本类学习资料原文，供新标签预览页渲染。
export function fetchMaterialPreview(id: number, source?: string | null): Promise<MaterialPreview> {
  const search = new URLSearchParams();
  if (source) {
    search.set('source', source);
  }
  const query = search.toString();
  return request<MaterialPreview>(`/api/rag/materials/${id}/preview${query ? `?${query}` : ''}`);
}

// 提交文本学习资料并触发索引。
export function indexText(payload: {
  title: string;
  documentType: string;
  source: string;
  content: string;
}): Promise<LearningMaterial> {
  return request<LearningMaterial>('/api/rag/materials/text', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 提交 RAG 检索问答请求。
export function queryRag(payload: RagQueryPayload): Promise<RagQueryResult> {
  return request<RagQueryResult>('/api/rag/query', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 查询最近几次 RAG 询问历史。
export function fetchRagQueryHistory(params: {
  startDate?: string;
  endDate?: string;
  limit?: number;
} = {}): Promise<RagQueryHistory[]> {
  const search = new URLSearchParams();
  if (params.startDate) search.set('startDate', params.startDate);
  if (params.endDate) search.set('endDate', params.endDate);
  if (params.limit) search.set('limit', String(params.limit));
  const query = search.toString();
  return request<RagQueryHistory[]>(`/api/rag/query/history${query ? `?${query}` : ''}`);
}

// 创建 RAG 检索问答任务，前端通过轮询读取实时进度。
export function startRagQueryTask(payload: RagQueryPayload): Promise<RagQueryTask> {
  return request<RagQueryTask>('/api/rag/query/tasks', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 读取 RAG 检索问答任务当前状态。
export function fetchRagQueryTask(taskId: string): Promise<RagQueryTask> {
  return request<RagQueryTask>(`/api/rag/query/tasks/${taskId}`);
}

// 创建并轮询 RAG 检索任务，持续把阶段事件回调给页面。
export async function runRagQueryTask(
  payload: RagQueryPayload,
  onProgress: (events: RagProgress[], task: RagQueryTask) => void,
  options: { pollIntervalMs?: number; signal?: AbortSignal } = {}
): Promise<RagQueryResult> {
  const pollIntervalMs = options.pollIntervalMs ?? 350;
  let task = await startRagQueryTask(payload);
  onProgress(task.progressEvents || [], task);

  while (task.status === 'RUNNING') {
    await wait(pollIntervalMs, options.signal);
    if (options.signal?.aborted) {
      throw new Error('RAG 检索已取消');
    }
    task = await fetchRagQueryTask(task.taskId);
    onProgress(task.progressEvents || [], task);
  }

  if (task.status === 'COMPLETED' && task.result) {
    return task.result;
  }
  throw new Error(task.errorMessage || task.message || 'RAG 检索失败');
}

// 支持 AbortController 取消正在等待的轮询间隔。
function wait(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('RAG 检索已取消'));
      return;
    }
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timer);
      reject(new Error('RAG 检索已取消'));
    }, { once: true });
  });
}

// 上传文件学习资料并触发索引。
export function uploadMaterial(file: File, highPrecision = false): Promise<LearningMaterial> {
  const form = new FormData();
  form.append('file', file);
  form.append('highPrecision', String(highPrecision));
  return request<LearningMaterial>('/api/rag/materials/upload', {
    method: 'POST',
    body: form
  });
}

// 上传一个学习资料分片，全部分片到齐后后端会合并并触发索引。
export function uploadMaterialChunk(payload: {
  chunk: Blob;
  filename: string;
  uploadId?: string;
  chunkIndex: number;
  totalChunks: number;
  totalSize: number;
  highPrecision?: boolean;
}): Promise<MaterialUploadChunk> {
  const form = new FormData();
  form.append('file', payload.chunk, payload.filename);
  form.append('filename', payload.filename);
  if (payload.uploadId) {
    form.append('uploadId', payload.uploadId);
  }
  form.append('chunkIndex', String(payload.chunkIndex));
  form.append('totalChunks', String(payload.totalChunks));
  form.append('totalSize', String(payload.totalSize));
  form.append('highPrecision', String(Boolean(payload.highPrecision)));
  return request<MaterialUploadChunk>('/api/rag/materials/upload/chunk', {
    method: 'POST',
    body: form
  });
}

// 重新读取原始文件并触发索引重建，高精度模式用于低质量资料补跑。
export function reindexMaterial(id: number, highPrecision = false): Promise<LearningMaterial> {
  return request<LearningMaterial>(`/api/rag/materials/${id}/reindex?highPrecision=${highPrecision}`, {
    method: 'POST'
  });
}

// 上传 DOCX 简历模板并解析字段绑定。
export function uploadResumeTemplate(file: File): Promise<ResumeTemplate> {
  const form = new FormData();
  form.append('file', file);
  return request<ResumeTemplate>('/api/rag/resume-templates', {
    method: 'POST',
    body: form
  });
}

// 查询当前用户上传过的 DOCX 简历模板历史。
export function fetchResumeTemplates(limit = 12): Promise<ResumeTemplate[]> {
  return request<ResumeTemplate[]>(`/api/rag/resume-templates?limit=${limit}`);
}

// 查询简历模板字段绑定。
export function fetchResumeTemplate(templateId: string): Promise<ResumeTemplate> {
  return request<ResumeTemplate>(`/api/rag/resume-templates/${templateId}`);
}

// 删除当前用户上传的简历模板及其派生内容。
export function deleteResumeTemplate(templateId: string): Promise<void> {
  return request<void>(`/api/rag/resume-templates/${templateId}`, {
    method: 'DELETE'
  });
}

// 查询或生成 DOCX 图片预览和字段区域标注。
export function fetchResumeTemplatePreview(templateId: string, refresh = false): Promise<ResumeTemplatePreview> {
  return request<ResumeTemplatePreview>(`/api/rag/resume-templates/${templateId}/preview?refresh=${refresh}`);
}

// 保存用户对图片区域的可改写约束。
export function saveResumeTemplateAnnotations(templateId: string, payload: {
  version: number;
  annotations: ResumeTemplateRegionAnnotation[];
}): Promise<ResumeTemplatePreview> {
  return request<ResumeTemplatePreview>(`/api/rag/resume-templates/${templateId}/annotations`, {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 通过 Java 鉴权接口读取预览图片 blob。
export async function fetchResumeTemplatePreviewImage(url: string): Promise<string> {
  const token = getStoredAuthToken();
  const headers = new Headers();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const response = await fetch(url, { headers });
  if (!response.ok) {
    throw new Error(`预览图片读取失败：${response.status}`);
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

// 基于 JD 和 evidence 生成字段级补丁草稿。
export function generateResumePatches(templateId: string, payload: {
  version: number;
  jobDescription: string;
  resumeText?: string;
  resumeMaterialId?: number;
  resumeMaterialTitle?: string;
  topK?: number;
  useConfirmedAnnotations?: boolean;
}): Promise<ResumePatchDraft> {
  return request<ResumePatchDraft>(`/api/rag/resume-templates/${templateId}/patches/generate`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 校验用户选择的字段级补丁。
export function validateResumePatches(templateId: string, payload: {
  version: number;
  patchDraftId: string;
  patches: ResumeContentPatch[];
}): Promise<ResumePatchDraft> {
  return request<ResumePatchDraft>(`/api/rag/resume-templates/${templateId}/patches/validate`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 导出人工确认后的新 DOCX 版本。
export function exportResumeTemplate(templateId: string, payload: {
  version: number;
  patchDraftId: string;
  idempotencyKey: string;
}): Promise<ResumeTemplateExport> {
  return request<ResumeTemplateExport>(`/api/rag/resume-templates/${templateId}/exports`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}
