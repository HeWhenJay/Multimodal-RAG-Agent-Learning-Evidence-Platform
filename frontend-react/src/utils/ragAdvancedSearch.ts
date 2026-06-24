import type { RagQueryPayload } from '../api/types';

export interface RagAdvancedSearchState {
  documentType: string;
  source: string;
  evidenceChannel: string;
  blockType: string;
  sectionKeyword: string;
  topK: number;
  candidateMultiplier: number;
}

export const DEFAULT_RAG_ADVANCED_SEARCH: RagAdvancedSearchState = {
  documentType: '',
  source: '',
  evidenceChannel: '',
  blockType: '',
  sectionKeyword: '',
  topK: 5,
  candidateMultiplier: 4
};

export const DOCUMENT_TYPE_OPTIONS = [
  { value: '', label: '全部资料类型' },
  { value: 'pdf', label: 'PDF' },
  { value: 'pptx', label: 'PPTX' },
  { value: 'markdown', label: 'Markdown' },
  { value: 'srt', label: '字幕 SRT' },
  { value: 'vtt', label: '字幕 VTT' },
  { value: 'mp4', label: '视频 MP4' },
  { value: 'webm', label: '视频 WEBM' }
];

export const SOURCE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'upload', label: '上传资料' },
  { value: 'manual', label: '手动录入' },
  { value: 'unit-test', label: '测试资料' }
];

export const EVIDENCE_CHANNEL_OPTIONS = [
  { value: '', label: '全部证据通道' },
  { value: 'subtitle', label: '字幕 / ASR' },
  { value: 'frame_ocr', label: '关键帧 OCR' },
  { value: 'video_segment_summary', label: '视频片段摘要' },
  { value: 'video_metadata', label: '视频元数据' }
];

export const BLOCK_TYPE_OPTIONS = [
  { value: '', label: '全部块类型' },
  { value: 'text', label: '文本' },
  { value: 'heading', label: '标题' },
  { value: 'table', label: '表格' },
  { value: 'image', label: '图片 / OCR' },
  { value: 'code', label: '代码' },
  { value: 'list', label: '列表' }
];

const FILTER_LABELS: Record<keyof Omit<RagAdvancedSearchState, 'topK' | 'candidateMultiplier'>, string> = {
  documentType: '资料类型',
  source: '来源',
  evidenceChannel: '证据通道',
  blockType: '块类型',
  sectionKeyword: '章节关键词'
};

// 生成 RAG 查询 payload，默认不带业务过滤即可保持原查询效果。
export function buildRagQueryPayload(question: string, state: RagAdvancedSearchState): RagQueryPayload {
  const metadataFilter: Record<string, unknown> = {};
  if (state.documentType) metadataFilter.documentType = state.documentType;
  if (state.source) metadataFilter.source = state.source;
  if (state.evidenceChannel) metadataFilter.evidenceChannel = state.evidenceChannel;
  if (state.blockType) metadataFilter.blockType = state.blockType;
  if (state.sectionKeyword.trim()) metadataFilter.sectionKeyword = state.sectionKeyword.trim();
  return {
    question,
    topK: clampNumber(state.topK, 1, 20),
    candidateMultiplier: clampNumber(state.candidateMultiplier, 2, 10),
    metadataFilter
  };
}

// 生效过滤摘要不展示 userId，只展示固定权限范围和用户选择的业务过滤。
export function formatRagFilterSummary(state: RagAdvancedSearchState) {
  const filters = (Object.keys(FILTER_LABELS) as Array<keyof typeof FILTER_LABELS>)
    .map((key) => {
      const value = key === 'sectionKeyword' ? state[key].trim() : state[key];
      return value ? `${FILTER_LABELS[key]}=${optionLabel(key, value)}` : '';
    })
    .filter(Boolean);
  return `权限范围：个人私有资料；业务过滤：${filters.length ? filters.join('；') : '无'}`;
}

export function clampNumber(value: number, min: number, max: number) {
  if (Number.isNaN(value)) return min;
  return Math.max(min, Math.min(max, Math.round(value)));
}

function optionLabel(key: keyof typeof FILTER_LABELS, value: string) {
  const options = key === 'documentType'
    ? DOCUMENT_TYPE_OPTIONS
    : key === 'source'
      ? SOURCE_OPTIONS
      : key === 'evidenceChannel'
        ? EVIDENCE_CHANNEL_OPTIONS
        : key === 'blockType'
          ? BLOCK_TYPE_OPTIONS
          : [];
  return options.find((item) => item.value === value)?.label || value;
}
