import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode
} from 'react';
import { login as loginRequest, logout as logoutRequest } from '../api/auth';
import type { AuthUser } from '../api/types';

const LOCAL_AUTH_KEY = 'learning-evidence.auth.local';
const SESSION_AUTH_KEY = 'learning-evidence.auth.session';

interface LoginPayload {
  account: string;
  password: string;
  remember: boolean;
}

interface StoredAuthState {
  token: string;
  user: AuthUser;
}

interface AuthContextValue {
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (payload: LoginPayload) => Promise<AuthUser>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// 从 localStorage 或 sessionStorage 读取本地登录状态。
function readStoredState(): StoredAuthState | null {
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
    return JSON.parse(raw) as StoredAuthState;
  } catch {
    window.localStorage.removeItem(LOCAL_AUTH_KEY);
    window.sessionStorage.removeItem(SESSION_AUTH_KEY);
    return null;
  }
}

// 按“记住登录状态”选项持久化会话。
function persistStoredState(state: StoredAuthState, remember: boolean) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.removeItem(LOCAL_AUTH_KEY);
  window.sessionStorage.removeItem(SESSION_AUTH_KEY);

  const storage = remember ? window.localStorage : window.sessionStorage;
  storage.setItem(remember ? LOCAL_AUTH_KEY : SESSION_AUTH_KEY, JSON.stringify(state));
}

// 清理本地保存的登录状态。
function clearStoredState() {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.removeItem(LOCAL_AUTH_KEY);
  window.sessionStorage.removeItem(SESSION_AUTH_KEY);
}

// 提供全局认证状态和登录/退出方法。
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<StoredAuthState | null>(() => readStoredState());

  // 登录成功后同时更新 React 状态和本地存储。
  const login = useCallback(async (payload: LoginPayload) => {
    if (!payload.account.trim() || !payload.password) {
      throw new Error('请输入账号和密码');
    }

    const result = await loginRequest(payload);
    const nextSession: StoredAuthState = {
      token: result.token,
      user: result.user
    };

    setSession(nextSession);
    persistStoredState(nextSession, payload.remember);
    return result.user;
  }, []);

  // 退出登录优先清理本地状态，服务端会话失败不阻塞前端退出。
  const logout = useCallback(async () => {
    const token = session?.token;
    if (token) {
      try {
        await logoutRequest(token);
      } catch {
        // 清理本地状态优先，服务端 session 失效可由过期回收兜底。
      }
    }

    clearStoredState();
    setSession(null);
  }, [session?.token]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user || null,
      isAuthenticated: Boolean(session?.user),
      login,
      logout
    }),
    [login, logout, session]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// 读取认证上下文，必须在 AuthProvider 内使用。
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth 必须在 AuthProvider 内使用');
  }
  return context;
}
