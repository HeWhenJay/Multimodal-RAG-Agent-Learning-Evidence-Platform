import type { ReactNode } from 'react';

interface MarkdownTextProps {
  content: string;
  className?: string;
  rewriteHref?: (href: string, contextText?: string) => string;
}

// 渲染后端 RAG 回答中的 Markdown 子集，避免把模型文本作为 HTML 注入页面。
export function MarkdownText({ content, className = '', rewriteHref }: MarkdownTextProps) {
  const blocks = renderMarkdownBlocks(content || '', rewriteHref);
  return <div className={`markdown-text ${className}`.trim()}>{blocks}</div>;
}

// 将 Markdown 行拆成标题、段落、列表、引用和代码块。
function renderMarkdownBlocks(content: string, rewriteHref?: (href: string, contextText?: string) => string) {
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
    blocks.push(<p key={`p-${blocks.length}`}>{renderInlineMarkdown(text, text, rewriteHref)}</p>);
    paragraphLines = [];
  }

  function flushList() {
    if (!listItems.length) return;
    const Tag = orderedList ? 'ol' : 'ul';
    blocks.push(
      <Tag key={`list-${blocks.length}`}>
        {listItems.map((item, index) => <li key={`${index}-${item}`}>{renderInlineMarkdown(item, item, rewriteHref)}</li>)}
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
      blocks.push(renderHeading(level, heading[2], `heading-${blocks.length}`, rewriteHref));
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
      blocks.push(<blockquote key={`quote-${blocks.length}`}>{renderInlineMarkdown(quote[1], quote[1], rewriteHref)}</blockquote>);
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
function renderHeading(level: number, text: string, key: string, rewriteHref?: (href: string, contextText?: string) => string) {
  if (level <= 4) {
    return <h4 key={key}>{renderInlineMarkdown(text, text, rewriteHref)}</h4>;
  }
  if (level === 5) {
    return <h5 key={key}>{renderInlineMarkdown(text, text, rewriteHref)}</h5>;
  }
  return <h6 key={key}>{renderInlineMarkdown(text, text, rewriteHref)}</h6>;
}

// 渲染常见内联语法：链接、证据 ID、加粗、代码和简易数学片段。
function renderInlineMarkdown(text: string, contextText = text, rewriteHref?: (href: string, contextText?: string) => string): ReactNode[] {
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
      const href = normalizeMarkdownHref(match[5], contextText, rewriteHref);
      nodes.push(href
        ? <a key={key} href={href} target={isExternalOrPreviewHref(href) ? '_blank' : undefined} rel="noreferrer">{renderInlineMarkdown(match[4], contextText, rewriteHref)}</a>
        : <span key={key}>{renderInlineMarkdown(match[4], contextText, rewriteHref)}</span>);
    } else if (match[7]) {
      nodes.push(<code key={key}>{match[7]}</code>);
    } else if (match[9]) {
      nodes.push(<strong key={key}>{renderInlineMarkdown(match[9], contextText, rewriteHref)}</strong>);
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

// 只允许常规站内页面和 http(s) 链接；原 Markdown 目录锚点不对应当前应用目标。
function normalizeMarkdownHref(rawHref: string, contextText = '', rewriteHref?: (href: string, contextText?: string) => string) {
  const href = rawHref.trim().split(/\s+/)[0].replace(/^<|>$/g, '');
  const rewritten = rewriteHref?.(href, contextText);
  if (rewritten) {
    return rewritten;
  }
  if (href.startsWith('#')) {
    return buildSourceBackedHashLink(href, contextText, rewriteHref);
  }
  if (isCurrentAppHashOnlyLink(href)) {
    return buildSourceBackedHashLink(href, contextText, rewriteHref);
  }
  if (/^(https?:\/\/|\/(?!\/))/i.test(href)) {
    return href;
  }
  return '';
}

// 兼容旧回答：把“位置”的当前应用 hash 链接重写到同一行的 OSS 来源 URL。
function buildSourceBackedHashLink(href: string, contextText: string, rewriteHref?: (href: string, contextText?: string) => string) {
  const source = extractHttpSourceFromEvidenceText(contextText);
  if (!source) return '';
  const hash = extractHash(href);
  const sourceBackedHref = hash ? `${source.split('#', 1)[0]}#${hash}` : source;
  return rewriteHref?.(sourceBackedHref, contextText) || sourceBackedHref;
}

// 从“来源：https://...”字段提取浏览器可打开的资料 URL。
function extractHttpSourceFromEvidenceText(text: string) {
  const match = /来源：\s*(https?:\/\/[^\s；;，,]+)/i.exec(text);
  return match?.[1] || '';
}

function extractHash(href: string) {
  const hashIndex = href.indexOf('#');
  return hashIndex >= 0 ? href.slice(hashIndex + 1) : '';
}

// 原文目录链接可能被模型改写成当前应用根路径 hash，但页面没有对应文档锚点。
function isCurrentAppHashOnlyLink(href: string) {
  if (!/^https?:\/\//i.test(href) || typeof window === 'undefined') {
    return false;
  }
  try {
    const target = new URL(href);
    const current = new URL(window.location.href);
    const sameHost = target.hostname === current.hostname || (isLoopbackHost(target.hostname) && isLoopbackHost(current.hostname));
    return (
      sameHost
      && target.protocol === current.protocol
      && target.port === current.port
      && target.pathname === '/'
      && Boolean(target.hash)
      && !target.search
    );
  } catch {
    return false;
  }
}

// 本地开发常在 localhost 和 127.0.0.1 之间切换，二者都指向当前应用。
function isLoopbackHost(hostname: string) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1' || hostname === '[::1]';
}

// 预览页和外部来源都应在新标签打开，站内普通导航可沿用当前页。
function isExternalOrPreviewHref(href: string) {
  return href.startsWith('http') || href.startsWith('/preview/') || href.startsWith('/videos');
}
