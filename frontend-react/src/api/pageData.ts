import type {
  DashboardData,
  Result,
  SystemSetting
} from './types';
import { getStoredAuthToken } from './auth';

// 统一处理页面数据接口响应。
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers();
  if (init?.headers) {
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  }
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
    throw new Error(envelope.msg || '请求失败');
  }
  return envelope.data;
}

// 获取工作台聚合数据，可控制近期任务的日期范围和条数。
export function fetchDashboardData(options: { startDate?: string; endDate?: string; recentLimit?: number } = {}): Promise<DashboardData> {
  const params = new URLSearchParams();
  if (options.startDate) {
    params.set('startDate', options.startDate);
  }
  if (options.endDate) {
    params.set('endDate', options.endDate);
  }
  if (options.recentLimit) {
    params.set('recentLimit', String(options.recentLimit));
  }
  const query = params.toString();
  return request<DashboardData>(`/api/page-data/dashboard${query ? `?${query}` : ''}`);
}

// 获取系统设置展示数据。
export function fetchSystemSettings(): Promise<SystemSetting[]> {
  return request<SystemSetting[]>('/api/page-data/settings');
}
