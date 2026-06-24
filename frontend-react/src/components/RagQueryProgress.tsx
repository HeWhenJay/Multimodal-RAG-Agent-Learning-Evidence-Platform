import { CheckCircle2, Circle, Loader2, TriangleAlert } from 'lucide-react';
import type { RagProgress } from '../api/types';

interface RagQueryProgressProps {
  events?: RagProgress[];
  running?: boolean;
}

const QUERY_STEPS = [
  { code: 'query.expand', label: 'Multi-Query', fallback: '生成查询变体' },
  { code: 'query.filter', label: '元数据过滤', fallback: '限定当前用户资料范围' },
  { code: 'query.bm25', label: 'BM25 召回', fallback: '关键词召回候选证据' },
  { code: 'query.vector', label: '向量召回', fallback: '语义向量召回候选证据' },
  { code: 'query.fusion', label: 'RAG-Fusion', fallback: 'RRF 融合多路召回结果' },
  { code: 'query.rerank', label: '重排', fallback: '对候选 evidence 重新排序' },
  { code: 'query.guard', label: '回答准入', fallback: '判断 evidence 是否足以支撑回答' },
  { code: 'query.answer', label: '回答生成', fallback: '生成带引用的 RAG 回复' }
];

// 展示一次 RAG 查询从扩展到生成的阶段，避免用户误以为请求卡住。
export function RagQueryProgress({ events = [], running = false }: RagQueryProgressProps) {
  const activeIndex = resolveActiveIndex(events, running);
  const latestEvent = events[events.length - 1];
  const overallPercent = latestEvent?.percent ?? (running ? Math.max(8, activeIndex * 12) : undefined);
  const eventsByStage = groupEventsByStage(events);

  return (
    <div className="rag-query-progress" aria-live="polite">
      <div className="rag-query-progress-head">
        <div>
          <strong>{running ? '正在执行 RAG 检索链路' : 'RAG 检索链路'}</strong>
          <span>{latestEvent?.message || '等待提交问题'}</span>
        </div>
        {typeof overallPercent === 'number' ? <em>{Math.round(overallPercent)}%</em> : null}
      </div>
      <div className="rag-query-progress-bar" aria-hidden="true">
        <span style={{ width: `${Math.min(Math.max(overallPercent ?? 0, 0), 100)}%` }} />
      </div>
      <div className="rag-query-steps">
        {QUERY_STEPS.map((step, index) => {
          const stageEvents = eventsByStage.get(step.code) || [];
          const event = stageEvents[stageEvents.length - 1];
          const state = resolveStepState(event, index, activeIndex, running);
          return (
            <div className={`rag-query-step ${state}`} key={step.code}>
              <span>{renderStepIcon(state)}</span>
              <div>
                <small>{step.label}</small>
                <strong>{event?.message || step.fallback}</strong>
                {formatStepMeta(event) ? <em>{formatStepMeta(event)}</em> : null}
                {stageEvents.length > 0 ? <StepDetails events={stageEvents} /> : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 查询提交后如果还没有真实事件，默认点亮第一步。
function resolveActiveIndex(events: RagProgress[], running: boolean) {
  if (!events.length) {
    return running ? 0 : -1;
  }
  const latestStep = Math.max(...events.map((event) => (event.currentStep || 1) - 1));
  return Math.max(0, Math.min(latestStep, QUERY_STEPS.length - 1));
}

// 按阶段归并事件，BM25 和向量召回会保留每个查询变体的召回详情。
function groupEventsByStage(events: RagProgress[]) {
  const result = new Map<string, RagProgress[]>();
  for (const event of events) {
    const items = result.get(event.stageCode) || [];
    items.push(event);
    result.set(event.stageCode, items);
  }
  return result;
}

// 根据事件状态、当前位置和请求状态推导阶段展示状态。
function resolveStepState(event: RagProgress | undefined, index: number, activeIndex: number, running: boolean) {
  const status = (event?.status || '').toUpperCase();
  if (status === 'FAILED') return 'failed';
  if (status === 'COMPLETED' || (!running && event)) return 'done';
  if (running && index === activeIndex) return 'active';
  if (running && event && index < activeIndex) return 'done';
  return 'pending';
}

// 渲染阶段图标。
function renderStepIcon(state: string) {
  if (state === 'done') return <CheckCircle2 size={16} />;
  if (state === 'failed') return <TriangleAlert size={16} />;
  if (state === 'active') return <Loader2 className="spin" size={16} />;
  return <Circle size={16} />;
}

// 生成阶段指标补充信息。
function formatStepMeta(event: RagProgress | undefined) {
  if (!event) return '';
  const parts = [
    event.currentStep && event.totalSteps ? `步骤 ${event.currentStep}/${event.totalSteps}` : '',
    event.currentChunk && event.totalChunks ? `切块 ${event.currentChunk}/${event.totalChunks}` : '',
    typeof event.percent === 'number' ? `${Math.round(event.percent)}%` : ''
  ].filter(Boolean);
  return parts.join(' · ');
}

// 展示阶段内的真实事件和详情文本，便于定位检索链路每一步做了什么。
function StepDetails({ events }: { events: RagProgress[] }) {
  const details = events
    .map((event) => formatEventDetail(event))
    .filter(Boolean);
  if (!details.length) return null;
  return (
    <ul className="rag-query-step-details">
      {details.map((detail, index) => <li key={`${index}-${detail}`}>{detail}</li>)}
    </ul>
  );
}

// 优先展示 detail；没有 detail 时展示阶段消息，避免运行中看起来没有变化。
function formatEventDetail(event: RagProgress) {
  const detail = event.detail?.trim();
  if (detail) return detail;
  const message = event.message?.trim();
  return message || '';
}
