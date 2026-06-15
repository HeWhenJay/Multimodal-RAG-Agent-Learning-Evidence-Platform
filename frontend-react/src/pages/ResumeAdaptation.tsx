import { CheckCircle2, FileDiff, TriangleAlert, XCircle } from 'lucide-react';

const rows = [
  ['RAG 项目经验', '实现 FastAPI RAG 服务、递归切块、BM25 + 向量混合检索', 'supported'],
  ['文档解析经验', '接入 MinerU 适配入口，当前本地降级解析可运行', 'weak'],
  ['Agent 编排能力', '第一阶段未实现 Agent 任务', 'missing']
];

export function ResumeAdaptation() {
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
          <span className="status-pill">Review Mode</span>
        </div>
        <div className="resume-grid">
          {rows.map(([requirement, evidence, status]) => (
            <div className="resume-row" key={requirement}>
              <div>
                <strong>{requirement}</strong>
                <span>JD Requirement</span>
              </div>
              <p>{evidence}</p>
              <StatusBadge status={status} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'supported') {
    return <span className="evidence-status supported"><CheckCircle2 size={16} />Evidence Supported</span>;
  }
  if (status === 'weak') {
    return <span className="evidence-status weak"><TriangleAlert size={16} />Insufficient Evidence</span>;
  }
  return <span className="evidence-status missing"><XCircle size={16} />Not Recommended</span>;
}

