import type { ReactNode } from 'react';

interface MarkdownTextProps {
  content: string;
  className?: string;
  rewriteHref?: (href: string, contextText?: string) => string;
}

// 渲染后端 RAG 回答中的安全 Markdown 子集，避免把模型文本作为 HTML 注入页面。
export function MarkdownText({ content, className = '', rewriteHref }: MarkdownTextProps) {
  const blocks = renderMarkdownBlocks(content || '', rewriteHref);
  return <div className={`markdown-text ${className}`.trim()}>{blocks}</div>;
}

type MarkdownListMarker = { indent: number; ordered: boolean; number?: number; content: string };
type MarkdownListItem = { content: string; children: MarkdownListBlock[] };
type MarkdownListBlock = { ordered: boolean; start?: number; items: MarkdownListItem[] };
type MarkdownTableAlignment = 'left' | 'center' | 'right';
type MarkdownTableBlock = {
  headers: string[];
  alignments: MarkdownTableAlignment[];
  rows: string[][];
};

// 将 Markdown 行拆成标题、段落、列表、引用和代码块；列表项目允许使用空行和缩进续写。
function renderMarkdownBlocks(content: string, rewriteHref?: (href: string, contextText?: string) => string) {
  const lines = normalizeGeneratedMarkdown(content).split('\n');
  const blocks: ReactNode[] = [];
  let paragraphLines: string[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];
  let lineIndex = 0;

  function flushParagraph() {
    if (!paragraphLines.length) return;
    const text = paragraphLines.join(' ');
    blocks.push(<p key={`p-${blocks.length}`}>{renderInlineMarkdown(text, text, rewriteHref)}</p>);
    paragraphLines = [];
  }

  while (lineIndex < lines.length) {
    const line = lines[lineIndex];
    const trimmed = line.trim();
    if (trimmed.startsWith('```')) {
      if (inCodeBlock) {
        blocks.push(<pre key={`code-${blocks.length}`}><code>{codeLines.join('\n')}</code></pre>);
        codeLines = [];
        inCodeBlock = false;
      } else {
        flushParagraph();
        inCodeBlock = true;
        codeLines = [];
      }
      lineIndex += 1;
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      lineIndex += 1;
      continue;
    }

    if (!trimmed) {
      flushParagraph();
      lineIndex += 1;
      continue;
    }

    const parsedTable = parseMarkdownTable(lines, lineIndex);
    if (parsedTable) {
      flushParagraph();
      blocks.push(renderMarkdownTable(parsedTable.block, `table-${blocks.length}`, rewriteHref));
      lineIndex = parsedTable.nextIndex;
      continue;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      const level = Math.min(heading[1].length + 3, 6);
      blocks.push(renderHeading(level, heading[2], `heading-${blocks.length}`, rewriteHref));
      lineIndex += 1;
      continue;
    }

    const listMarker = parseMarkdownListMarker(line);
    if (listMarker) {
      flushParagraph();
      const parsedList = parseMarkdownList(lines, lineIndex, listMarker.indent);
      blocks.push(renderMarkdownList(parsedList.block, `list-${blocks.length}`, rewriteHref));
      lineIndex = parsedList.nextIndex;
      continue;
    }

    const quote = /^>\s?(.+)$/.exec(trimmed);
    if (quote) {
      flushParagraph();
      blocks.push(<blockquote key={`quote-${blocks.length}`}>{renderInlineMarkdown(quote[1], quote[1], rewriteHref)}</blockquote>);
      lineIndex += 1;
      continue;
    }

    paragraphLines.push(trimmed);
    lineIndex += 1;
  }

  if (inCodeBlock) {
    blocks.push(<pre key={`code-${blocks.length}`}><code>{codeLines.join('\n')}</code></pre>);
  }
  flushParagraph();

  return blocks.length ? blocks : [<p key="empty">暂无内容</p>];
}

