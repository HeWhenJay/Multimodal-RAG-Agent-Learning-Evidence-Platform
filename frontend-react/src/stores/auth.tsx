import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode
} from 'react';

const LOCAL_AUTH_KEY = 'learning-evidence.auth.local';
const SESSION_AUTH_KEY = 'learning-evidence.auth.session';

export interface AuthUser {
  account: string;
  displayName: string;
  email: string;
  role: string;
  loginAt: string;
}

interface LoginPayload {
  account: string;
  password: string;
  remember: boolean;
}

interface AuthContextValue {
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (payload: LoginPayload) => AuthUser;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readStoredUser(): AuthUser | null {
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
    return JSON.parse(raw) as AuthUser;
  } catch {
    window.localStorage.removeItem(LOCAL_AUTH_KEY);
    window.sessionStorage.removeItem(SESSION_AUTH_KEY);
    return null;
  }
}

function buildUser(account: string): AuthUser {
  const normalized = account.trim();
  const name = normalized.includes('@') ? normalized.split('@')[0] : normalized;

  return {
    account: normalized,
    displayName: name || '管理员',
    email: normalized.includes('@') ? normalized : `${normalized}@evidence.local`,
    role: normalized.toLowerCase().includes('admin') ? '管理员' : '学习证据维护员',
    loginAt: new Date().toISOString()
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => readStoredUser());

  const login = useCallback((payload: LoginPayload) => {
    if (!payload.account.trim() || !payload.password) {
      throw new Error('请输入账号和密码');
    }

    const nextUser = buildUser(payload.account);
    setUser(nextUser);

    window.localStorage.removeItem(LOCAL_AUTH_KEY);
    window.sessionStorage.removeItem(SESSION_AUTH_KEY);

    const storage = payload.remember ? window.localStorage : window.sessionStorage;
    storage.setItem(payload.remember ? LOCAL_AUTH_KEY : SESSION_AUTH_KEY, JSON.stringify(nextUser));

    return nextUser;
  }, []);

  const logout = useCallback(() => {
    window.localStorage.removeItem(LOCAL_AUTH_KEY);
    window.sessionStorage.removeItem(SESSION_AUTH_KEY);
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: Boolean(user),
      login,
      logout
    }),
    [login, logout, user]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
