import { FileText, Loader2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { fetchMaterialPreview } from '../../api/rag';
import type { MaterialPreview as MaterialPreviewData } from '../../api/types';
import { MarkdownText } from '../../components/MarkdownText';

// 资料预览页在新标签中展示 Markdown/Text 原文，避免直接访问 OSS 触发下载。
export function MaterialPreview() {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const [preview, setPreview] = useState<MaterialPreviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const contentRef = useRef<HTMLDivElement>(null);
  const materialId = Number(id);
  const source = searchParams.get('source');
  const anchor = searchParams.get('anchor');
  const displayContent = useMemo(() => preview?.content || '', [preview?.content]);

  useEffect(() => {
    if (!Number.isFinite(materialId) || materialId <= 0) {
      setError('资料 ID 不合法');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    fetchMaterialPreview(materialId, source)
      .then(setPreview)
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : '资料预览加载失败'))
      .finally(() => setLoading(false));
  }, [materialId, source]);

  useEffect(() => {
    if (!anchor || !preview) {
      return;
    }
    window.requestAnimationFrame(() => scrollToAnchor(contentRef.current, anchor));
  }, [anchor, preview]);

  return (
    <div className="preview-shell">
      <header className="preview-topbar">
        <div>
          <span><FileText size={16} />资料预览</span>
          <h1>{preview?.title || '学习资料'}</h1>
          <p>{preview?.documentType || 'text'} · {preview?.contentType || 'text/plain'}</p>
        </div>
        {preview?.source ? <span className="preview-source-label">来源已保留</span> : null}
      </header>

      <main className="preview-content" ref={contentRef}>
        {loading ? (
          <div className="preview-state"><Loader2 className="spin" size={20} />正在加载资料...</div>
        ) : error ? (
          <div className="preview-state danger">{error}</div>
        ) : (
          <MarkdownText content={displayContent} />
        )}
      </main>
    </div>
  );
}

// 根据 Markdown 标题文本或 slug 定位到页面中的标题。
function scrollToAnchor(container: HTMLDivElement | null, anchor: string) {
  if (!container) return;
  const normalizedAnchor = normalizeAnchor(anchor);
  const headings = Array.from(container.querySelectorAll('h4, h5, h6'));
  const target = headings.find((heading) => {
    const text = heading.textContent || '';
    return normalizeAnchor(text) === normalizedAnchor || slugifyHeading(text) === normalizedAnchor;
  });
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function normalizeAnchor(value: string) {
  return decodeURIComponent(value)
    .trim()
    .replace(/^_+/, '')
    .toLowerCase();
}

function slugifyHeading(value: string) {
  return normalizeAnchor(value)
    .replace(/[^\w\s\u4e00-\u9fa5-]+/g, '')
    .replace(/\s+/g, '-');
}
