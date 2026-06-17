import type { AuthLoginResult, AuthUser, Result } from './types';

const jsonHeaders = {
  'Content-Type': 'application/json'
};

const LOCAL_AUTH_KEY = 'learning-evidence.auth.local';
const SESSION_AUTH_KEY = 'learning-evidence.auth.session';

// 从本地登录状态中读取当前 token，供业务 API 自动携带 Authorization。
export function getStoredAuthToken(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const raw =
    window.localStorage.getItem(LOCAL_AUTH_KEY) ||
    window.sessionStorage.getItem(SESSION_AUTH_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as { token?: string };
    return parsed.token || null;
  } catch {
    return null;
  }
}

// 统一处理认证接口响应和业务错误。
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`HTTP 请求失败：${response.status}`);
  }
  const envelope = (await response.json()) as Result<T>;
  if (envelope.code !== 1) {
    throw new Error(envelope.msg || '请求失败');
  }
  return envelope.data;
}

// 调用账号密码登录接口。
export function login(payload: {
  account: string;
  password: string;
  remember: boolean;
}): Promise<AuthLoginResult> {
  return request<AuthLoginResult>('/api/auth/login', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 调用退出登录接口。
export function logout(token: string): Promise<void> {
  return request<void>('/api/auth/logout', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`
    }
  });
}

// 查询当前登录用户信息。
export function fetchCurrentUser(token: string): Promise<AuthUser> {
  return request<AuthUser>('/api/auth/me', {
    headers: {
      Authorization: `Bearer ${token}`
    }
  });
}