// 识别 GFM 表格的表头、分隔行和数据行，并保留列对齐语义。
function parseMarkdownTable(lines: string[], startIndex: number): { block: MarkdownTableBlock; nextIndex: number } | null {
  if (startIndex + 1 >= lines.length) return null;
  const headers = parseMarkdownTableRow(lines[startIndex]);
  const delimiterCells = parseMarkdownTableRow(lines[startIndex + 1]);
  if (!headers || !delimiterCells || headers.length !== delimiterCells.length) return null;

  const alignments = delimiterCells.map(parseMarkdownTableAlignment);
  if (alignments.some((alignment) => alignment === null)) return null;

  const rows: string[][] = [];
  let lineIndex = startIndex + 2;
  while (lineIndex < lines.length) {
    if (!lines[lineIndex].trim()) break;
    const cells = parseMarkdownTableRow(lines[lineIndex]);
    if (!cells) break;
    rows.push(normalizeMarkdownTableCells(cells, headers.length));
    lineIndex += 1;
  }

  return {
    block: {
      headers,
      alignments: alignments as MarkdownTableAlignment[],
      rows
    },
    nextIndex: lineIndex
  };
}

// 按未转义的竖线拆分单行，避免把 `\|` 或行内代码中的竖线误当作列边界。
function parseMarkdownTableRow(line: string): string[] | null {
  let source = line.trim();
  if (!source.includes('|')) return null;
  if (source.startsWith('|')) source = source.slice(1);
  if (source.endsWith('|') && !source.endsWith('\\|')) source = source.slice(0, -1);

  const cells: string[] = [];
  let current = '';
  let inInlineCode = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === '\\' && source[index + 1] === '|') {
      current += '|';
      index += 1;
      continue;
    }
    if (character === '`') {
      inInlineCode = !inInlineCode;
      current += character;
      continue;
    }
    if (character === '|' && !inInlineCode) {
      cells.push(current.trim());
      current = '';
      continue;
    }
    current += character;
  }
  cells.push(current.trim());
  return cells.length >= 2 ? cells : null;
}

// 分隔行中的冒号决定表格列的左、中、右对齐方式。
function parseMarkdownTableAlignment(value: string): MarkdownTableAlignment | null {
  const normalized = value.replace(/\s+/g, '');
  const match = /^(:)?-{3,}(:)?$/.exec(normalized);
  if (!match) return null;
  if (match[1] && match[2]) return 'center';
  if (match[2]) return 'right';
  return 'left';
}

// 数据列少于表头时补空单元格，多余列按 GFM 行为忽略。
function normalizeMarkdownTableCells(cells: string[], columnCount: number) {
  return Array.from({ length: columnCount }, (_, index) => cells[index] || '');
}

