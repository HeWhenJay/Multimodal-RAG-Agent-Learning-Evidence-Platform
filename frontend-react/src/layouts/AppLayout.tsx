import {
  Bell,
  BookOpen,
  Brain,
  Database,
  FileText,
  FileSearch,
  LayoutDashboard,
  LifeBuoy,
  Loader2,
  LogOut,
  Menu,
  Search,
  Settings,
  Upload,
  UserCircle,
  Video,
  WandSparkles
} from 'lucide-react';
import { useRef } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { MATERIAL_FILE_ACCEPT, useMaterialUpload } from '../hooks/useMaterialUpload';
import { useAuth } from '../stores/auth';

const navItems = [
  { to: '/', label: '工作台', icon: LayoutDashboard },
  { to: '/materials', label: '学习资料', icon: BookOpen },
  { to: '/knowledge', label: '知识库', icon: Database },
  { to: '/videos', label: '视频复习', icon: Video },
  { to: '/jd-analysis', label: 'JD 分析', icon: FileSearch },
  { to: '/resume', label: '简历适配', icon: UserCircle },
  { to: '/resume-template', label: '模板补丁', icon: FileText },
  { to: '/agent-tasks', label: 'Agent 任务', icon: WandSparkles },
  { to: '/settings', label: '系统设置', icon: Settings }
];

// 应用主布局负责导航、顶部栏和登录用户入口。
export function AppLayout() {
  const { user, logout } = useAuth();
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const { uploading, uploadMessage, uploadFile } = useMaterialUpload();
  const displayName = user?.displayName || '管理员';
  const accountLabel = user?.email || user?.account || '未登录';
  const avatarText = displayName.slice(0, 1).toUpperCase();

  // 打开顶部栏隐藏文件选择器。
  function openUploadPicker() {
    uploadInputRef.current?.click();
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">
            <Brain size={22} />
          </div>
          <div>
            <h1>学迹智配</h1>
            <p>多模态 RAG 平台</p>
          </div>
        </div>

        <nav className="side-nav" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'}>
              <item.icon size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="user-strip">
          <div className="avatar">{avatarText}</div>
          <div className="user-strip-copy">
            <strong>{displayName}</strong>
            <span>{accountLabel}</span>
          </div>
          <button className="icon-button tiny" onClick={() => void logout()} aria-label="退出登录">
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <div className="content-shell">
        <header className="topbar">
          <button className="icon-button compact" aria-label="展开菜单">
            <Menu size={19} />
          </button>
          <div className="search-box">
            <Search size={18} />
            <input placeholder="搜索知识库、岗位证据或任务..." />
          </div>
          <button className="primary-action" onClick={openUploadPicker} disabled={uploading}>
            {uploading ? <Loader2 className="spin" size={17} /> : <Upload size={17} />}
            <span>{uploading ? '上传中' : '上传'}</span>
          </button>
          <input
            ref={uploadInputRef}
            className="visually-hidden-file"
            type="file"
            accept={MATERIAL_FILE_ACCEPT}
            disabled={uploading}
            onChange={(event) => {
              const file = event.target.files?.[0] || null;
              event.target.value = '';
              void uploadFile(file).catch(() => undefined);
            }}
          />
          {uploadMessage ? <span className="topbar-upload-status" aria-live="polite">{uploadMessage}</span> : null}
          <button className="ghost-action">
            <LifeBuoy size={17} />
            <span>帮助</span>
          </button>
          <button className="icon-button" aria-label="通知">
            <Bell size={18} />
          </button>
          <button className="icon-button" aria-label={`${displayName} 账户`}>
            <UserCircle size={20} />
          </button>
          <button className="ghost-action logout-action" onClick={() => void logout()}>
            <LogOut size={17} />
            <span>退出</span>
          </button>
        </header>

        <main className="page-surface">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
