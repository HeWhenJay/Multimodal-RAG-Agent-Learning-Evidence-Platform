import type { LearningMaterial, RagOverview, RagQueryResult, Result } from './types';
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
export function queryRag(payload: {
  question: string;
  topK?: number;
  metadataFilter?: Record<string, unknown>;
}): Promise<RagQueryResult> {
  return request<RagQueryResult>('/api/rag/query', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
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