// 使用语义化 table 渲染，并由外层容器承担窄屏横向滚动。
function renderMarkdownTable(
  block: MarkdownTableBlock,
  key: string,
  rewriteHref?: (href: string, contextText?: string) => string
): ReactNode {
  return (
    <div className="markdown-table-scroll" key={key} role="region" aria-label="内容表格，可横向滚动" tabIndex={0}>
      <table>
        <thead>
          <tr>
            {block.headers.map((header, index) => (
              <th className={`markdown-table-align-${block.alignments[index]}`} key={`${key}-header-${index}`} scope="col">
                {renderInlineMarkdown(header, header, rewriteHref)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, rowIndex) => (
            <tr key={`${key}-row-${rowIndex}`}>
              {row.map((cell, columnIndex) => (
                <td className={`markdown-table-align-${block.alignments[columnIndex]}`} key={`${key}-cell-${rowIndex}-${columnIndex}`}>
                  {renderInlineMarkdown(cell, cell, rewriteHref)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 读取列表标记并保留缩进，用于区分同级列表与嵌套列表。
function parseMarkdownListMarker(line: string): MarkdownListMarker | null {
  const ordered = /^(\s*)(\d+)[.)]\s+(.+)$/.exec(line);
  if (ordered) {
    return {
      indent: normalizeMarkdownIndent(ordered[1]),
      ordered: true,
      number: Number(ordered[2]),
      content: ordered[3]
    };
  }
  const unordered = /^(\s*)[-*+]\s+(.+)$/.exec(line);
  if (unordered) {
    return { indent: normalizeMarkdownIndent(unordered[1]), ordered: false, content: unordered[2] };
  }
  return null;
}

// 递归读取一个列表，保留空行分隔的同级项目和缩进产生的子列表。
function parseMarkdownList(lines: string[], startIndex: number, baseIndent: number): { block: MarkdownListBlock; nextIndex: number } {
  const first = parseMarkdownListMarker(lines[startIndex]);
  const ordered = first?.ordered ?? false;
  const items: MarkdownListItem[] = [];
  let lineIndex = startIndex;

  while (lineIndex < lines.length) {
    const marker = parseMarkdownListMarker(lines[lineIndex]);
    if (!marker || marker.indent !== baseIndent || marker.ordered !== ordered) break;
    const item: MarkdownListItem = { content: marker.content, children: [] };
    items.push(item);
    lineIndex += 1;

    while (lineIndex < lines.length) {
      const currentLine = lines[lineIndex];
      const trimmed = currentLine.trim();
      if (!trimmed) {
        lineIndex += 1;
        continue;
      }

      const nestedMarker = parseMarkdownListMarker(currentLine);
      if (nestedMarker) {
        if (nestedMarker.indent <= baseIndent) break;
        const nested = parseMarkdownList(lines, lineIndex, nestedMarker.indent);
        item.children.push(nested.block);
        lineIndex = nested.nextIndex;
        continue;
      }

      if (normalizeMarkdownIndent(currentLine.match(/^\s*/)?.[0] || '') > baseIndent) {
        item.content = `${item.content} ${trimmed}`;
        lineIndex += 1;
        continue;
      }
      break;
    }
  }

  return {
    block: { ordered, start: ordered && first?.number && first.number !== 1 ? first.number : undefined, items },
    nextIndex: lineIndex
  };
}

// 把解析后的列表渲染为语义化 ol/ul，嵌套列表自然形成层次结构。
function renderMarkdownList(block: MarkdownListBlock, key: string, rewriteHref?: (href: string, contextText?: string) => string): ReactNode {
  const Tag = block.ordered ? 'ol' : 'ul';
  return (
    <Tag key={key} start={block.ordered ? block.start : undefined}>
      {block.items.map((item, index) => (
        <li key={`${key}-item-${index}`}>
          {renderInlineMarkdown(item.content, item.content, rewriteHref)}
          {item.children.map((child, childIndex) => renderMarkdownList(child, `${key}-child-${index}-${childIndex}`, rewriteHref))}
        </li>
      ))}
    </Tag>
  );
}

// 把 Tab 缩进按四个空格处理，保证生成内容使用空格或 Tab 都能正确嵌套。
function normalizeMarkdownIndent(indent: string) {
  return indent.replace(/\t/g, '    ').length;
}

// 模型有时把列表压成一行，这里只做保守换行，避免影响普通句子。
function normalizeGeneratedMarkdown(content: string) {
  return content
    .replace(/\r\n/g, '\n')
    .replace(/([。；;:：])[ \t]+(\d+[.)])[ \t]+/g, '$1\n$2 ')
    .replace(/[ \t]+(-\s+❗\s*)/g, '\n$1');
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
        ? <a key={key} href={href} target={isNewTabHref(href) ? '_blank' : undefined} rel="noreferrer">{renderInlineMarkdown(match[4], contextText, rewriteHref)}</a>
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

// 只允许常规站内页面和 http(s) 链接；原 Markdown 目录锚点会映射到真实资料来源。
function normalizeMarkdownHref(rawHref: string, contextText = '', rewriteHref?: (href: string, contextText?: string) => string) {
  const href = rawHref.trim().split(/\s+/)[0].replace(/^<|>$/g, '');
  const rewritten = rewriteHref?.(href, contextText);
  if (rewritten) return rewritten;
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

// 兼容旧回答：把“位置”的当前应用 hash 链接重写到同一行的来源 URL。
function buildSourceBackedHashLink(href: string, contextText: string, rewriteHref?: (href: string, contextText?: string) => string) {
  const source = extractHttpSourceFromEvidenceText(contextText);
  if (!source) return '';
  const hash = extractHash(href);
  const sourceBackedHref = hash ? `${source.split('#', 1)[0]}#${hash}` : source;
  return rewriteHref?.(sourceBackedHref, contextText) || sourceBackedHref;
}

// 从“来源：https://...”字段提取浏览器可打开的资料 URL。
function extractHttpSourceFromEvidenceText(text: string) {
  const match = /来源[:：]\s*(https?:\/\/[^\s；;，,]+)/i.exec(text);
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

// 预览页、视频页和外部来源都应在新标签打开，站内普通导航可沿用当前页。
function isNewTabHref(href: string) {
  return href.startsWith('http') || href.startsWith('/preview/') || href.startsWith('/videos');
}
