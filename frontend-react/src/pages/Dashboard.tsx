import {
  Anchor,
  BarChart3,
  Bot,
  CheckCircle2,
  CloudUpload,
  Database,
  FileText,
  Flag,
  LibraryBig,
  PlayCircle,
  Search,
  Send,
  TriangleAlert,
  Video
} from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { fetchOverview, queryRag } from '../api/rag';
import type { RagEvidence, RagOverview } from '../api/types';

const stats = [
  { label: '已索引材料', value: '128', delta: '+12', note: '本周新增', icon: LibraryBig },
  { label: '视频片段', value: '456', delta: '+45', note: '本周新增', icon: Video },
  { label: 'RAG 证据锚点', value: '1.2k', delta: '98%', note: '命中率', icon: Anchor },
  { label: '运行中 Agent', value: '0', delta: 'RAG', note: '第一阶段', icon: Bot }
];

export function Dashboard() {
  const [overview, setOverview] = useState<RagOverview | null>(null);
  const [question, setQuestion] = useState('如何处理微服务架构中的分布式事务？');
  const [answer, setAnswer] = useState('在微服务架构中处理分布式事务通常有几种模式：两阶段提交、TCC、以及基于消息的最终一致性。当前第一阶段回答会优先展示 RAG 证据。');
  const [evidences, setEvidences] = useState<RagEvidence[]>([]);

  useEffect(() => {
    fetchOverview().then(setOverview).catch(() => undefined);
  }, []);

  async function runQuery() {
    const result = await queryRag({ question, topK: 3 });
    setAnswer(result.answer);
    setEvidences(result.evidences);
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>Agent 工作台</h2>
          <p>系统全局监控与多模态证据处理中心</p>
        </div>
        <div className="status-pill indexed">
          <Database size={15} />
          RAG Ready
        </div>
      </section>

      <section className="metric-grid">
        {stats.map((stat, index) => (
          <article className="metric-card" key={stat.label}>
            <div>
              <p>{stat.label}</p>
              <h3>{index === 0 && overview ? overview.materialCount : index === 2 && overview ? overview.evidenceCount : stat.value}</h3>
              <span>
                <strong>{index === 1 && overview ? overview.chunkCount : stat.delta}</strong>
                {stat.note}
              </span>
            </div>
            <div className="metric-icon">
              <stat.icon size={24} />
            </div>
          </article>
        ))}
      </section>

      <section className="dashboard-grid">
        <article className="panel wide">
          <div className="panel-title">
            <h3>
              <Search size={20} />
              知识库智能检索 (RAG)
            </h3>
            <button className="chip-button">高级检索模式</button>
          </div>
          <div className="rag-input-row">
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
            <button className="send-button" onClick={runQuery} aria-label="发送问题">
              <Send size={18} />
            </button>
          </div>
          <div className="answer-box">
            <div className="answer-label">
              <Bot size={17} />
              RAG 回复
            </div>
            <p>{answer}</p>
            <div className="citation-row">
              {evidences.length === 0 ? (
                <>
                  <span><FileText size={15} />[Doc A, p.24]</span>
                  <span><PlayCircle size={15} />[Video B, 05:20]</span>
                </>
              ) : (
                evidences.slice(0, 3).map((item) => (
                  <span key={item.evidenceId}>
                    <FileText size={15} />
                    [{item.title} / {item.sectionName}]
                  </span>
                ))
              )}
            </div>
          </div>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <CloudUpload size={20} />
              多模态数据接入通道
            </h3>
          </div>
          <div className="upload-zone">
            <CloudUpload size={28} />
            <strong>拖拽文件或点击上传</strong>
            <div className="format-row">
              {['PDF', 'DOCX', 'PPTX', 'MP4', 'MD'].map((format) => <span key={format}>{format}</span>)}
            </div>
          </div>
          <h4>近期处理任务</h4>
          <div className="task-row">
            <FileText size={20} />
            <span>System Design.pdf</span>
            <strong>100% Indexed</strong>
          </div>
          <div className="task-row">
            <Video size={20} />
            <span>Java Concurrency.mp4</span>
            <strong className="processing">65% OCR/ASR</strong>
          </div>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <BarChart3 size={20} />
              岗位适配分析
            </h3>
          </div>
          <label className="field-label">目标岗位描述 (JD) 输入</label>
          <textarea className="compact-textarea" placeholder="Paste Job Description here..." />
          <button className="full-action">
            <BarChart3 size={17} />
            运行适配分析
          </button>
          <h4>能力雷达匹配度</h4>
          <div className="stacked-bar" aria-label="能力匹配度">
            <span className="mastered" style={{ width: '60%' }}>Mastered</span>
            <span className="partial" style={{ width: '25%' }}>Partial</span>
            <span className="gaps" style={{ width: '15%' }}>Gaps</span>
          </div>
          <div className="plan-note">
            <Flag size={17} />
            <span>下一步学习计划：补充 Kubernetes 集群调度理论，优先复习云原生架构资料。</span>
          </div>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <Video size={20} />
              视频知识切片回顾
            </h3>
          </div>
          {[
            ['Java 并发编程核心原理解析', 'JVM Memory Model', '01:23:10 - 01:25:42'],
            ['分布式系统架构设计 (B站录播)', 'CAP 定理与实践', '00:45:22 - 00:48:15']
          ].map(([title, fragment, timeline]) => (
            <div className="video-slice" key={title}>
              <div className="play-badge"><PlayCircle size={18} /></div>
              <div>
                <h4>{title}</h4>
                <span>知识命中</span>
                <p>Fragment: {fragment}</p>
                <p>Timeline: {timeline}</p>
              </div>
            </div>
          ))}
        </article>

        <article className="panel wide">
          <div className="panel-title">
            <h3>
              <FileText size={20} />
              简历证据对齐 (JD vs Resume)
            </h3>
            <span className="status-pill">Review Mode</span>
          </div>
          <div className="evidence-table">
            <div className="table-head">JD Requirement</div>
            <div className="table-head">Resume Evidence</div>
            <div className="table-head">Status</div>
            {[
              ['Kubernetes Experience', '"Lead k8s migration project for 50+ microservices..."', 'supported'],
              ['High Concurrency Tuning', '"Participated in performance testing..."', 'weak'],
              ['React / Frontend Skills', 'No relevant entries found.', 'missing']
            ].map(([requirement, evidence, status]) => (
              <Fragment key={requirement}>
                <div>
                  <strong>{requirement}</strong>
                  <p>Require hands-on production evidence.</p>
                </div>
                <div>{evidence}</div>
                <div>
                  <StatusIcon status={status} />
                </div>
              </Fragment>
            ))}
          </div>
        </article>
      </section>
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'supported') {
    return <span className="evidence-status supported"><CheckCircle2 size={16} />Evidence Supported</span>;
  }
  if (status === 'weak') {
    return <span className="evidence-status weak"><TriangleAlert size={16} />Insufficient Evidence</span>;
  }
  return <span className="evidence-status missing">Not Recommended</span>;
}
