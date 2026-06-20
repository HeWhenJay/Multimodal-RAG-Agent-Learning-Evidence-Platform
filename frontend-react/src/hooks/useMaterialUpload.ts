import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchMaterial, uploadMaterial, uploadMaterialChunk } from '../api/rag';
import type { LearningMaterial } from '../api/types';

export const MATERIAL_FILE_ACCEPT = '.pdf,.doc,.docx,.ppt,.pptx,.md,.markdown,.xls,.xlsx,.txt,.srt,.vtt,.png,.jpg,.jpeg,.webp,.mp4,.mov,.m4v,.webm,.mkv,.avi';
export const MATERIAL_UPLOADED_EVENT = 'learning-evidence:material-uploaded';
const VIDEO_CHUNK_SIZE = 20 * 1024 * 1024;
const PROGRESS_POLL_INTERVAL_MS = 2000;
const CHUNK_UPLOAD_RETRY_LIMIT = 3;
const CHUNK_UPLOAD_SESSION_PREFIX = 'learning-evidence:chunk-upload:';
const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'];

interface ChunkUploadSession {
  uploadId: string;
  nextChunkIndex: number;
}

interface UseMaterialUploadOptions {
  highPrecision?: boolean;
  onUploaded?: (material: LearningMaterial) => void | Promise<void>;
}

// 广播资料上传完成事件，便于工作台、顶部栏和资料页同步刷新。
function publishMaterialUploaded(material: LearningMaterial) {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(new CustomEvent<LearningMaterial>(MATERIAL_UPLOADED_EVENT, { detail: material }));
}

// 统一处理学习资料文件上传、状态提示和上传完成通知。
export function useMaterialUpload({ highPrecision = false, onUploaded }: UseMaterialUploadOptions = {}) {
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState('');
  const progressTimerRef = useRef<number | null>(null);

  // 停止当前上传资料的进度轮询，避免连续上传时串扰。
  const stopProgressPolling = useCallback(() => {
    if (progressTimerRef.current !== null) {
      window.clearInterval(progressTimerRef.current);
      progressTimerRef.current = null;
    }
  }, []);

  // 上传完成后继续轮询 Java 资料状态，让上传提示显示真实 RAG 阶段。
  const startProgressPolling = useCallback((materialId: number, filename: string) => {
    stopProgressPolling();
    const poll = async () => {
      try {
        const material = await fetchMaterial(materialId);
        setUploadMessage(formatUploadProgress(material, filename));
        if (isTerminalStatus(material.status)) {
          stopProgressPolling();
        }
      } catch {
        setUploadMessage(`已上传，等待 RAG 进度：${filename}`);
      }
    };
    void poll();
    progressTimerRef.current = window.setInterval(() => {
      void poll();
    }, PROGRESS_POLL_INTERVAL_MS);
  }, [stopProgressPolling]);

  useEffect(() => stopProgressPolling, [stopProgressPolling]);

  const uploadFile = useCallback(async (file: File | null) => {
    if (!file) {
      return null;
    }

    stopProgressPolling();
    setUploading(true);
    setUploadMessage(`正在上传：${file.name}`);
    try {
      const material = shouldUseChunkUpload(file)
        ? await uploadVideoInChunks(file, highPrecision, setUploadMessage)
        : await uploadMaterial(file, highPrecision);
      setUploadMessage(formatUploadProgress(material, file.name));
      startProgressPolling(material.id, file.name);
      publishMaterialUploaded(material);
      await onUploaded?.(material);
      return material;
    } catch (error) {
      const message = error instanceof Error ? error.message : '上传失败';
      setUploadMessage(message);
      throw error;
    } finally {
      setUploading(false);
    }
  }, [highPrecision, onUploaded, startProgressPolling, stopProgressPolling]);

  return {
    uploading,
    uploadMessage,
    setUploadMessage,
    uploadFile
  };
}

