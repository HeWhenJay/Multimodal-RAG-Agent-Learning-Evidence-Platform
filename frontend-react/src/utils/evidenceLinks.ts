import type { RagEvidence } from '../api/types';
import { buildMaterialPreviewLink, extractSourceHash, normalizeComparableSource } from './evidencePreview';
import { browserHttpSource } from './sourceSafety';

const VIDEO_PAGE_PATH = '/videos';
const VIDEO_EXTENSION_PATTERN = /\.(mp4|mov|m4v|webm|mkv|avi)(?:[?#]|$)/i;
const SOURCE_KEYS = ['sourcePath', 'playbackUrl', 'videoUrl', 'mediaUrl', 'sourceVideoUrl'];

interface EvidenceLinkEntry {
  evidence: RagEvidence;
  href: string;
  sources: string[];
}

// 根据 evidence 类型构造新标签打开地址：视频走播放器，文本走应用内预览。
export function buildEvidenceOpenHref(evidence: RagEvidence) {
  return buildVideoEvidenceLink(evidence) || buildMaterialPreviewLink(evidence) || buildDirectSourceLink(evidence);
}

// 将回答 Markdown 中的原始来源链接改写成应用内 evidence 预览/播放链接。
export function buildEvidenceHrefRewriter(evidences: RagEvidence[]) {
  const entries = evidences
    .map((evidence) => {
      const href = buildEvidenceOpenHref(evidence);
      if (!href) return null;
      return { evidence, href, sources: collectComparableSources(evidence) };
    })
    .filter((entry): entry is EvidenceLinkEntry => Boolean(entry?.href && entry.sources.length));

  return (href: string, contextText = '') => {
    const normalizedHref = normalizeComparableSource(href);
    if (!normalizedHref) return '';
    const matches = entries.filter((entry) => entry.sources.includes(normalizedHref));
    const matched = selectContextualEntry(matches, contextText);
    if (!matched) return '';
    if (matched.href.startsWith(VIDEO_PAGE_PATH)) return matched.href;
    const hrefHash = extractSourceHash(href);
    if (!hrefHash) return matched.href;
    const url = new URL(matched.href, window.location.origin);
    url.searchParams.set('anchor', hrefHash);
    return `${url.pathname}${url.search}`;
  };
}

// 根据 RAG evidence 字段构造内部视频播放定位地址。
export function buildVideoEvidenceLink(evidence: RagEvidence) {
  const startTime = cleanValue(evidence.startTime);
  if (!startTime) return '';

  const metadata = evidence.metadata || {};
  const platformPage = firstBilibiliVideoPageUrl(
    cleanValue(evidence.sourcePath),
    cleanValue(evidence.source),
    cleanValue(evidence.playbackUrl),
    metadataText(metadata.sourcePath),
    metadataText(metadata.playbackUrl),
  );
  if (platformPage) return buildBilibiliTimestampLink(platformPage, startTime);

  const params = new URLSearchParams();
  const playbackUrl = cleanValue(evidence.playbackUrl);
  const internalParams = playbackUrl ? parseInternalVideoPageParams(playbackUrl) : null;
  if (internalParams) {
    ['documentId', 'title', 'startTime', 'endTime'].forEach((key) => {
      const value = cleanValue(internalParams.get(key));
      if (value) params.set(key, value);
    });
    ['sourcePath', 'videoUrl', 'playbackUrl', 'source'].forEach((key) => {
      const value = browserHttpSource(internalParams.get(key));
      if (value) params.set(key, key === 'videoUrl' || key === 'playbackUrl' ? stripFragment(value) : value);
    });
  } else if (browserHttpSource(playbackUrl)) {
    params.set('videoUrl', stripFragment(playbackUrl));
    params.set('playbackUrl', stripFragment(playbackUrl));
  }

  const title = cleanValue(evidence.documentTitle) || cleanValue(evidence.title);
  const documentId = cleanValue(evidence.documentId) || cleanValue(params.get('documentId'));
  const sourcePath = browserHttpSource(cleanValue(evidence.sourcePath) || metadataText(metadata.sourcePath));
  const directVideoUrl = firstConservativeVideoUrl(
    cleanValue(params.get('videoUrl')),
    sourcePath,
    cleanValue(evidence.source),
    metadataText(metadata.videoUrl),
    metadataText(metadata.mediaUrl),
    metadataText(metadata.sourceVideoUrl),
    metadataText(metadata.playbackUrl),
  );

  if (documentId) params.set('documentId', documentId);
  if (title) params.set('title', title);
  params.set('startTime', startTime);
  setOptionalParam(params, 'endTime', cleanValue(evidence.endTime) || cleanValue(params.get('endTime')));
  setOptionalParam(params, 'sourcePath', sourcePath || browserHttpSource(params.get('sourcePath')));
  setOptionalParam(params, 'source', browserHttpSource(evidence.source) || browserHttpSource(params.get('source')));
  setOptionalParam(params, 'videoUrl', directVideoUrl ? stripFragment(directVideoUrl) : cleanValue(params.get('videoUrl')));

  return `${VIDEO_PAGE_PATH}?${params.toString()}`;
}

function buildDirectSourceLink(evidence: RagEvidence) {
  const source = firstHttpUrl(cleanValue(evidence.sourcePath), cleanValue(evidence.source));
  if (!source) return '';
  const anchor = extractEvidenceAnchor(evidence.sectionTitle || evidence.sectionName) || extractSourceHash(source);
  return anchor ? `${source.split('#', 1)[0]}#${anchor}` : source;
}

function collectComparableSources(evidence: RagEvidence) {
  const metadata = evidence.metadata || {};
  const metadataSources = SOURCE_KEYS
    .map((key) => metadataText(metadata[key]))
    .filter(Boolean);
  return Array.from(new Set([
    evidence.sourcePath,
    evidence.source,
    evidence.playbackUrl,
    ...metadataSources,
  ].map(normalizeComparableSource).filter(Boolean)));
}

function selectContextualEntry(entries: EvidenceLinkEntry[], contextText: string) {
  if (!entries.length) return null;
  const evidenceId = extractContextEvidenceId(contextText);
  if (evidenceId) {
    const matchedById = entries.find((entry) => cleanValue(entry.evidence.evidenceId).toLowerCase() === evidenceId);
    if (matchedById) return matchedById;
  }
  const timeRange = extractContextTimeRange(contextText);
  if (timeRange?.startTime) {
    const matchedByTime = entries.find((entry) => timeMatches(entry.evidence.startTime, timeRange.startTime)
      && (!timeRange.endTime || !entry.evidence.endTime || timeMatches(entry.evidence.endTime, timeRange.endTime)));
    if (matchedByTime) return matchedByTime;
  }
  return entries[0];
}

function extractContextEvidenceId(value: string) {
  const match = /(material-\d+(?:-[a-z0-9_-]+)*)/i.exec(value || '');
  return match?.[1]?.toLowerCase() || '';
}

function extractContextTimeRange(value: string) {
  const timestamp = '(\\d{1,2}:\\d{2}(?::\\d{2})?(?:[,.]\\d+)?)';
  const explicit = new RegExp(`时间\\s*[=:：]\\s*${timestamp}\\s*[-–—~至]\\s*${timestamp}`, 'i').exec(value || '');
  const generic = explicit || new RegExp(`${timestamp}\\s*[-–—~至]\\s*${timestamp}`, 'i').exec(value || '');
  if (!generic) return null;
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

function parseInternalVideoPageParams(value: string) {
  if (!value.startsWith(VIDEO_PAGE_PATH)) return null;
  try {
    const url = new URL(value, 'http://learning-evidence.local');
    return url.pathname === VIDEO_PAGE_PATH ? url.searchParams : null;
  } catch {
    return null;
  }
}

function extractEvidenceAnchor(value?: string | null) {
  const text = (value || '').trim();
  const markdownLink = /\[[^\]]+]\(([^)]+)\)/.exec(text);
  const href = markdownLink?.[1]?.trim().replace(/^<|>$/g, '') || '';
  if (href.startsWith('#')) return href.slice(1);
  return extractSourceHash(href);
}

function firstConservativeVideoUrl(...values: Array<string | null>) {
  return values.find((value) => value && isConservativeVideoUrl(value)) || null;
}

function firstHttpUrl(...values: Array<string | null>) {
  return values.find((value) => value && isHttpUrl(value)) || null;
}

// 远程 Bilibili 资料没有持久化媒体文件，证据应回到平台页面并携带秒级定位。
function firstBilibiliVideoPageUrl(...values: Array<string | null>) {
  return values.find((value) => value && isBilibiliVideoPageUrl(value)) || null;
}

function isBilibiliVideoPageUrl(value: string) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    return url.protocol === 'https:'
      && ['bilibili.com', 'www.bilibili.com', 'm.bilibili.com'].includes(host)
      && /^\/video\/(?:BV[0-9A-Za-z]{8,20}|av[0-9]+)\/?$/i.test(url.pathname);
  } catch {
    return false;
  }
}

function buildBilibiliTimestampLink(value: string, startTime: string) {
  const seconds = timestampToSeconds(startTime);
  if (!Number.isFinite(seconds)) return value;
  const url = new URL(value);
  url.protocol = 'https:';
  url.hostname = 'www.bilibili.com';
  url.port = '';
  url.username = '';
  url.password = '';
  url.hash = '';
  url.searchParams.set('t', String(Math.max(0, Math.floor(seconds))));
  return url.toString();
}

function isConservativeVideoUrl(value: string) {
  return isHttpUrl(value) && VIDEO_EXTENSION_PATTERN.test(stripFragment(value));
}

function isHttpUrl(value: string) {
  return Boolean(browserHttpSource(value));
}

function setOptionalParam(params: URLSearchParams, key: string, value: string | null) {
  if (value) params.set(key, value);
  else params.delete(key);
}

function metadataText(value: unknown) {
  return typeof value === 'string' ? cleanValue(value) : null;
}

function cleanValue(value?: string | null) {
  return (value || '').trim().replace(/^<|>$/g, '');
}

function stripFragment(value: string) {
  return value.split('#', 1)[0];
}
