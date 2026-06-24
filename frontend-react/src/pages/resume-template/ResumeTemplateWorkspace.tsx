import {
  ExternalLink,
  FileText,
  Highlighter,
  History,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
  Save,
  TriangleAlert,
  Upload,
  XCircle
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { PointerEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  deleteResumeTemplate,
  fetchResumeTemplate,
  fetchResumeTemplatePreview,
  fetchResumeTemplatePreviewImage,
  fetchResumeTemplates,
  saveResumeTemplateAnnotations,
  uploadResumeTemplate
} from '../../api/rag';
import type {
  ResumeTemplate,
  ResumeTemplatePreview,
  ResumeTemplateRegionAnnotation
} from '../../api/types';

type DraftRegion = {
  pageIndex: number;
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
};

// 简历模板页只负责模板选择、字段识别、图片区域确认和模板效果原型预览。
export function ResumeTemplateWorkspace() {
  const [searchParams] = useSearchParams();
  const [templates, setTemplates] = useState<ResumeTemplate[]>([]);
  const [template, setTemplate] = useState<ResumeTemplate | null>(null);
  const [preview, setPreview] = useState<ResumeTemplatePreview | null>(null);
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string>('');
  const [imageUrls, setImageUrls] = useState<Record<number, string>>({});
  const [draftRegion, setDraftRegion] = useState<DraftRegion | null>(null);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [annotationDirty, setAnnotationDirty] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const preferredTemplateId = searchParams.get('templateId') || '';

  const fieldsById = useMemo(() => new Map((template?.fields || []).map((field) => [field.fieldId, field])), [template]);
  const editableAnnotations = useMemo(
    () => (preview?.annotations || []).filter((item) => item.editable && item.status === 'ACTIVE' && item.fieldId),
    [preview]
  );
  const selectedAnnotation = useMemo(
    () => (preview?.annotations || []).find((item) => item.annotationId === selectedAnnotationId) || null,
    [preview, selectedAnnotationId]
  );

  useEffect(() => {
    void loadTemplateHistory(preferredTemplateId);
  }, []);

  useEffect(() => {
    let disposed = false;
    const objectUrls: string[] = [];
    async function loadImages() {
      if (!preview?.pages.length) {
        setImageUrls({});
        return;
      }
      const entries = await Promise.all(preview.pages.map(async (page) => {
        const objectUrl = await fetchResumeTemplatePreviewImage(page.imageUrl);
        objectUrls.push(objectUrl);
        return [page.pageIndex, objectUrl] as const;
      }));
      if (!disposed) {
        setImageUrls(Object.fromEntries(entries));
      }
    }
    void loadImages().catch((imageError) => setError(imageError instanceof Error ? imageError.message : '预览图片读取失败'));
    return () => {
      disposed = true;
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [preview]);

  // 读取历史模板并自动选择 URL 指定模板或最近可用模板。
  async function loadTemplateHistory(preferTemplateId = template?.templateId || '') {
    try {
      setTemplateLoading(true);
      setError('');
      const history = await fetchResumeTemplates(30);
      setTemplates(history);
      const currentTemplateId = preferTemplateId || template?.templateId || '';
      const nextTemplate = history.find((item) => item.templateId === currentTemplateId)
        || history.find(templateCanUse)
        || history[0]
        || null;
      if (nextTemplate) {
        await selectTemplate(nextTemplate.templateId, true);
      } else {
        clearTemplateState();
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '简历模板历史加载失败');
    } finally {
      setTemplateLoading(false);
    }
  }

  // 选择历史模板后刷新字段详情和图片预览。
  async function selectTemplate(templateId: string, silent = false) {
    if (!templateId) return;
    try {
      setBusy(true);
      setError('');
      if (!silent) {
        setMessage('');
      }
      const detail = await fetchResumeTemplate(templateId);
      setTemplate(detail);
      setPreview(null);
      setImageUrls({});
      setDraftRegion(null);
      setAnnotationDirty(false);
      await loadPreview(detail.templateId, false, detail);
      if (!silent) {
        setMessage(`已选择模板：${detail.filename}`);
      }
    } catch (selectError) {
      setError(selectError instanceof Error ? selectError.message : '简历模板加载失败');
    } finally {
      setBusy(false);
    }
  }

  // 上传 DOCX 后解析字段绑定，并加入历史模板列表。
  async function submitTemplate(file: File | null) {
    if (!file) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await uploadResumeTemplate(file);
      setTemplates((previous) => [result, ...previous.filter((item) => item.templateId !== result.templateId)]);
      setTemplate(result);
      setPreview(null);
      setImageUrls({});
      setDraftRegion(null);
      setAnnotationDirty(false);
      setMessage('简历模板字段解析完成，正在生成图片预览');
      await loadPreview(result.templateId, false, result);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '简历模板解析失败');
    } finally {
      setBusy(false);
    }
  }

  // 删除指定模板，并在列表中切换到下一份可用模板。
  async function removeTemplate(templateId: string) {
    if (!templateId || !window.confirm('确认删除这份简历模板及其预览、草稿和导出记录？')) {
      return;
    }
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await deleteResumeTemplate(templateId);
      const nextTemplates = templates.filter((item) => item.templateId !== templateId);
      setTemplates(nextTemplates);
      if (template?.templateId === templateId) {
        const nextTemplate = nextTemplates.find(templateCanUse) || nextTemplates[0] || null;
        if (nextTemplate) {
          await selectTemplate(nextTemplate.templateId, true);
          setMessage('当前模板已删除，已切换到下一份模板');
        } else {
          clearTemplateState();
          setMessage('简历模板已删除');
        }
      } else {
        setMessage('简历模板已删除');
      }
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '简历模板删除失败');
    } finally {
      setBusy(false);
    }
  }

  // 查询或刷新图片预览。
  async function loadPreview(templateId = template?.templateId || '', refresh = false, sourceTemplate = template) {
    if (!templateId) return;
    try {
      setPreviewLoading(true);
      const result = await fetchResumeTemplatePreview(templateId, refresh);
      setPreview(result);
      setSelectedAnnotationId(result.annotations[0]?.annotationId || '');
      setAnnotationDirty(false);
      if (result.previewStatus === 'UNAVAILABLE') {
        setMessage('图片预览暂不可用，模板字段列表仍可用于 Agent 简历修改');
      } else if (result.previewStatus === 'PARTIAL') {
        setMessage(`字段草图预览已生成，可继续确认区域：${result.warnings[0] || sourceTemplate?.filename || '部分字段未精确映射'}`);
      } else if (sourceTemplate) {
        setMessage(`模板效果预览已生成：${sourceTemplate.filename}`);
      }
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : '图片预览生成失败');
    } finally {
      setPreviewLoading(false);
    }
  }

  // 保存图片区域约束，Agent 侧生成简历补丁时会读取这些确认结果。
  async function submitAnnotations() {
    if (!template || !preview) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await saveResumeTemplateAnnotations(template.templateId, {
        version: template.version,
        annotations: preview.annotations.map((annotation) => ({
          ...annotation,
          annotationId: annotation.annotationId?.startsWith('local-') ? null : annotation.annotationId
        }))
      });
      setPreview(result);
      setAnnotationDirty(false);
      const activeCount = result.annotations.filter((item) => item.editable && item.status === 'ACTIVE' && item.fieldId).length;
      setSelectedAnnotationId((current) => result.annotations.some((item) => item.annotationId === current) ? current : result.annotations[0]?.annotationId || '');
      setMessage(`图片区域约束已保存：${activeCount} 个字段允许 Agent 修改`);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '图片区域约束保存失败');
    } finally {
      setBusy(false);
    }
  }

  // 更新单条标注属性，并把页面标记为待保存。
  function updateAnnotation(annotationId: string | undefined | null, updater: (annotation: ResumeTemplateRegionAnnotation) => ResumeTemplateRegionAnnotation) {
    if (!annotationId) return;
    setPreview((previous) => {
      if (!previous) return previous;
      return {
        ...previous,
        annotations: previous.annotations.map((annotation) => annotation.annotationId === annotationId ? updater(annotation) : annotation)
      };
    });
    setAnnotationDirty(true);
    setMessage('');
  }

  // 在预览图片上拖拽创建未绑定视觉备注。
  function startManualRegion(event: PointerEvent<HTMLDivElement>, pageIndex: number) {
    if (!preview) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = clamp01((event.clientX - rect.left) / rect.width);
    const y = clamp01((event.clientY - rect.top) / rect.height);
    setDraftRegion({ pageIndex, startX: x, startY: y, currentX: x, currentY: y });
  }

  // 更新拖拽中的视觉备注区域。
  function moveManualRegion(event: PointerEvent<HTMLDivElement>) {
    if (!draftRegion) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setDraftRegion({
      ...draftRegion,
      currentX: clamp01((event.clientX - rect.left) / rect.width),
      currentY: clamp01((event.clientY - rect.top) / rect.height)
    });
  }

  // 完成拖拽并写入未绑定标注。
  function finishManualRegion() {
    if (!preview || !draftRegion) return;
    const rect = draftToRect(draftRegion);
    if (rect.width < 0.01 || rect.height < 0.01) {
      setDraftRegion(null);
      return;
    }
    const annotationId = `local-${Date.now()}`;
    setPreview({
      ...preview,
      annotations: [
        ...preview.annotations,
        {
          annotationId,
          fieldId: null,
          pageIndex: draftRegion.pageIndex,
          rect,
          sourceType: 'MANUAL_UNBOUND',
          editable: false,
          sectionKey: 'other',
          userInstruction: '',
          requiredEvidencePolicy: 'NONE',
          status: 'ACTIVE',
          annotationRevision: null
        }
      ]
    });
    setSelectedAnnotationId(annotationId);
    setDraftRegion(null);
    setAnnotationDirty(true);
    setMessage('');
  }

  function clearTemplateState() {
    setTemplate(null);
    setPreview(null);
    setImageUrls({});
    setSelectedAnnotationId('');
    setDraftRegion(null);
    setAnnotationDirty(false);
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>简历模板</h2>
          <p>上传、选择、预览和确认 DOCX 模板可修改区域</p>
        </div>
        <Link className="primary-action" to="/agent">
          <ExternalLink size={17} />
          <span>去 Agent 按 JD 修改简历</span>
        </Link>
      </section>

      <div className="resume-template-steps">
        {['选择模板', '生成预览', '确认区域', 'Agent 修改'].map((step, index) => (
          <span key={step} className={stepActive(index, template, preview, annotationDirty, editableAnnotations.length) ? 'active' : ''}>
            {index + 1}. {step}
          </span>
        ))}
      </div>

      <section className="resume-template-grid">
        <article className="panel">
          <div className="panel-title">
            <h3><Upload size={20} />上传模板</h3>
            <span className="status-pill">{template ? formatTemplateStatus(template.status) : '未选择'}</span>
          </div>
          <label className="file-drop compact">
            <FileText size={28} />
            <strong>上传 DOCX 简历模板</strong>
            <span>解析字段绑定、hash 和 layout fingerprint</span>
            <input
              type="file"
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0] || null;
                event.target.value = '';
                void submitTemplate(file);
              }}
            />
          </label>
          {template ? (
            <div className="resume-template-meta">
              <strong>{template.filename}</strong>
              <span>版本 {template.version} · {template.fields.length} 个字段 · {template.unsupportedRegions.length} 个不支持区域</span>
              <span>更新时间 {formatTime(template.updatedAt || template.createdAt)}</span>
              <button className="chip-button danger" onClick={() => void removeTemplate(template.templateId)} disabled={busy} type="button">
                <XCircle size={16} />删除当前模板
              </button>
            </div>
          ) : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3><History size={20} />历史模板</h3>
            <button className="chip-button" onClick={() => void loadTemplateHistory()} disabled={templateLoading || busy} type="button">
              {templateLoading ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
              刷新
            </button>
          </div>
          <div className="resume-template-history-list">
            {templateLoading ? (
              <div className="empty-state compact">
                <Loader2 className="spin" size={17} />
                <span>正在读取历史模板</span>
              </div>
            ) : templates.length ? (
              templates.map((item) => {
                const active = item.templateId === template?.templateId;
                return (
                  <div className="resume-template-history-item" key={item.templateId}>
                    <button
                      className={['resume-template-history-card', active ? 'is-active' : '', templateCanUse(item) ? '' : 'is-disabled'].filter(Boolean).join(' ')}
                      disabled={busy || !templateCanUse(item)}
                      onClick={() => void selectTemplate(item.templateId)}
                      type="button"
                    >
                      <span className="resume-template-card-top">
                        <FileText size={17} />
                        <em>{formatTemplateStatus(item.status)}</em>
                      </span>
                      <strong>{item.filename}</strong>
                      <small>版本 {item.version} · {item.fields.length} 个字段 · {formatTime(item.updatedAt || item.createdAt)}</small>
                    </button>
                    <button className="icon-button small danger" disabled={busy} onClick={() => void removeTemplate(item.templateId)} title="删除模板" type="button">
                      <XCircle size={15} />
                    </button>
                  </div>
                );
              })
            ) : (
              <div className="empty-state compact">暂无上传过的简历模板</div>
            )}
          </div>
        </article>
      </section>

      {message ? <p className="form-message">{message}</p> : null}
      {error ? <p className="form-message danger">{error}</p> : null}
      {annotationDirty ? (
        <p className="form-message warning">
          区域约束有未保存改动。保存后，Agent 按 JD 修改简历时才会使用这些确认结果。
        </p>
      ) : null}

      <TemplatePrototype
        template={template}
        preview={preview}
        imageUrls={imageUrls}
        editableCount={editableAnnotations.length}
        selectedAnnotationId={selectedAnnotationId}
      />

      <section className="panel resume-preview-shell">
        <div className="panel-title">
          <h3><ImageIcon size={20} />图片预览确认</h3>
          <div className="resume-preview-title-actions">
            <span className={`status-pill ${annotationDirty ? 'running' : editableAnnotations.length ? 'indexed' : ''}`}>
              {annotationDirty ? '待保存' : preview ? `${editableAnnotations.length} 个可修改区域` : '未生成'}
            </span>
            <button className="chip-button" onClick={() => void loadPreview(undefined, true)} disabled={busy || previewLoading || !template}>
              {previewLoading ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
              刷新预览
            </button>
          </div>
        </div>
        <Legend />
        <p className="resume-template-hint">在图片上选择字段区域，打开“允许 Agent 修改”，再保存区域约束。手动备注未绑定字段时只用于视觉说明，不会自动修改 DOCX。</p>
        {preview?.warnings.length ? (
          <div className="resume-validation-errors">
            {preview.warnings.map((item) => <span key={item}><TriangleAlert size={15} />{item}</span>)}
          </div>
        ) : null}
        {preview?.pages.length ? (
          <div className="resume-preview-layout">
            <div className="resume-preview-pages">
              {preview.pages.map((page) => (
                <div
                  className="resume-preview-page"
                  key={page.pageIndex}
                  onPointerDown={(event) => startManualRegion(event, page.pageIndex)}
                  onPointerMove={moveManualRegion}
                  onPointerUp={finishManualRegion}
                >
                  {imageUrls[page.pageIndex] ? <img src={imageUrls[page.pageIndex]} alt={`简历预览第 ${page.pageIndex + 1} 页`} draggable={false} /> : <div className="resume-preview-loading">图片读取中</div>}
                  {preview.annotations.filter((annotation) => annotation.pageIndex === page.pageIndex).map((annotation) => (
                    <button
                      key={annotation.annotationId}
                      type="button"
                      className={`resume-region ${regionClass(annotation, selectedAnnotationId)}`}
                      style={rectStyle(annotation.rect)}
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={() => setSelectedAnnotationId(annotation.annotationId || '')}
                      title={regionTitle(annotation, fieldsById)}
                    />
                  ))}
                  {draftRegion?.pageIndex === page.pageIndex ? <span className="resume-region manual-unbound drafting" style={rectStyle(draftToRect(draftRegion))} /> : null}
                </div>
              ))}
            </div>
            <AnnotationPanel
              annotation={selectedAnnotation}
              fields={template?.fields || []}
              onChange={updateAnnotation}
            />
          </div>
        ) : (
          <div className="empty-state">
            {previewLoading ? '正在生成图片预览' : '图片预览不可用或尚未生成，模板字段仍可在 Agent 中作为修改边界'}
          </div>
        )}
        <div className="resume-template-actions">
          <Link className="ghost-action" to="/agent">
            <ExternalLink size={17} />去 Agent 填写岗位 JD
          </Link>
          <button className="primary-action" onClick={() => void submitAnnotations()} disabled={busy || !preview?.annotations.length || !annotationDirty} type="button">
            {busy ? <Loader2 className="spin" size={17} /> : <Save size={17} />}
            保存区域约束
          </button>
        </div>
      </section>
    </div>
  );
}

function TemplatePrototype({
  template,
  preview,
  imageUrls,
  editableCount,
  selectedAnnotationId
}: {
  template: ResumeTemplate | null;
  preview: ResumeTemplatePreview | null;
  imageUrls: Record<number, string>;
  editableCount: number;
  selectedAnnotationId: string;
}) {
  const firstPage = preview?.pages[0];
  const firstPageAnnotations = firstPage ? preview.annotations.filter((annotation) => annotation.pageIndex === firstPage.pageIndex) : [];
  const firstPageImage = firstPage ? imageUrls[firstPage.pageIndex] : '';
  return (
    <section className="panel resume-template-prototype">
      <div className="panel-title">
        <h3><Highlighter size={20} />模板效果原型图</h3>
        <span className="status-pill">{template ? `${template.fields.length} 个识别字段` : '等待模板'}</span>
      </div>
      {template ? (
        <div className="resume-prototype-layout">
          <div className="resume-prototype-canvas">
            {firstPageImage ? (
              <>
                <img src={firstPageImage} alt="简历模板效果原型图" />
                {firstPageAnnotations.map((annotation) => (
                  <span
                    key={annotation.annotationId}
                    className={`resume-region prototype-region ${regionClass(annotation, selectedAnnotationId)}`}
                    style={rectStyle(annotation.rect)}
                  />
                ))}
              </>
            ) : (
              <div className="resume-prototype-paper">
                {template.fields.slice(0, 8).map((field) => (
                  <div className="resume-prototype-line" key={field.fieldId}>
                    <strong>{field.displayName}</strong>
                    <span>{field.sourceText.slice(0, 48)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="resume-prototype-summary">
            <MetaBlock label="当前模板" value={template.filename} />
            <MetaBlock label="版本" value={String(template.version)} />
            <MetaBlock label="允许 Agent 修改" value={`${editableCount} 个区域`} />
            <MetaBlock label="复杂结构" value={`${template.unsupportedRegions.length} 个`} />
            <div className="resume-prototype-tags">
              {template.fields.slice(0, 8).map((field) => <span key={field.fieldId}>{formatSection(field.sectionKey)}</span>)}
            </div>
            <p>这里展示的是 Agent 后续生成 DOCX 前可见的模板原型：图片来自真实 DOCX 渲染或字段草图降级预览，彩色区域代表已识别或人工确认的可修改字段。</p>
          </div>
        </div>
      ) : (
        <div className="empty-state">选择或上传模板后显示真实 DOCX 效果原型图</div>
      )}
    </section>
  );
}

function MetaBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="resume-prototype-meta">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function AnnotationPanel({
  annotation,
  fields,
  onChange
}: {
  annotation: ResumeTemplateRegionAnnotation | null;
  fields: ResumeTemplate['fields'];
  onChange: (annotationId: string | undefined | null, updater: (annotation: ResumeTemplateRegionAnnotation) => ResumeTemplateRegionAnnotation) => void;
}) {
  const field = annotation?.fieldId ? fields.find((item) => item.fieldId === annotation.fieldId) : null;
  if (!annotation) {
    return (
      <aside className="resume-annotation-panel">
        <div className="empty-state">选择一个高亮区域后编辑绑定字段、改写要求和证据要求</div>
      </aside>
    );
  }
  const canEdit = annotation.sourceType !== 'MANUAL_UNBOUND' && Boolean(annotation.fieldId);
  return (
    <aside className="resume-annotation-panel">
      <div className="resume-annotation-head">
        <strong>{field?.displayName || '未绑定区域'}</strong>
        <span>{annotation.sourceType} · {annotation.status}</span>
      </div>
      <label>
        <span>字段绑定</span>
        <select
          value={annotation.fieldId || ''}
          onChange={(event) => onChange(annotation.annotationId, (current) => ({
            ...current,
            fieldId: event.target.value || null,
            sourceType: event.target.value ? 'MANUAL_BOUND' : 'MANUAL_UNBOUND',
            editable: event.target.value ? current.editable : false,
            requiredEvidencePolicy: event.target.value ? (fields.find((item) => item.fieldId === event.target.value)?.requiredEvidencePolicy || current.requiredEvidencePolicy) : 'NONE'
          }))}
        >
          <option value="">未绑定字段</option>
          {fields.map((item) => <option key={item.fieldId} value={item.fieldId}>{item.displayName}</option>)}
        </select>
      </label>
      <label className={annotation.editable ? 'resume-toggle-row active' : 'resume-toggle-row'}>
        <input
          type="checkbox"
          checked={annotation.editable}
          disabled={!canEdit}
          onChange={(event) => onChange(annotation.annotationId, (current) => ({ ...current, editable: event.target.checked && canEdit }))}
        />
        <span>{annotation.editable ? '已允许 Agent 修改该字段' : '不允许 Agent 修改该字段'}</span>
      </label>
      <label>
        <span>section</span>
        <select value={annotation.sectionKey} onChange={(event) => onChange(annotation.annotationId, (current) => ({ ...current, sectionKey: event.target.value }))}>
          {Object.entries(sectionLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      <label>
        <span>证据要求</span>
        <select value={annotation.requiredEvidencePolicy} onChange={(event) => onChange(annotation.annotationId, (current) => ({ ...current, requiredEvidencePolicy: event.target.value }))}>
          <option value="NONE">不要求</option>
          <option value="OPTIONAL">可选</option>
          <option value="REQUIRED">必须</option>
        </select>
      </label>
      <label>
        <span>Agent 改写要求</span>
        <textarea
          value={annotation.userInstruction || ''}
          maxLength={500}
          onChange={(event) => onChange(annotation.annotationId, (current) => ({ ...current, userInstruction: event.target.value }))}
          placeholder="填写内容改写要求，不要写排版、定位、XML 或路径指令"
        />
      </label>
      <label>
        <span>状态</span>
        <select value={annotation.status} onChange={(event) => onChange(annotation.annotationId, (current) => ({ ...current, status: event.target.value }))}>
          <option value="ACTIVE">启用</option>
          <option value="IGNORED">忽略</option>
        </select>
      </label>
      <div className="resume-annotation-facts">
        <span>{field?.sourceText ? `原文：${field.sourceText.slice(0, 90)}` : '未绑定字段，仅作为视觉备注'}</span>
        <span>{annotation.fieldId && annotation.editable && annotation.status === 'ACTIVE' ? '保存后会作为 Agent 可修改边界' : '不会参与 Agent 自动修改'}</span>
      </div>
    </aside>
  );
}

function Legend() {
  const items = [
    ['auto', 'AUTO：系统识别字段'],
    ['manual-unbound', 'MANUAL_UNBOUND：手动备注，不会自动改'],
    ['manual-bound', 'MANUAL_BOUND：人工绑定字段'],
    ['confirmed-unused', 'CONFIRMED：允许 Agent 修改']
  ];
  return (
    <div className="resume-region-legend">
      {items.map(([className, label]) => <span key={className}><i className={className} />{label}</span>)}
    </div>
  );
}

function rectStyle(rect: { x: number; y: number; width: number; height: number }) {
  return {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.width * 100}%`,
    height: `${rect.height * 100}%`
  };
}

function draftToRect(region: DraftRegion) {
  const x = Math.min(region.startX, region.currentX);
  const y = Math.min(region.startY, region.currentY);
  return {
    x,
    y,
    width: Math.abs(region.currentX - region.startX),
    height: Math.abs(region.currentY - region.startY)
  };
}

function regionClass(annotation: ResumeTemplateRegionAnnotation, selectedId: string) {
  const classes = [annotation.sourceType === 'AUTO' ? 'auto' : annotation.sourceType === 'MANUAL_BOUND' ? 'manual-bound' : 'manual-unbound'];
  if (annotation.editable && annotation.status === 'ACTIVE') {
    classes.push('confirmed-unused');
  }
  if (annotation.annotationId === selectedId) {
    classes.push('selected');
  }
  if (annotation.status === 'IGNORED') {
    classes.push('ignored');
  }
  return classes.join(' ');
}

function regionTitle(annotation: ResumeTemplateRegionAnnotation, fieldsById: Map<string, ResumeTemplate['fields'][number]>) {
  const field = annotation.fieldId ? fieldsById.get(annotation.fieldId) : null;
  return field?.displayName || (annotation.sourceType === 'MANUAL_UNBOUND' ? '手动未绑定备注' : '未绑定区域');
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function stepActive(index: number, template: ResumeTemplate | null, preview: ResumeTemplatePreview | null, dirty: boolean, editableCount: number) {
  if (index === 0) return Boolean(template);
  if (index === 1) return Boolean(preview);
  if (index === 2) return editableCount > 0 && !dirty;
  return Boolean(template && preview && editableCount > 0 && !dirty);
}

function templateCanUse(template: ResumeTemplate) {
  return ['READY', 'EXPORTED'].includes(template.status);
}

function formatTemplateStatus(status: string) {
  if (status === 'READY') return '已解析';
  if (status === 'PARSING') return '解析中';
  if (status === 'EXPORTED') return '已导出';
  if (status === 'FAILED') return '解析失败';
  return status || '未知状态';
}

const sectionLabels: Record<string, string> = {
  personal_info: '个人信息',
  summary: '个人总结',
  education: '教育背景',
  work_experience: '工作经历',
  project_experience: '项目经历',
  skills: '技能',
  awards: '奖项',
  certifications: '证书',
  research: '科研',
  other: '其他'
};

function formatSection(section?: string) {
  return sectionLabels[section || 'other'] || section || '其他';
}

function formatTime(value?: string | null) {
  if (!value) return '暂无时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}
