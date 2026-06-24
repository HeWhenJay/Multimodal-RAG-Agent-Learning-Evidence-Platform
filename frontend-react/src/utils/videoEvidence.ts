import type { RagEvidence } from '../api/types';

const VIDEO_PAGE_PATH = '/videos';
const VIDEO_EXTENSION_PATTERN = /\.(mp4|mov|m4v|webm|mkv|avi)(?:[?#]|$)/i;
const VIDEO_PAGE_PARAM_KEYS = ['documentId', 'title', 'startTime', 'endTime', 'sourcePath', 'videoUrl', 'returnTo'];

// 根据 RAG evidence 字段构造内部视频播放定位地址。
export function buildVideoEvidenceLink(evidence: RagEvidence) {
  const startTime = cleanValue(evidence.startTime);
  if (!startTime) return null;

  const params = new URLSearchParams();
  const playbackUrl = cleanValue(evidence.playbackUrl);
  const internalPlaybackParams = playbackUrl ? parseInternalVideoPageParams(playbackUrl) : null;
  if (internalPlaybackParams) {
    VIDEO_PAGE_PARAM_KEYS.forEach((key) => {
      const value = cleanValue(internalPlaybackParams.get(key));
      if (value) params.set(key, key === 'videoUrl' ? stripFragment(value) : value);
    });
  } else if (playbackUrl && isHttpUrl(playbackUrl)) {
    params.set('videoUrl', stripFragment(playbackUrl));
  }

  const title = cleanValue(evidence.documentTitle) || cleanValue(evidence.title);
  const documentId = cleanValue(evidence.documentId) || cleanValue(params.get('documentId'));
  const sourcePath = cleanValue(evidence.sourcePath);
  const sourceVideoUrl = findSourceVideoUrl(evidence);

  if (documentId) params.set('documentId', documentId);
  if (title) params.set('title', title);
  params.set('startTime', startTime);
  setOptionalParam(params, 'endTime', cleanValue(evidence.endTime) || cleanValue(params.get('endTime')));
  setOptionalParam(params, 'sourcePath', sourcePath || sourceVideoUrl || cleanValue(params.get('sourcePath')));

  if (!cleanValue(params.get('videoUrl'))) {
    const directSourceUrl = sourcePath && isConservativeVideoUrl(sourcePath) ? sourcePath : sourceVideoUrl;
    setOptionalParam(params, 'videoUrl', directSourceUrl ? stripFragment(directSourceUrl) : null);
  }
  if (!cleanValue(params.get('returnTo')) && typeof window !== 'undefined') {
    params.set('returnTo', `${window.location.pathname}${window.location.search}`);
  }

  const hasPlaybackEntry = Boolean(playbackUrl || sourcePath || sourceVideoUrl || cleanValue(params.get('videoUrl')));
  return hasPlaybackEntry ? `${VIDEO_PAGE_PATH}?${params.toString()}` : null;
}

// 只从明确的视频 URL 来源中提取播放器候选，避免把普通网页或来源枚举误判为视频。
function findSourceVideoUrl(evidence: RagEvidence) {
  const source = cleanValue(evidence.source);
  return source && isConservativeVideoUrl(source) ? stripFragment(source) : null;
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

function setOptionalParam(params: URLSearchParams, key: string, value: string | null) {
  if (value) {
    params.set(key, value);
  } else {
    params.delete(key);
  }
}

function cleanValue(value?: string | null) {
  const text = value?.trim();
  return text ? text : null;
}

function stripFragment(value: string) {
  return value.split('#', 1)[0];
}

function isHttpUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function isConservativeVideoUrl(value: string) {
  return isHttpUrl(value) && VIDEO_EXTENSION_PATTERN.test(stripFragment(value));
}
