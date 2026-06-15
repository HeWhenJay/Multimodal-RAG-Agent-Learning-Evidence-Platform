import { Bot, Clock, LockKeyhole, Route } from 'lucide-react';

export function AgentTasks() {
  return (
    <div className="page-stack">
      <section className="page-heading">
        <div>
          <h2>Agent 任务</h2>
          <p>当前阶段保留入口，正式编排不在本轮实现</p>
        </div>
        <div className="status-pill"><LockKeyhole size={15} />Not Started</div>
      </section>

      <section className="agent-placeholder">
        <article className="panel">
          <Bot size={34} />
          <h3>RAG 已就绪后再接入 Agent</h3>
          <p>下一阶段会在稳定证据检索、引用和质量反馈后，加入 JD 学习计划 Agent 与简历优化 Agent。</p>
        </article>
        <article className="panel">
          <Route size={30} />
          <h3>计划任务链路</h3>
          <div className="roadmap-row"><span>01</span><strong>检索证据</strong><p>按 JD、简历和学习资料过滤。</p></div>
          <div className="roadmap-row"><span>02</span><strong>生成计划</strong><p>输出能力缺口、学习顺序和引用来源。</p></div>
          <div className="roadmap-row"><span>03</span><strong>人工确认</strong><p>关键改写建议需要用户确认。</p></div>
        </article>
        <article className="panel">
          <Clock size={30} />
          <h3>当前可用能力</h3>
          <p>文档识别、递归切块、混合检索、RAG-Fusion 和证据引用。</p>
        </article>
      </section>
    </div>
  );
}

