import type { LearningMaterial, RagProgress } from '../api/types';

// 合并资料刷新结果，避免接口短暂缺少进度字段时覆盖已展示的 RAG 进度。
export function mergeMaterialProgress(previous: LearningMaterial[], next: LearningMaterial[]) {
  const previousById = new Map(previous.map((item) => [item.id, item]));
  return next.map((item) => mergeSingleMaterialProgress(previousById.get(item.id), item));
}

// 将上传接口返回的资料并入当前列表，让后续刷新有可保留的进度基线。
export function upsertMaterialWithProgress(previous: LearningMaterial[], material: LearningMaterial) {
  const merged = mergeSingleMaterialProgress(previous.find((item) => item.id === material.id), material);
  const existingIndex = previous.findIndex((item) => item.id === material.id);
  if (existingIndex < 0) {
    return [merged, ...previous];
  }
  return previous.map((item, index) => index === existingIndex ? merged : item);
}

// 优先使用最新进度；当最新响应缺失进度时保留旧进度展示。
function mergeSingleMaterialProgress(previous: LearningMaterial | undefined, next: LearningMaterial) {
  if (!previous) {
    return next;
  }
  return {
    ...next,
    latestProgress: next.latestProgress || previous.latestProgress,
    progressEvents: mergeProgressEvents(previous.progressEvents, next.progressEvents)
  };
}

// 合并刷新前后的进度，保留视频关键阶段，避免逐帧 OCR 事件挤掉翻页检测结果。
function mergeProgressEvents(previous?: RagProgress[], next?: RagProgress[]) {
  if (!next?.length) {
    return previous;
  }
  const merged = [...next];
  for (const progress of previous || []) {
    if (!isStickyVideoProgress(progress)) {
      continue;
    }
    if (!merged.some((item) => progressKey(item) === progressKey(progress))) {
      merged.push(progress);
    }
  }
  return merged;
}

function isStickyVideoProgress(progress: RagProgress) {
  return [
    'parse.video.frame.extract',
    'parse.video.frame.candidates',
    'parse.video.slide_detect',
    'parse.video.ocr'
  ].includes(progress.stageCode);
}

function progressKey(progress: RagProgress) {
  return [
    progress.stageCode || '',
    progress.message || '',
    progress.chunkId || '',
    progress.currentChunk || '',
    progress.totalChunks || ''
  ].join('|');
}
