import { BarChart3, BrainCircuit, CheckCircle2, ClipboardList, Flag, TriangleAlert } from 'lucide-react';
import { useState } from 'react';

export function JdAnalysis() {
  const [jd, setJd] = useState('要求熟悉 Java、Spring Boot、RAG、向量数据库、文档解析，有 AI 应用项目经验。');

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>JD 分析</h2>
          <p>岗位要求解析、知识证据匹配与学习计划摘要</p>
        </div>
        <div className="status-pill"><BrainCircuit size={15} />RAG First</div>
      </section>

      <section className="two-column">
        <article className="panel">
          <div className="panel-title">
            <h3><ClipboardList size={20} />目标岗位描述</h3>
          </div>
          <textarea className="material-textarea" value={jd} onChange={(event) => setJd(event.target.value)} />
          <button className="full-action">
            <BarChart3 size={17} />
            运行适配分析
          </button>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>能力雷达匹配度</h3>
            <span className="status-pill indexed">72%</span>
          </div>
          <div className="stacked-bar large">
            <span className="mastered" style={{ width: '58%' }}>已掌握</span>
            <span className="partial" style={{ width: '27%' }}>待强化</span>
            <span className="gaps" style={{ width: '15%' }}>缺口</span>
          </div>
          <div className="skill-list">
            <span><CheckCircle2 size={16} />Java / Spring Boot</span>
            <span><CheckCircle2 size={16} />RAG 基础链路</span>
            <span><TriangleAlert size={16} />MinerU 生产部署</span>
            <span><TriangleAlert size={16} />向量库评估指标</span>
          </div>
        </article>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3><Flag size={20} />下一步学习计划</h3>
        </div>
        <div className="roadmap-row">
          <span>01</span>
          <strong>补齐 MinerU 批处理与失败重试</strong>
          <p>围绕 PDF、PPTX、扫描件建立解析质量记录。</p>
        </div>
        <div className="roadmap-row">
          <span>02</span>
          <strong>补齐混合检索评估</strong>
          <p>记录 Recall@K、MRR、引用命中率和人工反馈。</p>
        </div>
        <div className="roadmap-row">
          <span>03</span>
          <strong>准备项目证据表达</strong>
          <p>把 RAG、Java、Python 服务边界写成可验证项目经历。</p>
        </div>
      </section>
    </div>
  );
}

