import type { ReactNode } from 'react';

interface MarkdownTextProps {
  content: string;
  className?: string;
  rewriteHref?: (href: string, contextText?: string) => string;
}

// 娓叉煋鍚庣 RAG 鍥炵瓟涓殑 Markdown 瀛愰泦锛岄伩鍏嶆妸妯″瀷鏂囨湰浣滀负 HTML 娉ㄥ叆椤甸潰銆?
export function MarkdownText({ content, className = '', rewriteHref }: MarkdownTextProps) {
  const blocks = renderMarkdownBlocks(content || '', rewriteHref);
  return <div className={`markdown-text ${className}`.trim()}>{blocks}</div>;
}

// 灏?Markdown 琛屾媶鎴愭爣棰樸€佹钀姐€佸垪琛ㄣ€佸紩鐢ㄥ拰浠ｇ爜鍧椼€?
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

  return blocks.length ? blocks : [<p key="empty">鏆傛棤鍐呭</p>];
}

// 妯″瀷鏈夋椂鎶婂垪琛ㄥ帇鎴愪竴琛岋紝杩欓噷鍙仛淇濆畧鎹㈣锛岄伩鍏嶅奖鍝嶆櫘閫氬彞瀛愩€?
function normalizeGeneratedMarkdown(content: string) {
  return content
    .replace(/\r\n/g, '\n')
    .replace(/([銆傦紱;:锛歖)\s+(\d+[.)]\s+\*\*)/g, '$1\n$2')
    .replace(/\s+(-\s+\u2757\s*)/g, '\n$1');
}

// 鏄惧紡閫夋嫨 HTML 鏍囬鏍囩锛岄伩鍏嶅姩鎬?JSX 鏍囩琚叏灞€ Three 绫诲瀷璇垽銆?
function renderHeading(level: number, text: string, key: string, rewriteHref?: (href: string, contextText?: string) => string) {
  if (level <= 4) {
    return <h4 key={key}>{renderInlineMarkdown(text, text, rewriteHref)}</h4>;
  }
  if (level === 5) {
    return <h5 key={key}>{renderInlineMarkdown(text, text, rewriteHref)}</h5>;
  }
  return <h6 key={key}>{renderInlineMarkdown(text, text, rewriteHref)}</h6>;
}

// 娓叉煋甯歌鍐呰仈璇硶锛氶摼鎺ャ€佽瘉鎹?ID銆佸姞绮椼€佷唬鐮佸拰绠€鏄撴暟瀛︾墖娈点€?
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

// 鍙厑璁稿父瑙勭珯鍐呴〉闈㈠拰 http(s) 閾炬帴锛涘師 Markdown 鐩綍閿氱偣涓嶅搴斿綋鍓嶅簲鐢ㄧ洰鏍囥€?
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

// 鍏煎鏃у洖绛旓細鎶娾€滀綅缃€濈殑褰撳墠搴旂敤 hash 閾炬帴閲嶅啓鍒板悓涓€琛岀殑 OSS 鏉ユ簮 URL銆?
function buildSourceBackedHashLink(href: string, contextText: string, rewriteHref?: (href: string, contextText?: string) => string) {
  const source = extractHttpSourceFromEvidenceText(contextText);
  if (!source) return '';
  const hash = extractHash(href);
  const sourceBackedHref = hash ? `${source.split('#', 1)[0]}#${hash}` : source;
  return rewriteHref?.(sourceBackedHref, contextText) || sourceBackedHref;
}

// 浠庘€滄潵婧愶細https://...鈥濆瓧娈垫彁鍙栨祻瑙堝櫒鍙墦寮€鐨勮祫鏂?URL銆?
function extractHttpSourceFromEvidenceText(text: string) {
  const match = /鏉ユ簮锛歕s*(https?:\/\/[^\s锛?锛?]+)/i.exec(text);
  return match?.[1] || '';
}

function extractHash(href: string) {
  const hashIndex = href.indexOf('#');
  return hashIndex >= 0 ? href.slice(hashIndex + 1) : '';
}

// 鍘熸枃鐩綍閾炬帴鍙兘琚ā鍨嬫敼鍐欐垚褰撳墠搴旂敤鏍硅矾寰?hash锛屼絾椤甸潰娌℃湁瀵瑰簲鏂囨。閿氱偣銆?
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

// 鏈湴寮€鍙戝父鍦?localhost 鍜?127.0.0.1 涔嬮棿鍒囨崲锛屼簩鑰呴兘鎸囧悜褰撳墠搴旂敤銆?
function isLoopbackHost(hostname: string) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1' || hostname === '[::1]';
}

// 棰勮椤靛拰澶栭儴鏉ユ簮閮藉簲鍦ㄦ柊鏍囩鎵撳紑锛岀珯鍐呮櫘閫氬鑸彲娌跨敤褰撳墠椤点€?
function isExternalOrPreviewHref(href: string) {
  return href.startsWith('http') || href.startsWith('/preview/');
}

