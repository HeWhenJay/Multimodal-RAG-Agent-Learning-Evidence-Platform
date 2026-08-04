import {
  Bell,
  BookOpen,
  Brain,
  CalendarClock,
  ChevronDown,
  Folder,
  FolderPlus,
  GripVertical,
  LayoutDashboard,
  LifeBuoy,
  Loader2,
  LogOut,
  Menu,
  Plus,
  Search,
  Settings,
  Trash2,
  Upload,
  UserCircle,
  WandSparkles
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  createAgentConversation,
  createAgentConversationFolder,
  deleteAgentConversationFolder,
  fetchAgentConversationTree,
  moveAgentConversation
} from '../api/agent';
import type { AgentConversationFolder, AgentConversationTree, AgentTask } from '../api/types';
import { REVIEW_OVERVIEW_UPDATED_EVENT, fetchReviewOverview, syncReviewMaterials, type ReviewOverview } from '../api/reviews';
import { MATERIAL_FILE_ACCEPT, MATERIAL_UPLOADED_EVENT, useMaterialUpload } from '../hooks/useMaterialUpload';
import { useAuth } from '../stores/auth';

const navItems = [
  { to: '/', label: '\u5de5\u4f5c\u53f0', icon: LayoutDashboard },
  { to: '/materials', label: '\u5b66\u4e60\u8d44\u6599', icon: BookOpen },
  { to: '/agent', label: 'Agent', icon: WandSparkles },
  { to: '/reviews', label: '\u590d\u4e60\u4e2d\u5fc3', icon: CalendarClock },
  { to: '/settings', label: '\u7cfb\u7edf\u8bbe\u7f6e', icon: Settings }
];

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const { uploading, uploadMessage, uploadFile } = useMaterialUpload();
  const [reviewDueCount, setReviewDueCount] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const displayName = user?.displayName || '\u7ba1\u7406\u5458';
  const accountLabel = user?.email || user?.account || '\u672a\u767b\u5f55';
  const avatarText = displayName.slice(0, 1).toUpperCase();

  // 移动端导航完成后自动收起侧栏，桌面端不影响固定导航。
  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  function openUploadPicker() {
    uploadInputRef.current?.click();
  }

  // 定时读取服务端持久化到期数量，并在已授权且页面隐藏时发送一次浏览器提醒。
  useEffect(() => {
    let active = true;
    let syncInFlight = false;
    const refreshReviewOverview = async () => {
      try {
        const overview = await fetchReviewOverview();
        if (!active) return;
        const actionableDueCount = resolveActionableDueCount(overview);
        setReviewDueCount(actionableDueCount);
        notifyDueReviews(overview, actionableDueCount);
      } catch {
        // 复习服务暂不可用时不影响其他工作台功能。
      }
    };
    const syncAndRefreshReviewOverview = async () => {
      if (syncInFlight) return;
      syncInFlight = true;
      try {
        // 资料上传事件只处理一份新完成入库的资料，概览事件本身不触发模型调用。
        await syncReviewMaterials(1).catch(() => undefined);
        await refreshReviewOverview();
      } finally {
        syncInFlight = false;
      }
    };
    void refreshReviewOverview();
    // 普通轮询只读取持久化概览；资料上传事件才触发增量卡片生成，避免重复调用 LLM。
    const timer = window.setInterval(() => void refreshReviewOverview(), 60_000);
    window.addEventListener(REVIEW_OVERVIEW_UPDATED_EVENT, refreshReviewOverview);
    window.addEventListener(MATERIAL_UPLOADED_EVENT, syncAndRefreshReviewOverview);
    window.addEventListener('review-notification-permission-updated', refreshReviewOverview);
    window.addEventListener('focus', refreshReviewOverview);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener(REVIEW_OVERVIEW_UPDATED_EVENT, refreshReviewOverview);
      window.removeEventListener(MATERIAL_UPLOADED_EVENT, syncAndRefreshReviewOverview);
      window.removeEventListener('review-notification-permission-updated', refreshReviewOverview);
      window.removeEventListener('focus', refreshReviewOverview);
    };
  }, []);

  return (
    <div className={`app-shell${sidebarOpen ? ' sidebar-open' : ''}`}>
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">
            <Brain size={22} />
          </div>
          <div>
            <h1>{'\u5b66\u8ff9\u667a\u914d'}</h1>
            <p>{'\u591a\u6a21\u6001 RAG \u5e73\u53f0'}</p>
          </div>
        </div>

        <nav className="side-nav" aria-label="主导航">
          {navItems.map((item) => (
            item.to === '/agent' ? (
              <AgentConversationNav key={item.to} />
            ) : (
              <NavLink key={item.to} to={item.to} end={item.to === '/'}>
                <item.icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            )
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
          <button className="icon-button compact" aria-label={sidebarOpen ? '收起菜单' : '展开菜单'} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((value) => !value)}>
            <Menu size={19} />
          </button>
          <div className="search-box">
            <Search size={18} />
            <input placeholder="搜索资料、证据或任务..." />
          </div>
          <button className="primary-action" onClick={openUploadPicker} disabled={uploading}>
            {uploading ? <Loader2 className="spin" size={17} /> : <Upload size={17} />}
            <span>{uploading ? '\u4e0a\u4f20\u4e2d' : '\u4e0a\u4f20'}</span>
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
            <span>{'\u5e2e\u52a9'}</span>
          </button>
          <button
            className="icon-button review-bell-button"
            type="button"
            aria-label={reviewDueCount > 0 ? `\u6709 ${reviewDueCount} \u4e2a\u77e5\u8bc6\u70b9\u5f85\u590d\u4e60` : '\u6682\u65e0\u5f85\u590d\u4e60\u77e5\u8bc6\u70b9'}
            title={reviewDueCount > 0 ? `${reviewDueCount} \u4e2a\u77e5\u8bc6\u70b9\u5f85\u590d\u4e60` : '\u590d\u4e60\u4e2d\u5fc3'}
            onClick={() => navigate('/reviews')}
          >
            <Bell size={18} />
            {reviewDueCount > 0 ? <span className="review-bell-badge" aria-hidden="true">{reviewDueCount > 99 ? '99+' : reviewDueCount}</span> : null}
          </button>
          <button className="icon-button" aria-label={`${displayName} \u8d26\u6237`}>
            <UserCircle size={20} />
          </button>
          <button className="ghost-action logout-action" onClick={() => void logout()}>
            <LogOut size={17} />
            <span>{'\u9000\u51fa'}</span>
          </button>
        </header>

        <main className="page-surface">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

const REVIEW_NOTIFICATION_MARKER_KEY = 'learning-evidence.review-notification-marker';

// 每个用户时区的自然日最多通知一次，页面可见时由顶部徽标承担提醒。
function notifyDueReviews(overview: ReviewOverview, actionableDueCount: number) {
  if (!overview.settings.enabled || actionableDueCount <= 0 || !document.hidden) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const reminderDay = resolveReminderDay(overview.settings.timezone, overview.settings.reminderTime);
  if (!reminderDay) return;
  const marker = `${reminderDay}:${overview.settings.reminderTime}`;
  try {
    if (window.localStorage.getItem(REVIEW_NOTIFICATION_MARKER_KEY) === marker) return;
  } catch {
    // 隐私模式可能禁用 localStorage，仍允许本次提醒继续。
  }
  try {
    new Notification('\u5b66\u4e60\u590d\u4e60\u63d0\u9192', {
      body: `\u4f60\u4eca\u5929\u6709 ${actionableDueCount} \u4efd\u8d44\u6599\u5f85\u590d\u4e60\u3002`,
      tag: 'learning-evidence-review'
    });
    try {
      window.localStorage.setItem(REVIEW_NOTIFICATION_MARKER_KEY, marker);
    } catch {
      // 无法记录去重标记时不影响通知本身。
    }
  } catch {
    // 浏览器拒绝构造通知时保留顶部徽标，不影响页面使用。
  }
}

// 顶部徽标只展示今日文档额度内真正可操作的到期资料。
function resolveActionableDueCount(overview: ReviewOverview) {
  if (Number.isFinite(overview.actionableDueCount)) {
    return Math.max(0, overview.actionableDueCount);
  }
  const remainingToday = Math.max(0, overview.settings.dailyLimit - overview.todayReviewedCount);
  return Math.min(Math.max(0, overview.dueCount), remainingToday);
}

// 仅在设置时区的本地时间到达提醒点后返回当天键。
function resolveReminderDay(timezone: string, reminderTime: string, now = new Date()) {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23'
    }).formatToParts(now);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    const currentMinutes = Number(values.hour) * 60 + Number(values.minute);
    const [reminderHour, reminderMinute] = reminderTime.split(':').map(Number);
    if (!Number.isFinite(currentMinutes) || !Number.isFinite(reminderHour) || !Number.isFinite(reminderMinute)) return null;
    if (currentMinutes < reminderHour * 60 + reminderMinute) return null;
    return `${values.year}-${values.month}-${values.day}`;
  } catch {
    return null;
  }
}

