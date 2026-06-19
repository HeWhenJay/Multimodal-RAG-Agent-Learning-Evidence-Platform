import type { ReactNode } from 'react';

interface MarkdownTextProps {
  content: string;
  className?: string;
}

// 渲染后端 RAG 回答中的 Markdown 子集，避免把模型文本作为 HTML 注入页面。
export function MarkdownText({ content, className = '' }: MarkdownTextProps) {
  const blocks = renderMarkdownBlocks(content || '');
  return <div className={`markdown-text ${className}`.trim()}>{blocks}</div>;
}

// 将 Markdown 行拆成标题、段落、列表、引用和代码块。
function renderMarkdownBlocks(content: string) {
  const lines = normalizeGeneratedMarkdown(content).split('\n');
  const blocks: ReactNode[] = [];
  let paragraphLines: string[] = [];
  let listItems: string[] = [];
  let orderedList = false;
  let inCodeBlock = false;
  let codeLines: string[] = [];

  function flushParagraph() {
    if (!paragraphLines.length) return;
    const text = paragraphLines.join(' ');
    blocks.push(<p key={`p-${blocks.length}`}>{renderInlineMarkdown(text)}</p>);
    paragraphLines = [];
  }

  function flushList() {
    if (!listItems.length) return;
    const Tag = orderedList ? 'ol' : 'ul';
    blocks.push(
      <Tag key={`list-${blocks.length}`}>
        {listItems.map((item, index) => <li key={`${index}-${item}`}>{renderInlineMarkdown(item)}</li>)}
      </Tag>
    );
    listItems = [];
    orderedList = false;
  }

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith('```')) {
      if (inCodeBlock) {
        blocks.push(<pre key={`code-${blocks.length}`}><code>{codeLines.join('\n')}</code></pre>);
        codeLines = [];
        inCodeBlock = false;
        return;
      }
      flushParagraph();
      flushList();
      inCodeBlock = true;
      codeLines = [];
      return;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }

    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1].length + 3, 6);
      blocks.push(renderHeading(level, heading[2], `heading-${blocks.length}`));
      return;
    }

    const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed);
    const unordered = /^[-*+]\s+(.+)$/.exec(trimmed);
    if (ordered || unordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      if (listItems.length && orderedList !== isOrdered) {
        flushList();
      }
      orderedList = isOrdered;
      listItems.push((ordered || unordered)?.[1] || trimmed);
      return;
    }

    const quote = /^>\s?(.+)$/.exec(trimmed);
    if (quote) {
      flushParagraph();
      flushList();
      blocks.push(<blockquote key={`quote-${blocks.length}`}>{renderInlineMarkdown(quote[1])}</blockquote>);
      return;
    }

    paragraphLines.push(trimmed);
  });

  if (inCodeBlock) {
    blocks.push(<pre key={`code-${blocks.length}`}><code>{codeLines.join('\n')}</code></pre>);
  }
  flushParagraph();
  flushList();

  return blocks.length ? blocks : [<p key="empty">暂无内容</p>];
}

// 模型有时把列表压成一行，这里只做保守换行，避免影响普通句子。
function normalizeGeneratedMarkdown(content: string) {
  return content
    .replace(/\r\n/g, '\n')
    .replace(/([。；;:：])\s+(\d+[.)]\s+\*\*)/g, '$1\n$2')
    .replace(/\s+(-\s+\u2757\s*)/g, '\n$1');
}

// 显式选择 HTML 标题标签，避免动态 JSX 标签被全局 Three 类型误判。
function renderHeading(level: number, text: string, key: string) {
  if (level <= 4) {
    return <h4 key={key}>{renderInlineMarkdown(text)}</h4>;
  }
  if (level === 5) {
    return <h5 key={key}>{renderInlineMarkdown(text)}</h5>;
  }
  return <h6 key={key}>{renderInlineMarkdown(text)}</h6>;
}

// 渲染常见内联语法：链接、证据 ID、加粗、代码和简易数学片段。
function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\[evidenceId=([^\]]+)])|(\[([^\]]+)]\(([^)]+)\))|(`([^`]+)`)|(\*\*([^*]+)\*\*)|(\$([^$\n]+)\$)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const key = `${match.index}-${match[0]}`;
    if (match[2]) {
      nodes.push(<span className="markdown-evidence" key={key}>{match[2]}</span>);
    } else if (match[4] && match[5]) {
      const href = normalizeMarkdownHref(match[5]);
      nodes.push(href
        ? <a key={key} href={href} target={href.startsWith('http') ? '_blank' : undefined} rel="noreferrer">{renderInlineMarkdown(match[4])}</a>
        : <span key={key}>{renderInlineMarkdown(match[4])}</span>);
    } else if (match[7]) {
      nodes.push(<code key={key}>{match[7]}</code>);
    } else if (match[9]) {
      nodes.push(<strong key={key}>{renderInlineMarkdown(match[9])}</strong>);
    } else if (match[11]) {
      nodes.push(<span className="markdown-math" key={key}>{match[11]}</span>);
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

// 只允许常规站内、锚点和 http(s) 链接。
function normalizeMarkdownHref(rawHref: string) {
  const href = rawHref.trim().split(/\s+/)[0].replace(/^<|>$/g, '');
  if (/^(https?:\/\/|#|\/)/i.test(href)) {
    return href;
  }
  return '';
}
