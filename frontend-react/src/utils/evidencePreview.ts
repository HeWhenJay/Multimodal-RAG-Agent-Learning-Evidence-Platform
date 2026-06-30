import type { RagEvidence } from '../api/types';

const PREVIEWABLE_TYPES = new Set(['markdown', 'md', 'txt', 'text', 'srt', 'vtt']);

interface PreviewLinkEntry {
  evidence: RagEvidence;
  preview: string;
  sources: string[];
}

// 根据 evidence 构造应用内资料预览链接，避免直接访问 OSS 触发下载。
export function buildMaterialPreviewLink(evidence: RagEvidence) {
  if (!isPreviewableEvidence(evidence)) {
    return '';
  }
  const materialId = extractMaterialId(evidence.documentId || evidence.evidenceId);
  if (!materialId) {
    return '';
  }
  const source = cleanValue(evidence.sourcePath) || cleanValue(evidence.source);
  if (!source || source.toLowerCase() === 'manual') {
    return '';
  }
  const anchor = extractEvidenceAnchor(evidence.sectionTitle || evidence.sectionName) || extractSourceHash(source);
  const params = new URLSearchParams();
  if (source) params.set('source', source);
  if (anchor) params.set('anchor', anchor);
  const query = params.toString();
  return `/preview/material/${materialId}${query ? `?${query}` : ''}`;
}

// 将回答正文中的原始资料 URL 改写到应用内预览页。
export function buildPreviewHrefRewriter(evidences: RagEvidence[]) {
  const entries = evidences
    .map(buildPreviewLinkEntry)
    .filter((entry): entry is PreviewLinkEntry => Boolean(entry?.preview && entry.sources.length));

  return (href: string, contextText = '') => {
    const normalizedHref = normalizeComparableSource(href);
    if (!normalizedHref) {
      return '';
    }
    const matches = entries.filter((entry) => entry.sources.includes(normalizedHref));
    const matched = selectContextualEntry(matches, contextText);
    if (!matched) {
      return '';
    }
    const hrefHash = extractSourceHash(href);
    if (!hrefHash) {
      return matched.preview;
    }
    const url = new URL(matched.preview, window.location.origin);
    url.searchParams.set('anchor', hrefHash);
    return `${url.pathname}${url.search}`;
  };
}

// 生成用于链接改写的候选来源。
function buildPreviewLinkEntry(evidence: RagEvidence): PreviewLinkEntry | null {
  const preview = buildMaterialPreviewLink(evidence);
  if (!preview) {
    return null;
  }
  return {
    evidence,
    preview,
    sources: collectComparableSources(evidence)
  };
}

function collectComparableSources(evidence: RagEvidence) {
  const metadata = evidence.metadata || {};
  const metadataSources = ['sourcePath', 'playbackUrl', 'videoUrl', 'mediaUrl', 'sourceVideoUrl']
    .map((key) => metadata[key])
    .filter((value): value is string => typeof value === 'string');
  return Array.from(new Set([
    evidence.sourcePath,
    evidence.source,
    evidence.playbackUrl,
    ...metadataSources
  ].map(normalizeComparableSource).filter(Boolean)));
}

// 同一个视频文件会出现在多条 evidence 中，优先按上下文里的 evidenceId 或时间段选中正确片段。
function selectContextualEntry(entries: PreviewLinkEntry[], contextText: string) {
  if (!entries.length) {
    return null;
  }
  const evidenceId = extractContextEvidenceId(contextText);
  if (evidenceId) {
    const matchedById = entries.find((entry) => cleanValue(entry.evidence.evidenceId).toLowerCase() === evidenceId);
    if (matchedById) {
      return matchedById;
    }
  }
  const timeRange = extractContextTimeRange(contextText);
  if (timeRange?.startTime) {
    const matchedByTime = entries.find((entry) => timeMatches(entry.evidence.startTime, timeRange.startTime)
      && (!timeRange.endTime || !entry.evidence.endTime || timeMatches(entry.evidence.endTime, timeRange.endTime)));
    if (matchedByTime) {
      return matchedByTime;
    }
  }
  return entries[0];
}

function extractContextEvidenceId(value: string) {
  const match = /(material-\d+(?:-[a-z0-9_-]+)*)/i.exec(value || '');
  return match?.[1]?.toLowerCase() || '';
}

function extractContextTimeRange(value: string) {
  const timestamp = '(\\d{1,2}:\\d{2}(?::\\d{2})?(?:[,.]\\d+)?)';
  const explicit = new RegExp(`时间\\s*=\\s*${timestamp}\\s*[-–—~至]\\s*${timestamp}`, 'i').exec(value || '');
  const generic = explicit || new RegExp(`${timestamp}\\s*[-–—~至]\\s*${timestamp}`, 'i').exec(value || '');
  if (!generic) {
    return null;
  }
  return { startTime: generic[1], endTime: generic[2] };
}

function timeMatches(left?: string | null, right?: string | null) {
  const leftSeconds = timestampToSeconds(left);
  const rightSeconds = timestampToSeconds(right);
  return Number.isFinite(leftSeconds) && Number.isFinite(rightSeconds) && Math.abs(leftSeconds - rightSeconds) < 0.5;
}

function timestampToSeconds(value?: string | null) {
  const normalized = cleanValue(value).replace(',', '.');
  if (!normalized) return Number.NaN;
  const parts = normalized.split(':');
  if (parts.length === 1) return Number.parseFloat(parts[0]);
  if (parts.length === 2) {
    return (Number.parseInt(parts[0], 10) || 0) * 60 + (Number.parseFloat(parts[1]) || 0);
  }
  const [hoursText, minutesText, secondsText] = parts.slice(-3);
  return (Number.parseInt(hoursText, 10) || 0) * 3600
    + (Number.parseInt(minutesText, 10) || 0) * 60
    + (Number.parseFloat(secondsText) || 0);
}

// 判断 evidence 是否可用文本预览页展示。
export function isPreviewableEvidence(evidence: RagEvidence) {
  const documentType = cleanValue(evidence.documentType).toLowerCase();
  return PREVIEWABLE_TYPES.has(documentType);
}

// 从 documentId 或 evidenceId 中提取 material 数字 ID。
export function extractMaterialId(value?: string | null) {
  const match = /(?:^|[^a-z0-9])material-(\d+)(?:\D|$)/i.exec(value || '');
  return match?.[1] || '';
}

// 从 Markdown 目录链接或当前应用 hash 链接中提取章节锚点。
export function extractEvidenceAnchor(value?: string | null) {
  const text = (value || '').trim();
  const markdownLink = /\[[^\]]+]\(([^)]+)\)/.exec(text);
  const href = markdownLink?.[1]?.trim().replace(/^<|>$/g, '') || '';
  if (href.startsWith('#')) return href.slice(1);
  return extractSourceHash(href);
}

// 去掉来源 URL 的 hash，便于与后端返回 evidence sourcePath 比较。
export function normalizeComparableSource(value?: string | null) {
  const source = cleanValue(value);
  if (!/^https?:\/\//i.test(source) && !source.startsWith('/')) {
    return '';
  }
  return safeDecodeURIComponent(source.split('#', 1)[0]);
}

// 提取来源 URL 上的 fragment。
export function extractSourceHash(value?: string | null) {
  const source = cleanValue(value);
  const hashIndex = source.indexOf('#');
  return hashIndex >= 0 ? source.slice(hashIndex + 1) : '';
}

function cleanValue(value?: string | null) {
  return (value || '').trim().replace(/^<|>$/g, '');
}

function safeDecodeURIComponent(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
