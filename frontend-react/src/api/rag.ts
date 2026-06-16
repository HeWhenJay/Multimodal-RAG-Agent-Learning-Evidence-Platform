import type { LearningMaterial, RagOverview, RagQueryResult, Result } from './types';

const jsonHeaders = {
  'Content-Type': 'application/json'
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const envelope = (await response.json()) as Result<T>;
  if (envelope.code !== 1) {
    throw new Error(envelope.msg || '请求失败');
  }
  return envelope.data;
}

export function fetchOverview(): Promise<RagOverview> {
  return request<RagOverview>('/api/rag/overview');
}

export function fetchMaterials(): Promise<LearningMaterial[]> {
  return request<LearningMaterial[]>('/api/rag/materials');
}

export function fetchMaterial(id: number): Promise<LearningMaterial> {
  return request<LearningMaterial>(`/api/rag/materials/${id}`);
}

export function fetchMaterialEvidences(id: number, limit = 20): Promise<RagQueryResult['evidences']> {
  return request<RagQueryResult['evidences']>(`/api/rag/materials/${id}/evidences?limit=${limit}`);
}

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

export function uploadMaterial(file: File, highPrecision = false): Promise<LearningMaterial> {
  const form = new FormData();
  form.append('file', file);
  form.append('highPrecision', String(highPrecision));
  return request<LearningMaterial>('/api/rag/materials/upload', {
    method: 'POST',
    body: form
  });
}
