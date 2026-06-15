import {
  Bell,
  BookOpen,
  Brain,
  Database,
  FileSearch,
  LayoutDashboard,
  LifeBuoy,
  Menu,
  Search,
  Settings,
  Upload,
  UserCircle,
  Video,
  WandSparkles
} from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/materials', label: 'Learning Materials', icon: BookOpen },
  { to: '/knowledge', label: 'Knowledge Base', icon: Database },
  { to: '/videos', label: 'Video Review', icon: Video },
  { to: '/jd-analysis', label: 'JD Analysis', icon: FileSearch },
  { to: '/resume', label: 'Resume Adaptation', icon: UserCircle },
  { to: '/agent-tasks', label: 'Agent Tasks', icon: WandSparkles },
  { to: '/settings', label: 'System Settings', icon: Settings }
];

export function AppLayout() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">
            <Brain size={22} />
          </div>
          <div>
            <h1>Evidence Platform</h1>
            <p>Multimodal RAG Agent</p>
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
          <div className="avatar">U</div>
          <div>
            <strong>Admin User</strong>
            <span>admin@evidence.ai</span>
          </div>
        </div>
      </aside>

      <div className="content-shell">
        <header className="topbar">
          <button className="icon-button compact" aria-label="展开菜单">
            <Menu size={19} />
          </button>
          <div className="search-box">
            <Search size={18} />
            <input placeholder="Search knowledge base, candidates, or tasks..." />
          </div>
          <button className="primary-action">
            <Upload size={17} />
            <span>Upload</span>
          </button>
          <button className="ghost-action">
            <LifeBuoy size={17} />
            <span>Help</span>
          </button>
          <button className="icon-button" aria-label="通知">
            <Bell size={18} />
          </button>
          <button className="icon-button" aria-label="账户">
            <UserCircle size={20} />
          </button>
        </header>

        <main className="page-surface">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