// 判断视频是否需要走分片上传，避免单个 multipart 请求过大。
function shouldUseChunkUpload(file: File) {
  const lower = file.name.toLowerCase();
  return file.size > VIDEO_CHUNK_SIZE && VIDEO_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

// 生成上传提示的主文案，优先展示当前 RAG 处理阶段和切块进度。
function formatUploadProgress(material: LearningMaterial, filename: string) {
  const progress = material.latestProgress;
  if (!progress) {
    if (isTerminalStatus(material.status)) {
      return `${formatMaterialStatus(material.status)}：${filename}`;
    }
    return `已上传，等待 RAG 进度：${filename}`;
  }
  const parts = [
    progress.message || progress.stageLabel || progress.stageCode,
    progress.currentChunk && progress.totalChunks ? `切块 ${progress.currentChunk}/${progress.totalChunks}` : '',
    typeof progress.percent === 'number' ? `${Math.round(progress.percent)}%` : ''
  ].filter(Boolean);
  if (parts.length > 0) {
    return parts.join(' · ');
  }
  return `${formatMaterialStatus(material.status)}：${filename}`;
}

// 判断后台解析是否已经进入终态。
function isTerminalStatus(status: string) {
  return ['READY', 'PARTIAL', 'FAILED'].includes(status);
}

// 将资料终态转换为上传提示文本。
function formatMaterialStatus(status: string) {
  if (status === 'READY') return '已入库';
  if (status === 'PARTIAL') return '部分完成';
  if (status === 'FAILED') return '解析失败';
  if (status === 'REINDEXING') return '重建索引';
  if (status === 'PARSING') return '解析中';
  if (status === 'PENDING') return '等待解析';
  return status;
}

// 按固定大小切分视频文件，分片收齐后以后端返回的资料记录进入轮询。
async function uploadVideoInChunks(
  file: File,
  highPrecision: boolean,
  setUploadMessage: (message: string) => void
): Promise<LearningMaterial> {
  const totalChunks = Math.ceil(file.size / VIDEO_CHUNK_SIZE);
  const sessionKey = chunkUploadSessionKey(file);
  let session = readChunkUploadSession(sessionKey);
  if (!session) {
    session = {
      uploadId: createUploadId(),
      nextChunkIndex: 0
    };
    writeChunkUploadSession(sessionKey, session);
  }

  for (let chunkIndex = clampChunkIndex(session.nextChunkIndex, totalChunks); chunkIndex < totalChunks; chunkIndex += 1) {
    const start = chunkIndex * VIDEO_CHUNK_SIZE;
    const end = Math.min(file.size, start + VIDEO_CHUNK_SIZE);
    const chunk = file.slice(start, end, file.type || 'application/octet-stream');
    setUploadMessage(`正在上传视频分片：${chunkIndex + 1}/${totalChunks}，uploadId=${session.uploadId}`);
    const result = await uploadChunkWithRetry(
      {
        chunk,
        filename: file.name,
        uploadId: session.uploadId,
        chunkIndex,
        totalChunks,
        totalSize: file.size,
        highPrecision
      },
      setUploadMessage
    );
    session = {
      uploadId: result.uploadId || session.uploadId,
      nextChunkIndex: clampChunkIndex(result.nextChunkIndex ?? chunkIndex + 1, totalChunks)
    };
    writeChunkUploadSession(sessionKey, session);
    if (result.message) {
      setUploadMessage(result.message);
    }
    if (result.completed && result.material) {
      clearChunkUploadSession(sessionKey);
      return result.material;
    }
    chunkIndex = session.nextChunkIndex - 1;
  }
  throw new Error('视频分片已上传，但后端未返回可轮询的资料记录');
}

// 单片上传失败只重试当前分片，避免已成功分片被重新上传。
async function uploadChunkWithRetry(
  payload: Parameters<typeof uploadMaterialChunk>[0],
  setUploadMessage: (message: string) => void
) {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= CHUNK_UPLOAD_RETRY_LIMIT; attempt += 1) {
    try {
      return await uploadMaterialChunk(payload);
    } catch (error) {
      lastError = error;
      if (attempt >= CHUNK_UPLOAD_RETRY_LIMIT) {
        break;
      }
      setUploadMessage(`第 ${payload.chunkIndex + 1}/${payload.totalChunks} 个视频分片上传失败，正在重试 ${attempt + 1}/${CHUNK_UPLOAD_RETRY_LIMIT}`);
    }
  }
  throw lastError instanceof Error ? lastError : new Error('视频分片上传失败');
}

// 生成当前文件的续传状态键。
function chunkUploadSessionKey(file: File) {
  return `${CHUNK_UPLOAD_SESSION_PREFIX}${file.name}:${file.size}:${file.lastModified}`;
}

// 读取本地保存的续传状态。
function readChunkUploadSession(key: string): ChunkUploadSession | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ChunkUploadSession;
    if (!parsed.uploadId) return null;
    return {
      uploadId: parsed.uploadId,
      nextChunkIndex: Number.isFinite(parsed.nextChunkIndex) ? parsed.nextChunkIndex : 0
    };
  } catch {
    return null;
  }
}

// 保存本地续传状态，刷新或重新选择同一文件时继续使用同一个 uploadId。
function writeChunkUploadSession(key: string, session: ChunkUploadSession) {
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.setItem(key, JSON.stringify(session));
}

// 上传完成后清理本地续传状态。
function clearChunkUploadSession(key: string) {
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.removeItem(key);
}

// 生成前端上传批次 ID，避免首片响应丢失后无法续传。
function createUploadId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID().replace(/-/g, '');
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

// 限制续传分片序号，避免本地缓存异常导致越界。
function clampChunkIndex(value: number, totalChunks: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(totalChunks, Math.trunc(value)));
}
