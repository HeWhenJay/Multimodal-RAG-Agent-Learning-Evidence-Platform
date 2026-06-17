import { BarChart3, BrainCircuit, CheckCircle2, ClipboardList, Flag, Loader2, TriangleAlert } from 'lucide-react';
import { useEffect, useState } from 'react';
import { analyzeJd, fetchJdAnalysis } from '../../api/pageData';
import type { JdAnalysis as JdAnalysisData } from '../../api/types';

// JD 分析页展示岗位描述输入、能力匹配和学习计划摘要。
export function JdAnalysis() {
  const [analysis, setAnalysis] = useState<JdAnalysisData | null>(null);
  const [jobDescription, setJobDescription] = useState('');
  const [resumeText, setResumeText] = useState('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    fetchJdAnalysis()
      .then((data) => {
        setAnalysis(data);
        setJobDescription(data?.jobDescription || '');
      })
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : 'JD 分析数据加载失败'));
  }, []);

  // 提交 JD 和简历文本，生成并保存一次新的 RAG 适配分析。
  async function submitAnalysis() {
    if (!jobDescription.trim()) {
      setError('请输入岗位描述');
      return;
    }
    setRunning(true);
    setError('');
    setMessage('');
    try {
      const result = await analyzeJd({ jobDescription, resumeText });
      setAnalysis(result);
      setMessage('JD 适配分析已保存');
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'JD 适配分析失败');
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>JD 分析</h2>
          <p>岗位要求解析、知识证据匹配与学习计划摘要</p>
        </div>
        <div className="status-pill"><BrainCircuit size={15} />RAG 优先</div>
      </section>

      <section className="two-column">
        <article className="panel">
          <div className="panel-title">
            <h3><ClipboardList size={20} />目标岗位描述</h3>
          </div>
          <label className="field-label">岗位描述</label>
          <textarea className="material-textarea" value={jobDescription} onChange={(event) => setJobDescription(event.target.value)} placeholder="粘贴目标岗位 JD" />
          <label className="field-label">简历文本</label>
          <textarea className="compact-textarea" value={resumeText} onChange={(event) => setResumeText(event.target.value)} placeholder="粘贴简历摘要或项目经历" />
          <button className="full-action" onClick={submitAnalysis} disabled={running}>
            {running ? <Loader2 className="spin" size={17} /> : <BarChart3 size={17} />}
            运行 RAG 适配分析
          </button>
          {message ? <p className="form-message">{message}</p> : null}
          {error ? <p className="form-message danger">{error}</p> : null}
        </article>

        <article className="panel">
          <div className="panel-title">
            <h3>能力雷达匹配度</h3>
            <span className="status-pill indexed">{analysis?.matchScore ?? 0}%</span>
          </div>
          <div className="stacked-bar large">
            <span className="mastered" style={{ width: `${analysis?.masteredPercent || 0}%` }}>已掌握</span>
            <span className="partial" style={{ width: `${analysis?.partialPercent || 0}%` }}>待强化</span>
            <span className="gaps" style={{ width: `${analysis?.gapPercent || 0}%` }}>缺口</span>
          </div>
          <div className="skill-list">
            {(analysis?.skills || []).map((skill) => (
              <span key={skill.id}>
                {skill.status === 'supported' ? <CheckCircle2 size={16} /> : <TriangleAlert size={16} />}
                {skill.skillName}
              </span>
            ))}
            {!analysis ? <span>暂无技能匹配记录</span> : null}
          </div>
        </article>
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3><Flag size={20} />下一步学习计划</h3>
        </div>
        {(analysis?.learningPlan || []).map((item) => (
          <div className="roadmap-row" key={item.id}>
            <span>{String(item.stepNo).padStart(2, '0')}</span>
            <strong>{item.title}</strong>
            <p>{item.description}</p>
          </div>
        ))}
        {!analysis ? <div className="empty-state">暂无学习计划记录</div> : null}
      </section>
    </div>
  );
}