function AgentConversationNav() {
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(location.pathname.startsWith('/agent'));
  const [tree, setTree] = useState<AgentConversationTree | null>(null);
  const [folderName, setFolderName] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const isAgentActive = location.pathname.startsWith('/agent');

  useEffect(() => {
    void loadTree();
    function reloadTree() {
      void loadTree();
    }
    window.addEventListener('agent-conversations-updated', reloadTree);
    window.addEventListener('focus', reloadTree);
    return () => {
      window.removeEventListener('agent-conversations-updated', reloadTree);
      window.removeEventListener('focus', reloadTree);
    };
  }, []);

  async function loadTree() {
    try {
      setError('');
      const latest = await fetchAgentConversationTree(8);
      setTree(latest);
    } catch (loadError) {
      setTree(null);
      setError(loadError instanceof Error ? loadError.message : '会话记录读取失败');
    }
  }

  async function createFolder() {
    const name = folderName.trim();
    if (!name) return;
    try {
      setBusy('create-folder');
      await createAgentConversationFolder({ name });
      setFolderName('');
      await loadTree();
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : '文件夹创建失败');
    } finally {
      setBusy('');
    }
  }

  async function createConversation(folderId: string | null) {
    const targetKey = folderId || 'unfiled';
    try {
      setBusy(`create-conversation-${targetKey}`);
      const task = await createAgentConversation({
        folderId,
        title: '新对话',
        taskType: 'pure_read_query',
        input: { workspaceMode: 'read' }
      });
      await loadTree();
      window.dispatchEvent(new Event('agent-conversations-updated'));
      navigate(`/agent?taskId=${encodeURIComponent(task.id)}`);
      setOpen(true);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : '新建对话失败');
    } finally {
      setBusy('');
    }
  }

  async function removeFolder(folder: AgentConversationFolder) {
    if (!folder.id) return;
    if (!window.confirm(`删除“${folder.name}”？其中会话会回到未分类。`)) return;
    try {
      setBusy(`delete-${folder.id}`);
      await deleteAgentConversationFolder(folder.id);
      await loadTree();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '文件夹删除失败');
    } finally {
      setBusy('');
    }
  }

  async function moveTask(task: AgentTask, folderId: string) {
    try {
      setBusy(`move-${task.id}`);
      await moveAgentConversation(task.id, { folderId: folderId || null });
      await loadTree();
      window.dispatchEvent(new CustomEvent('agent-task-folder-updated', { detail: { taskId: task.id, folderId: folderId || null } }));
    } catch (moveError) {
      setError(moveError instanceof Error ? moveError.message : '会话移动失败');
    } finally {
      setBusy('');
    }
  }

  function openTask(task: AgentTask) {
    navigate(`/agent?taskId=${encodeURIComponent(task.id)}`);
    setOpen(true);
  }

  return (
    <div className="agent-side-tree">
      <NavLink to="/agent" className={isAgentActive ? 'active' : undefined}>
        <WandSparkles size={18} />
        <span>Agent</span>
        <button
          className="agent-side-toggle"
          type="button"
          aria-label={open ? '收起 Agent 会话' : '展开 Agent 会话'}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setOpen((value) => !value);
          }}
        >
          <ChevronDown size={15} />
        </button>
      </NavLink>
      {open ? (
        <div className="agent-side-conversations">
          <form
            className="agent-folder-create"
            onSubmit={(event) => {
              event.preventDefault();
              void createFolder();
            }}
          >
            <input value={folderName} onChange={(event) => setFolderName(event.target.value)} placeholder="新建分类" maxLength={80} />
            <button type="submit" aria-label="新建 Agent 分类" disabled={!folderName.trim() || busy === 'create-folder'}>
              {busy === 'create-folder' ? <Loader2 className="spin" size={14} /> : <FolderPlus size={14} />}
            </button>
          </form>
          {error ? <p className="agent-side-error">{error}</p> : null}
          {tree ? (
            <>
              <ConversationFolderBlock
                folder={tree.unfiled}
                folders={tree.folders}
                busy={busy}
                onCreateConversation={(folderId) => void createConversation(folderId)}
                onOpenTask={openTask}
                onMoveTask={(task, folderId) => void moveTask(task, folderId)}
              />
              {tree.folders.map((folder) => (
                <ConversationFolderBlock
                  key={folder.id || folder.name}
                  folder={folder}
                  folders={tree.folders}
                  busy={busy}
                  onCreateConversation={(folderId) => void createConversation(folderId)}
                  onDeleteFolder={() => void removeFolder(folder)}
                  onOpenTask={openTask}
                  onMoveTask={(task, folderId) => void moveTask(task, folderId)}
                />
              ))}
            </>
          ) : (
            <p className="agent-side-empty">暂无会话记录</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

function ConversationFolderBlock({
  folder,
  folders,
  busy,
  onCreateConversation,
  onDeleteFolder,
  onMoveTask,
  onOpenTask
}: {
  folder: AgentConversationFolder;
  folders: AgentConversationFolder[];
  busy: string;
  onCreateConversation: (folderId: string | null) => void;
  onDeleteFolder?: () => void;
  onMoveTask: (task: AgentTask, folderId: string) => void;
  onOpenTask: (task: AgentTask) => void;
}) {
  const [open, setOpen] = useState(true);
  const folderKey = folder.id || 'unfiled';
  return (
    <section className="agent-folder-block">
      <div className="agent-folder-head">
        <button type="button" onClick={() => setOpen((value) => !value)}>
          <ChevronDown size={13} />
          <Folder size={14} />
          <span>{folder.name}</span>
          <em>{folder.conversationCount}</em>
        </button>
        <button
          className="agent-folder-new"
          type="button"
          onClick={() => onCreateConversation(folder.id)}
          disabled={busy === `create-conversation-${folderKey}`}
          aria-label={`在${folder.name}中新建对话`}
        >
          {busy === `create-conversation-${folderKey}` ? <Loader2 className="spin" size={13} /> : <Plus size={13} />}
        </button>
        {folder.id ? (
          <button className="agent-folder-delete" type="button" onClick={onDeleteFolder} disabled={busy === `delete-${folder.id}`} aria-label={`删除 ${folder.name}`}>
            {busy === `delete-${folder.id}` ? <Loader2 className="spin" size={13} /> : <Trash2 size={13} />}
          </button>
        ) : null}
      </div>
      {open ? (
        <div className="agent-task-tree-list">
          {folder.conversations.length ? folder.conversations.map((task) => (
            <div className="agent-task-tree-row" key={task.id}>
              <button type="button" onClick={() => onOpenTask(task)} title={task.title}>
                <GripVertical size={12} />
                <span>{task.title}</span>
              </button>
              <select
                value={task.folderId || ''}
                aria-label={`移动 ${task.title}`}
                disabled={busy === `move-${task.id}`}
                onChange={(event) => onMoveTask(task, event.target.value)}
              >
                <option value="">未分类</option>
                {folders.map((target) => (
                  <option value={target.id || ''} key={target.id || target.name}>{target.name}</option>
                ))}
              </select>
            </div>
          )) : <p className="agent-side-empty">暂无会话</p>}
        </div>
      ) : null}
    </section>
  );
}
