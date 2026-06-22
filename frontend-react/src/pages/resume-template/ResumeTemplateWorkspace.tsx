import { CheckCircle2, Download, FileText, Loader2, ShieldCheck, TriangleAlert, Upload, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { exportResumeTemplate, generateResumePatches, uploadResumeTemplate, validateResumePatches } from '../../api/rag';
import type { ResumeContentPatch, ResumePatchDraft, ResumeTemplate, ResumeTemplateExport } from '../../api/types';

// 简历模板页负责字段级补丁确认和确定性 DOCX 导出。
export function ResumeTemplateWorkspace() {
  const [template, setTemplate] = useState<ResumeTemplate | null>(null);
  const [draft, setDraft] = useState<ResumePatchDraft | null>(null);
  const [exportResult, setExportResult] = useState<ResumeTemplateExport | null>(null);
  const [jobDescription, setJobDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const confirmedCount = useMemo(() => (draft?.patches || []).filter((patch) => patch.status === 'CONFIRMED').length, [draft]);

  // 上传 DOCX 后解析字段绑定。
  async function submitTemplate(file: File | null) {
    if (!file) return;
    setBusy(true);
    setError('');
    setMessage('');
    setDraft(null);
    setExportResult(null);
    try {
      const result = await uploadResumeTemplate(file);
      setTemplate(result);
      setMessage('简历模板字段解析完成');
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '简历模板解析失败');
    } finally {
      setBusy(false);
    }
  }

  // 基于 JD 生成字段级补丁草稿。
  async function submitGenerate() {
    if (!template) {
      setError('请先上传并解析简历模板');
      return;
    }
    if (!jobDescription.trim()) {
      setError('请输入岗位 JD');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('');
    setExportResult(null);
    try {
      const result = await generateResumePatches(template.templateId, {
        version: template.version,
        jobDescription,
        topK: 5
      });
      setDraft(result);
      setMessage(result.validationErrors.length ? '补丁草稿已生成，但存在校验提示' : '补丁草稿已生成');
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : '补丁草稿生成失败');
    } finally {
      setBusy(false);
    }
  }

  // 更新单条补丁状态或内容。
  function updatePatch(fieldId: string, updater: (patch: ResumeContentPatch) => ResumeContentPatch) {
    setDraft((previous) => {
      if (!previous) return previous;
      return {
        ...previous,
        patches: previous.patches.map((patch) => patch.fieldId === fieldId ? updater(patch) : patch)
      };
    });
  }

  // 校验当前用户确认的补丁。
  async function submitValidate() {
    if (!template || !draft) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await validateResumePatches(template.templateId, {
        version: template.version,
        patchDraftId: draft.patchDraftId,
        patches: draft.patches
      });
      setDraft(result);
      setMessage(result.validationErrors.length ? '补丁仍有校验问题' : '补丁已通过校验');
    } catch (validateError) {
      setError(validateError instanceof Error ? validateError.message : '补丁校验失败');
    } finally {
      setBusy(false);
    }
  }

  // 导出确认后的 DOCX 新版本。
  async function submitExport() {
    if (!template || !draft) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await exportResumeTemplate(template.templateId, {
        version: template.version,
        patchDraftId: draft.patchDraftId,
        idempotencyKey: `${template.templateId}-${draft.patchDraftId}-${confirmedCount}`
      });
      setExportResult(result);
      setMessage('简历 DOCX 新版本已导出');
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : '简历导出失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>简历模板</h2>
          <p>字段级内容补丁、人工确认与确定性 DOCX 导出</p>
        </div>
        <span className="status-pill"><ShieldCheck size={15} />排版保护</span>
      </section>

      <section className="resume-template-grid">
        <article className="panel">
          <div className="panel-title">
            <h3><Upload size={20} />模板解析</h3>
            <span className="status-pill">{template ? formatTemplateStatus(template.status) : '未上传'}</span>
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
            </div>
          ) : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>岗位 JD</h3>
            <span className="status-pill">{draft?.provider || '等待生成'}</span>
          </div>
          <textarea
            className="resume-template-jd"
            value={jobDescription}
            onChange={(event) => setJobDescription(event.target.value)}
            placeholder="粘贴目标岗位 JD，系统会检索当前用户 evidence 并生成字段级补丁草稿"
          />
          <button className="full-action" onClick={() => void submitGenerate()} disabled={busy || !template}>
            {busy ? <Loader2 className="spin" size={17} /> : <FileText size={17} />}
            生成补丁草稿
          </button>
        </article>
      </section>

      {message ? <p className="form-message">{message}</p> : null}
      {error ? <p className="form-message danger">{error}</p> : null}

      <section className="panel">
        <div className="panel-title">
          <h3>字段补丁确认</h3>
          <span className="status-pill">{confirmedCount}/{draft?.patches.length || 0} 已确认</span>
        </div>
        {template && draft ? (
          <div className="resume-patch-list">
            {draft.patches.map((patch) => {
              const field = template.fields.find((item) => item.fieldId === patch.fieldId);
              return (
                <div className="resume-patch-row" key={patch.fieldId}>
                  <div className="resume-patch-head">
                    <div>
                      <strong>{field?.displayName || patch.fieldId}</strong>
                      <span>{formatSection(field?.sectionKey)} · {patch.confidence ? `${Math.round(patch.confidence * 100)}%` : '待评估'}</span>
                    </div>
                    <PatchStatus status={patch.status} />
                  </div>
                  <div className="resume-patch-compare">
                    <label>
                      <span>原文</span>
                      <p>{field?.sourceText || '字段原文不可用'}</p>
                    </label>
                    <label>
                      <span>新文本</span>
                      <textarea
                        value={patch.newText}
                        onChange={(event) => updatePatch(patch.fieldId, (current) => ({ ...current, newText: event.target.value, status: 'DRAFT' }))}
                      />
                    </label>
                  </div>
                  <p className="resume-patch-reason">{patch.rewriteReason}</p>
                  <div className="resume-risk-row">
                    {patch.riskFlags.map((flag) => <span key={flag} className={flag === 'NONE' ? 'risk-ok' : 'risk-warn'}>{formatRisk(flag)}</span>)}
                    {patch.evidenceIds.map((id) => <span key={id}>证据 {id}</span>)}
                  </div>
                  <div className="resume-patch-actions">
                    <button className="chip-button" onClick={() => updatePatch(patch.fieldId, (current) => ({ ...current, status: 'CONFIRMED' }))}>
                      <CheckCircle2 size={16} />确认
                    </button>
                    <button className="chip-button" onClick={() => updatePatch(patch.fieldId, (current) => ({ ...current, status: 'REJECTED' }))}>
                      <XCircle size={16} />拒绝
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">上传模板并生成补丁草稿后，在这里逐条确认内容修改</div>
        )}
        {draft?.validationErrors.length ? (
          <div className="resume-validation-errors">
            {draft.validationErrors.map((item) => <span key={item}><TriangleAlert size={15} />{item}</span>)}
          </div>
        ) : null}
        <div className="resume-template-actions">
          <button className="ghost-action" onClick={() => void submitValidate()} disabled={busy || !draft}>
            <ShieldCheck size={17} />校验补丁
          </button>
          <button className="primary-action" onClick={() => void submitExport()} disabled={busy || !draft || confirmedCount === 0}>
            <Download size={17} />导出 DOCX
          </button>
        </div>
      </section>

      {exportResult ? (
        <section className="panel">
          <div className="panel-title">
            <h3><Download size={20} />导出结果</h3>
            <span className="status-pill indexed">{exportResult.status}</span>
          </div>
          <div className="resume-export-result">
            <strong>{exportResult.filename}</strong>
            <span>版本 {exportResult.baseVersion} → {exportResult.exportVersion}</span>
            <p>{exportResult.publicUrl || exportResult.filePath}</p>
            <p>{String(exportResult.layoutValidation?.message || 'XML 结构 fingerprint 已通过校验')}</p>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function PatchStatus({ status }: { status: string }) {
  if (status === 'CONFIRMED' || status === 'VALIDATED') {
    return <span className="evidence-status supported"><CheckCircle2 size={15} />{formatPatchStatus(status)}</span>;
  }
  if (status === 'REJECTED') {
    return <span className="evidence-status missing"><XCircle size={15} />已拒绝</span>;
  }
  return <span className="evidence-status weak"><TriangleAlert size={15} />{formatPatchStatus(status)}</span>;
}

function formatTemplateStatus(status: string) {
  if (status === 'READY') return '已解析';
  if (status === 'PARSING') return '解析中';
  if (status === 'EXPORTED') return '已导出';
  if (status === 'FAILED') return '解析失败';
  return status;
}

function formatPatchStatus(status: string) {
  if (status === 'DRAFT') return '待确认';
  if (status === 'VALIDATED') return '已校验';
  if (status === 'CONFIRMED') return '已确认';
  if (status === 'EXPORTED') return '已导出';
  return status;
}

function formatSection(section?: string) {
  const labels: Record<string, string> = {
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
  return labels[section || 'other'] || section || '其他';
}

function formatRisk(flag: string) {
  const labels: Record<string, string> = {
    NONE: '无风险',
    MISSING_EVIDENCE: '缺少证据',
    LOW_CONFIDENCE: '低置信度',
    OVER_LENGTH: '长度风险',
    LAYOUT_RISK: '版式风险',
    SENSITIVE_INFO: '敏感信息',
    UNSUPPORTED_REGION: '不支持区域',
    INJECTION_RISK: '注入风险'
  };
  return labels[flag] || flag;
}
