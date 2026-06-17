import type {
  DashboardData,
  JdAnalysis,
  JdAnalysisRequest,
  Result,
  ResumeEvidenceAlignment,
  SystemSetting,
  VideoSlice
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

// 获取工作台聚合数据。
export function fetchDashboardData(): Promise<DashboardData> {
  return request<DashboardData>('/api/page-data/dashboard');
}

// 获取最近一次 JD 分析数据。
export function fetchJdAnalysis(): Promise<JdAnalysis | null> {
  return request<JdAnalysis | null>('/api/page-data/jd-analysis');
}

// 提交 JD 和简历文本，运行 RAG 证据适配分析。
export function analyzeJd(payload: JdAnalysisRequest): Promise<JdAnalysis> {
  return request<JdAnalysis>('/api/page-data/jd-analysis/analyze', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

// 获取简历证据对齐数据。
export function fetchResumeAlignments(): Promise<ResumeEvidenceAlignment[]> {
  return request<ResumeEvidenceAlignment[]>('/api/page-data/resume-adaptation');
}

// 获取视频切片数据。
export function fetchVideoSlices(): Promise<VideoSlice[]> {
  return request<VideoSlice[]>('/api/page-data/video-review');
}

// 获取系统设置展示数据。
export function fetchSystemSettings(): Promise<SystemSetting[]> {
  return request<SystemSetting[]>('/api/page-data/settings');
}
