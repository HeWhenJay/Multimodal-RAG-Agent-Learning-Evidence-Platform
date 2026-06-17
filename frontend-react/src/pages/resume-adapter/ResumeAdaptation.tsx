import { CheckCircle2, FileDiff, TriangleAlert, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchResumeAlignments } from '../../api/pageData';
import type { ResumeEvidenceAlignment } from '../../api/types';

// 简历适配页展示 JD 要求与简历证据的对齐情况。
export function ResumeAdaptation() {
  const [rows, setRows] = useState<ResumeEvidenceAlignment[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchResumeAlignments()
      .then(setRows)
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : '简历证据数据加载失败'));
  }, []);

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>简历适配</h2>
          <p>JD 要求、简历表述与真实证据对齐</p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3><FileDiff size={20} />证据对齐矩阵</h3>
          <span className="status-pill">复核模式</span>
        </div>
        <div className="resume-grid">
          {rows.map((item) => (
            <div className="resume-row" key={item.id}>
              <div>
                <strong>{item.requirement}</strong>
                <span>JD 要求</span>
              </div>
              <p>{item.evidence}</p>
              <StatusBadge status={item.status} />
            </div>
          ))}
          {rows.length === 0 ? <div className="empty-state">暂无简历证据对齐记录</div> : null}
        </div>
        {error ? <p className="form-message danger">{error}</p> : null}
      </section>
    </div>
  );
}

// 根据证据状态展示中文徽标。
function StatusBadge({ status }: { status: string }) {
  if (status === 'supported') {
    return <span className="evidence-status supported"><CheckCircle2 size={16} />证据充分</span>;
  }
  if (status === 'weak') {
    return <span className="evidence-status weak"><TriangleAlert size={16} />证据不足</span>;
  }
  return <span className="evidence-status missing"><XCircle size={16} />不建议写入</span>;
}
