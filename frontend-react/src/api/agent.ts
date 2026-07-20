import type {
  AgentConversationFolder,
  AgentConversationFolderPayload,
  AgentConversationMovePayload,
  AgentConversationTree,
  AgentMemory,
  AgentMemoryCreatePayload,
  AgentChatMessage,
  AgentMessagePage,
  AgentStreamEvent,
  AgentOperation,
  AgentOperationUndoPayload,
  AgentReviewDecisionPayload,
  AgentTask,
  AgentTaskCreatePayload,
  AgentToolDefinition,
  Result
} from './types';
import { getStoredAuthToken } from './auth';

const jsonHeaders = {
  'Content-Type': 'application/json'
};

// 统一处理 Agent 接口响应和业务错误。
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const token = getStoredAuthToken();
  const headers = new Headers(init?.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    throw new Error(`HTTP 请求失败：${response.status}`);
  }
  const envelope = (await response.json()) as Result<T>;
  if (envelope.code !== 1) {
    throw new Error(envelope.msg || 'Agent 请求失败');
  }
  return envelope.data;
}

// 创建 Agent 任务。
export function createAgentTask(payload: AgentTaskCreatePayload): Promise<AgentTask> {
  return request<AgentTask>('/api/agent/tasks', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 查询当前用户最近的 Agent 会话任务。
export function fetchAgentTasks(limit = 20): Promise<AgentTask[]> {
  return request<AgentTask[]>(`/api/agent/tasks?limit=${limit}`);
}

// 读取 Agent 任务详情，前端通过轮询展示事件回写结果。
export function fetchAgentTask(taskId: string): Promise<AgentTask> {
  return request<AgentTask>(`/api/agent/tasks/${taskId}`);
}

// 查询当前任务的后端持久化聊天消息流。
export function fetchAgentTaskMessages(taskId: string, params: { beforeSequenceNo?: number | null; afterSequenceNo?: number | null; limit?: number } = {}): Promise<AgentMessagePage> {
  const searchParams = new URLSearchParams();
  if (params.beforeSequenceNo !== undefined && params.beforeSequenceNo !== null) searchParams.set('beforeSequenceNo', String(params.beforeSequenceNo));
  if (params.afterSequenceNo !== undefined && params.afterSequenceNo !== null) searchParams.set('afterSequenceNo', String(params.afterSequenceNo));
  if (params.limit) searchParams.set('limit', String(params.limit));
  const suffix = searchParams.toString() ? `?${searchParams.toString()}` : '';
  return request<AgentMessagePage>(`/api/agent/tasks/${taskId}/messages${suffix}`);
}

// 查询侧边栏 Agent 会话树。
export function fetchAgentConversationTree(limitPerFolder = 8): Promise<AgentConversationTree> {
  return request<AgentConversationTree>(`/api/agent/conversations/tree?limitPerFolder=${limitPerFolder}`);
}

// 创建当前用户会话文件夹。
export function createAgentConversationFolder(payload: AgentConversationFolderPayload): Promise<AgentConversationFolder> {
  return request<AgentConversationFolder>('/api/agent/conversation-folders', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 更新当前用户会话文件夹。
export function updateAgentConversationFolder(folderId: string, payload: AgentConversationFolderPayload): Promise<AgentConversationFolder> {
  return request<AgentConversationFolder>(`/api/agent/conversation-folders/${folderId}`, {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 删除当前用户会话文件夹，会话回到未分类。
export function deleteAgentConversationFolder(folderId: string): Promise<void> {
  return request<void>(`/api/agent/conversation-folders/${folderId}`, {
    method: 'DELETE'
  });
}

// 移动会话到指定文件夹，folderId 为空表示未分类。
export function moveAgentConversation(taskId: string, payload: AgentConversationMovePayload): Promise<AgentTask> {
  return request<AgentTask>(`/api/agent/tasks/${taskId}/folder`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 订阅 Agent 任务快照流，后端按任务事件增量推送最新状态。
export function subscribeAgentTask(
  taskId: string,
  handlers: {
    onTask: (task: AgentTask) => void;
    onAgentEvent?: (event: AgentStreamEvent) => void;
    onError?: (error: Event) => void;
    onDone?: (task: AgentTask) => void;
  }
): EventSource | null {
  const token = getStoredAuthToken();
  if (!token) return null;
  const source = new EventSource(`/api/agent/tasks/${taskId}/stream?token=${encodeURIComponent(token)}`);
  source.addEventListener('task', (event) => {
    handlers.onTask(JSON.parse((event as MessageEvent).data) as AgentTask);
  });
  source.addEventListener('agent_event', (event) => {
    handlers.onAgentEvent?.(JSON.parse((event as MessageEvent).data) as AgentStreamEvent);
  });
  source.addEventListener('done', (event) => {
    const task = JSON.parse((event as MessageEvent).data) as AgentTask;
    handlers.onTask(task);
    handlers.onDone?.(task);
    source.close();
  });
  source.onerror = (event) => {
    handlers.onError?.(event);
  };
  return source;
}

// 提交计划或输出审批决策。
export function decideAgentReview(taskId: string, reviewId: string, payload: AgentReviewDecisionPayload): Promise<AgentTask> {
  return request<AgentTask>(`/api/agent/tasks/${taskId}/reviews/${reviewId}/decide`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 撤销当前用户窗口内的 Agent 变更操作。
export function undoAgentOperation(operationId: string, payload: AgentOperationUndoPayload): Promise<AgentOperation> {
  return request<AgentOperation>(`/api/agent/operations/${operationId}/undo`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 获取当前阶段可展示的 Agent 工具能力。
export function fetchAgentTools(): Promise<AgentToolDefinition[]> {
  return request<AgentToolDefinition[]>('/api/agent/tools');
}

// 查询当前用户 Agent 记忆，默认返回未删除的全部状态。
export function fetchAgentMemories(params: {
  status?: string;
  memoryType?: string;
  namespace?: string;
  scopeType?: string;
} = {}): Promise<AgentMemory[]> {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) {
      searchParams.set(key, value);
    }
  });
  const suffix = searchParams.toString() ? `?${searchParams.toString()}` : '';
  return request<AgentMemory[]>(`/api/agent/memories${suffix}`);
}

// 显式创建当前用户授权的 Agent 记忆。
export function createAgentMemory(payload: AgentMemoryCreatePayload): Promise<AgentMemory> {
  return request<AgentMemory>('/api/agent/memories', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

// 二次确认待审记忆，成功后由 Python 后端进入索引流程。
export function confirmAgentMemory(memoryId: string): Promise<AgentMemory> {
  return request<AgentMemory>(`/api/agent/memories/${memoryId}/confirm`, {
    method: 'POST'
  });
}

// 拒绝待审记忆，不进入默认 memoryContext。
export function rejectAgentMemory(memoryId: string): Promise<AgentMemory> {
  return request<AgentMemory>(`/api/agent/memories/${memoryId}/reject`, {
    method: 'POST'
  });
}

// 归档记忆并停用检索索引。
export function archiveAgentMemory(memoryId: string): Promise<AgentMemory> {
  return request<AgentMemory>(`/api/agent/memories/${memoryId}/archive`, {
    method: 'POST'
  });
}

// 删除记忆并擦除正文。
export function deleteAgentMemory(memoryId: string): Promise<AgentMemory> {
  return request<AgentMemory>(`/api/agent/memories/${memoryId}`, {
    method: 'DELETE'
  });
}
