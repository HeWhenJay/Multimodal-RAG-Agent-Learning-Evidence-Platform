import { getStoredAuthToken } from './auth';
import type { Result } from './types';

export interface DshLocalSyncStatus {
  configured: boolean;
  readable: boolean;
  documentCount: number;
  syncedDocumentCount: number;
  pendingDocumentCount: number;
  lastSyncedAt?: string | null;
  message: string;
}

export interface DshLocalSyncItem {
  documentId: string;
  materialId?: number | null;
  title: string;
  action: 'CREATED' | 'UPDATED' | 'SKIPPED' | 'FAILED' | string;
  status: string;
  message?: string | null;
}

export interface DshLocalSyncResult {
  scannedCount: number;
  createdCount: number;
  updatedCount: number;
  skippedCount: number;
  failedCount: number;
  items: DshLocalSyncItem[];
}

// 同步接口只携带当前登录令牌，不向服务端提交用户 ID 或本地库路径。
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getStoredAuthToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    throw new Error(`HTTP 请求失败：${response.status}`);
  }
  const envelope = (await response.json()) as Result<T>;
  if (envelope.code !== 1) {
    throw new Error(envelope.msg || 'DSH 本地知识库请求失败');
  }
  return envelope.data;
}

// 查询服务端固定本地库的可读性和当前用户同步统计。
export function fetchDshLocalSyncStatus(): Promise<DshLocalSyncStatus> {
  return request<DshLocalSyncStatus>('/api/dsh-local-sync/status');
}

// 主动同步全部变化资料，请求无 body，范围完全由服务端决定。
export function syncDshLocalKnowledge(): Promise<DshLocalSyncResult> {
  return request<DshLocalSyncResult>('/api/dsh-local-sync/sync', { method: 'POST' });
}
