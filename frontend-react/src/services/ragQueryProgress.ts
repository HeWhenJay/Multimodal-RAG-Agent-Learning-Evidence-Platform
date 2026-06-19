import type { RagProgress } from '../api/types';

// 查询失败时补齐失败阶段，避免页面停留在运行中。
export function markRagQueryProgressFailed(events: RagProgress[], message = 'RAG 查询失败，请查看错误提示') {
  if (!events.length) {
    return [{
      stageCode: 'query.expand',
      stageLabel: 'RAG 查询',
      message,
      status: 'FAILED',
      percent: 0
    }];
  }
  return events.map((event, index) => index === events.length - 1
    ? { ...event, status: 'FAILED', message: event.message || message }
    : event);
}
