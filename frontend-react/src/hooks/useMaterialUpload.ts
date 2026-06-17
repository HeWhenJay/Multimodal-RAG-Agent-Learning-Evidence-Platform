import { useCallback, useState } from 'react';
import { uploadMaterial, uploadMaterialChunk } from '../api/rag';
import type { LearningMaterial } from '../api/types';

export const MATERIAL_FILE_ACCEPT = '.pdf,.doc,.docx,.ppt,.pptx,.md,.markdown,.xls,.xlsx,.txt,.srt,.vtt,.png,.jpg,.jpeg,.webp,.mp4,.mov,.m4v,.webm,.mkv,.avi';
export const MATERIAL_UPLOADED_EVENT = 'learning-evidence:material-uploaded';
const VIDEO_CHUNK_SIZE = 20 * 1024 * 1024;
const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'];

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

  const uploadFile = useCallback(async (file: File | null) => {
    if (!file) {
      return null;
    }

    setUploading(true);
    setUploadMessage(`正在上传：${file.name}`);
    try {
      const material = shouldUseChunkUpload(file)
        ? await uploadVideoInChunks(file, highPrecision, setUploadMessage)
        : await uploadMaterial(file, highPrecision);
      setUploadMessage(`已上传，正在后台解析：${file.name}`);
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
  }, [highPrecision, onUploaded]);

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

// 按固定大小切分视频文件，最后一个分片完成时返回资料索引结果。
async function uploadVideoInChunks(
  file: File,
  highPrecision: boolean,
  setUploadMessage: (message: string) => void
): Promise<LearningMaterial> {
  const totalChunks = Math.ceil(file.size / VIDEO_CHUNK_SIZE);
  let uploadId = '';
  for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
    const start = chunkIndex * VIDEO_CHUNK_SIZE;
    const end = Math.min(file.size, start + VIDEO_CHUNK_SIZE);
    const chunk = file.slice(start, end, file.type || 'application/octet-stream');
    setUploadMessage(`正在上传视频分片：${chunkIndex + 1}/${totalChunks}`);
    const result = await uploadMaterialChunk({
      chunk,
      filename: file.name,
      uploadId,
      chunkIndex,
      totalChunks,
      totalSize: file.size,
      highPrecision
    });
    uploadId = result.uploadId;
    if (result.completed && result.material) {
      return result.material;
    }
  }
  throw new Error('视频分片上传未返回资料索引结果');
}
