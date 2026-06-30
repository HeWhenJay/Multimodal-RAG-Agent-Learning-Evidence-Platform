import {
  Bell,
  BookOpen,
  Brain,
  ChevronDown,
  Folder,
  FolderPlus,
  GripVertical,
  LayoutDashboard,
  LifeBuoy,
  Loader2,
  LogOut,
  Menu,
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
  createAgentConversationFolder,
  deleteAgentConversationFolder,
  fetchAgentConversationTree,
  moveAgentConversation
} from '../api/agent';
import type { AgentConversationFolder, AgentConversationTree, AgentTask } from '../api/types';
import { MATERIAL_FILE_ACCEPT, useMaterialUpload } from '../hooks/useMaterialUpload';
import { useAuth } from '../stores/auth';

const navItems = [
  { to: '/', label: '\u5de5\u4f5c\u53f0', icon: LayoutDashboard },
  { to: '/materials', label: '\u5b66\u4e60\u8d44\u6599', icon: BookOpen },
  { to: '/agent', label: 'Agent', icon: WandSparkles },
  { to: '/settings', label: '\u7cfb\u7edf\u8bbe\u7f6e', icon: Settings }
];

export function AppLayout() {
  const { user, logout } = useAuth();
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const { uploading, uploadMessage, uploadFile } = useMaterialUpload();
  const displayName = user?.displayName || '\u7ba1\u7406\u5458';
  const accountLabel = user?.email || user?.account || '\u672a\u767b\u5f55';
  const avatarText = displayName.slice(0, 1).toUpperCase();

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
            <h1>{'\u5b66\u8ff9\u667a\u914d'}</h1>
            <p>{'\u591a\u6a21\u6001 RAG \u5e73\u53f0'}</p>
          </div>
        </div>

        <nav className="side-nav" aria-label="\u4e3b\u5bfc\u822a">
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
          <button className="icon-button tiny" onClick={() => void logout()} aria-label="\u9000\u51fa\u767b\u5f55">
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <div className="content-shell">
        <header className="topbar">
          <button className="icon-button compact" aria-label="\u5c55\u5f00\u83dc\u5355">
            <Menu size={19} />
          </button>
          <div className="search-box">
            <Search size={18} />
            <input placeholder="\u641c\u7d22\u8d44\u6599\u3001\u8bc1\u636e\u6216\u4efb\u52a1..." />
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
          <button className="icon-button" aria-label="\u901a\u77e5">
            <Bell size={18} />
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
                onOpenTask={openTask}
                onMoveTask={(task, folderId) => void moveTask(task, folderId)}
              />
              {tree.folders.map((folder) => (
                <ConversationFolderBlock
                  key={folder.id || folder.name}
                  folder={folder}
                  folders={tree.folders}
                  busy={busy}
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
  onDeleteFolder,
  onMoveTask,
  onOpenTask
}: {
  folder: AgentConversationFolder;
  folders: AgentConversationFolder[];
  busy: string;
  onDeleteFolder?: () => void;
  onMoveTask: (task: AgentTask, folderId: string) => void;
  onOpenTask: (task: AgentTask) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <section className="agent-folder-block">
      <div className="agent-folder-head">
        <button type="button" onClick={() => setOpen((value) => !value)}>
          <ChevronDown size={13} />
          <Folder size={14} />
          <span>{folder.name}</span>
          <em>{folder.conversationCount}</em>
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
