import {
  Brain,
  CheckCircle2,
  Eye,
  EyeOff,
  LockKeyhole,
  Mail,
  ShieldCheck,
  Sparkles
} from 'lucide-react';
import { Suspense, lazy, useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate, type Location } from 'react-router-dom';
import { useAuth } from '../stores/auth';

interface LoginLocationState {
  from?: Location;
}

const LoginRagScene = lazy(() =>
  import('../components/LoginRagScene').then((module) => ({ default: module.LoginRagScene }))
);

export function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { isAuthenticated, login } = useAuth();
  const [account, setAccount] = useState('admin@evidence.ai');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  const state = location.state as LoginLocationState | null;
  const destination = state?.from
    ? `${state.from.pathname}${state.from.search}${state.from.hash}`
    : '/';

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');

    try {
      login({ account, password, remember });
      navigate(destination, { replace: true });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '登录失败，请稍后重试');
    }
  }

  return (
    <main className="login-page">
      <section className="login-form-column" aria-label="登录表单">
        <div className="login-brand">
          <div className="brand-mark">
            <Brain size={22} />
          </div>
          <div>
            <h1>学迹智配</h1>
            <p>多模态 RAG 证据平台</p>
          </div>
        </div>

        <form className="login-panel" onSubmit={handleSubmit}>
          <div className="login-panel-heading">
            <span><ShieldCheck size={17} />受保护工作区</span>
            <h2>登录后台</h2>
            <p>进入资料索引、知识检索与岗位适配工作台。</p>
          </div>

          <label className="field-label" htmlFor="login-account">账号</label>
          <div className="login-input">
            <Mail size={18} />
            <input
              id="login-account"
              value={account}
              onChange={(event) => setAccount(event.target.value)}
              autoComplete="username"
              placeholder="admin@evidence.ai"
            />
          </div>

          <label className="field-label" htmlFor="login-password">密码</label>
          <div className="login-input">
            <LockKeyhole size={18} />
            <input
              id="login-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              type={showPassword ? 'text' : 'password'}
              placeholder="输入本地登录密码"
            />
            <button
              type="button"
              className="icon-button tiny"
              aria-label={showPassword ? '隐藏密码' : '显示密码'}
              onClick={() => setShowPassword((current) => !current)}
            >
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>

          <div className="login-options">
            <label>
              <input
                type="checkbox"
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
              />
              记住登录状态
            </label>
            <span>本地会话</span>
          </div>

          {error ? <p className="form-message danger">{error}</p> : null}

          <button className="full-action login-submit" type="submit">
            <Sparkles size={17} />
            登录系统
          </button>
        </form>
      </section>

      <section className="login-visual" aria-label="RAG 运行态">
        <div className="login-scene">
          <Suspense fallback={<div className="login-scene-fallback">RAG</div>}>
            <LoginRagScene />
          </Suspense>
        </div>
        <div className="login-visual-copy">
          <span className="status-pill indexed">
            <CheckCircle2 size={15} />
            RAG 闭环
          </span>
          <h2>证据、知识与岗位要求在同一处对齐</h2>
          <div className="login-signal-grid">
            <span>MinerU</span>
            <span>递归切块</span>
            <span>混合检索</span>
            <span>证据引用</span>
          </div>
        </div>
      </section>
    </main>
  );
}
