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
import { useEffect, useState } from 'react';
import { fetchDashboardData } from '../api/pageData';
import { queryRag } from '../api/rag';
import type { DashboardData, RagEvidence } from '../api/types';

// 工作台首页展示 RAG 概览、检索入口和证据对齐摘要。
export function Dashboard() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [evidences, setEvidences] = useState<RagEvidence[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchDashboardData().then(setDashboard).catch((loadError) => {
      setError(loadError instanceof Error ? loadError.message : '工作台数据加载失败');
    });
  }, []);

  // 执行一次 RAG 检索并刷新回答与证据列表。
  async function runQuery() {
    if (!question.trim()) {
      setError('请输入检索问题');
      return;
    }
    setError('');
    const result = await queryRag({ question, topK: 3 });
    setAnswer(result.answer);
    setEvidences(result.evidences);
  }

  const stats = [
    { label: '已入库材料', value: dashboard?.materialCount ?? 0, delta: dashboard?.materialDelta7Days ?? 0, note: '本周新增', icon: LibraryBig },
    { label: '视频片段', value: dashboard?.videoSliceCount ?? 0, delta: dashboard?.videoSliceDelta7Days ?? 0, note: '本周新增', icon: Video },
    { label: 'RAG 证据锚点', value: dashboard?.evidenceCount ?? 0, delta: dashboard?.evidenceCount ?? 0, note: '当前切块', icon: Anchor },
    { label: '待处理错误', value: dashboard?.openErrorCount ?? 0, delta: dashboard?.errorCount30Days ?? 0, note: '近 30 天错误', icon: Bot }
  ];

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>Agent 工作台</h2>
          <p>系统全局监控与多模态证据处理中心</p>
        </div>
        <div className="status-pill indexed">
          <Database size={15} />
          RAG 已就绪
        </div>
      </section>

      <section className="metric-grid">
        {stats.map((stat, index) => (
          <article className="metric-card" key={stat.label}>
            <div>
              <p>{stat.label}</p>
              <h3>{formatNumber(stat.value)}</h3>
              <span>
                <strong>{index < 2 ? `+${stat.delta}` : formatNumber(stat.delta)}</strong>
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
            <p>{answer || '提交问题后展示基于数据库证据检索生成的回答。'}</p>
            <div className="citation-row">
              {evidences.length > 0 ? (
                evidences.slice(0, 3).map((item) => (
                  <span key={item.evidenceId}>
                    <FileText size={15} />
                    [{item.title} / {item.sectionTitle || item.sectionName}]
                  </span>
                ))
              ) : <span><FileText size={15} />暂无证据引用</span>}
            </div>
          </div>
          {error ? <p className="form-message danger">{error}</p> : null}
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
          {(dashboard?.recentMaterials || []).map((item) => (
            <div className="task-row" key={item.id}>
              <FileText size={20} />
              <span>{item.title}</span>
              <strong className={item.status === 'READY' ? '' : 'processing'}>{formatMaterialStatus(item.status)}</strong>
            </div>
          ))}
          {(dashboard?.recentMaterials || []).length === 0 ? <div className="empty-state">暂无资料处理任务</div> : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <BarChart3 size={20} />
              岗位适配分析
            </h3>
          </div>
          <label className="field-label">目标岗位描述 (JD) 输入</label>
          <textarea className="compact-textarea" value={dashboard?.latestJdAnalysis?.jobDescription || ''} readOnly placeholder="暂无 JD 分析记录" />
          <button className="full-action">
            <BarChart3 size={17} />
            查看适配分析
          </button>
          <h4>能力雷达匹配度</h4>
          <div className="stacked-bar" aria-label="能力匹配度">
            <span className="mastered" style={{ width: `${dashboard?.latestJdAnalysis?.masteredPercent || 0}%` }}>已掌握</span>
            <span className="partial" style={{ width: `${dashboard?.latestJdAnalysis?.partialPercent || 0}%` }}>待强化</span>
            <span className="gaps" style={{ width: `${dashboard?.latestJdAnalysis?.gapPercent || 0}%` }}>缺口</span>
          </div>
          <div className="plan-note">
            <Flag size={17} />
            <span>下一步学习计划：{dashboard?.latestJdAnalysis?.learningPlan?.[0]?.title || '暂无学习计划'}</span>
          </div>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>
              <Video size={20} />
              视频知识切片回顾
            </h3>
          </div>
          {(dashboard?.recentVideoSlices || []).map((item) => (
            <div className="video-slice" key={item.id}>
              <div className="play-badge"><PlayCircle size={18} /></div>
              <div>
                <h4>{item.title}</h4>
                <span>知识命中</span>
                <p>知识片段：{item.topic}</p>
                <p>时间范围：{item.startTime} - {item.endTime}</p>
              </div>
            </div>
          ))}
          {(dashboard?.recentVideoSlices || []).length === 0 ? <div className="empty-state">暂无视频切片</div> : null}
        </article>

        <article className="panel wide">
          <div className="panel-title">
            <h3>
              <FileText size={20} />
              简历证据对齐 (JD 与简历)
            </h3>
            <span className="status-pill">复核模式</span>
          </div>
          <div className="evidence-stack">
            {(dashboard?.resumeAlignments || []).map((item) => (
              <div className="evidence-item" key={item.id}>
                <div className="evidence-field">
                  <span className="evidence-field-label">JD 要求</span>
                  <strong>{item.requirement}</strong>
                </div>
                <div className="evidence-field">
                  <span className="evidence-field-label">简历证据</span>
                  <p>{item.evidence}</p>
                </div>
                <div className="evidence-field">
                  <span className="evidence-field-label">状态</span>
                  <StatusIcon status={item.status} />
                </div>
              </div>
            ))}
            {(dashboard?.resumeAlignments || []).length === 0 ? <div className="empty-state">暂无简历证据对齐记录</div> : null}
          </div>
        </article>
      </section>
    </div>
  );
}

// 格式化统计数字展示。
function formatNumber(value: number) {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}k`;
  }
  return String(value);
}

// 将资料状态转换为中文展示。
function formatMaterialStatus(status: string) {
  if (status === 'READY') return '已入库';
  if (status === 'PARTIAL') return '部分完成';
  if (status === 'PARSING') return '解析中';
  if (status === 'PENDING') return '等待解析';
  if (status === 'FAILED') return '解析失败';
  return status;
}

// 根据适配状态展示对应的中文状态标记。
function StatusIcon({ status }: { status: string }) {
  if (status === 'supported') {
    return <span className="evidence-status supported"><CheckCircle2 size={16} />证据充分</span>;
  }
  if (status === 'weak') {
    return <span className="evidence-status weak"><TriangleAlert size={16} />证据不足</span>;
  }
  return <span className="evidence-status missing">不建议写入</span>;
}
