import type { ReactNode } from "react";

const INLINE_PATTERN = /(`[^`]+`|\*\*[^*\n]+?\*\*|__[^_\n]+?__|~~[^~\n]+?~~|\*[^*\n]+?\*)/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let part = 0;
  INLINE_PATTERN.lastIndex = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      nodes.push(text.slice(cursor, start));
    }
    const token = match[0];
    const key = `${keyPrefix}-${part++}`;
    if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (
      (token.startsWith("**") && token.endsWith("**")) ||
      (token.startsWith("__") && token.endsWith("__"))
    ) {
      nodes.push(<strong key={key}>{renderInline(token.slice(2, -2), `${key}-b`)}</strong>);
    } else if (token.startsWith("~~") && token.endsWith("~~")) {
      nodes.push(<del key={key}>{renderInline(token.slice(2, -2), `${key}-s`)}</del>);
    } else if (token.startsWith("*") && token.endsWith("*")) {
      nodes.push(<em key={key}>{renderInline(token.slice(1, -1), `${key}-i`)}</em>);
    } else {
      nodes.push(token);
    }
    cursor = start + token.length;
  }
  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isTableSeparator(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|") || !trimmed.includes("-")) return false;
  const cells = splitTableRow(trimmed);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isHeading(line: string): boolean {
  return /^#{1,4}\s+/.test(line);
}

function isHorizontalRule(line: string): boolean {
  return /^(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line.trim());
}

function isUnorderedItem(line: string): boolean {
  return /^\s*[-*+]\s+/.test(line);
}

function isOrderedItem(line: string): boolean {
  return /^\s*\d+\.\s+/.test(line);
}

function startsBlock(line: string, nextLine: string | undefined): boolean {
  return (
    !line.trim() ||
    line.trim().startsWith("```") ||
    isHeading(line) ||
    isHorizontalRule(line) ||
    line.trimStart().startsWith(">") ||
    isUnorderedItem(line) ||
    isOrderedItem(line) ||
    (line.includes("|") && nextLine !== undefined && isTableSeparator(nextLine))
  );
}

export function Markdown({ content, className }: { content: string; className?: string }) {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const lang = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").trim().startsWith("```")) {
        codeLines.push(lines[i] ?? "");
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push(
        <pre key={`code-${i}`} data-lang={lang || undefined}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const headingMatch = /^(#{1,4})\s+(.+)$/.exec(line);
    if (headingMatch) {
      const level = headingMatch[1]?.length ?? 1;
      const headingText = headingMatch[2] ?? "";
      const key = `h-${i}`;
      const rendered = renderInline(headingText, key);
      if (level === 1) blocks.push(<h1 key={key}>{rendered}</h1>);
      else if (level === 2) blocks.push(<h2 key={key}>{rendered}</h2>);
      else if (level === 3) blocks.push(<h3 key={key}>{rendered}</h3>);
      else blocks.push(<h4 key={key}>{rendered}</h4>);
      i += 1;
      continue;
    }

    if (isHorizontalRule(line)) {
      blocks.push(<hr key={`hr-${i}`} />);
      i += 1;
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1] ?? "")) {
      const headers = splitTableRow(line);
      const bodyRows: string[][] = [];
      i += 2;
      while (i < lines.length) {
        const rowLine = lines[i] ?? "";
        if (!rowLine.trim() || !rowLine.includes("|")) break;
        bodyRows.push(splitTableRow(rowLine));
        i += 1;
      }
      blocks.push(
        <div className="md-table-wrap" key={`table-${i}`}>
          <table>
            <thead>
              <tr>
                {headers.map((header, idx) => (
                  <th key={idx}>{renderInline(header, `th-${i}-${idx}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((row, rowIdx) => (
                <tr key={rowIdx}>
                  {headers.map((_, colIdx) => (
                    <td key={colIdx}>
                      {renderInline(row[colIdx] ?? "", `td-${i}-${rowIdx}-${colIdx}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (line.trimStart().startsWith(">")) {
      const quoteLines: string[] = [];
      const startIdx = i;
      while (i < lines.length && (lines[i] ?? "").trimStart().startsWith(">")) {
        quoteLines.push((lines[i] ?? "").trimStart().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push(
        <blockquote key={`q-${startIdx}`}>
          <Markdown content={quoteLines.join("\n")} />
        </blockquote>,
      );
      continue;
    }

    if (isUnorderedItem(line)) {
      const items: string[] = [];
      const startIdx = i;
      while (i < lines.length && isUnorderedItem(lines[i] ?? "")) {
        items.push((lines[i] ?? "").replace(/^\s*[-*+]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul key={`ul-${startIdx}`}>
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `ul-${startIdx}-${idx}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (isOrderedItem(line)) {
      const items: string[] = [];
      const startIdx = i;
      while (i < lines.length && isOrderedItem(lines[i] ?? "")) {
        items.push((lines[i] ?? "").replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ol key={`ol-${startIdx}`}>
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `ol-${startIdx}-${idx}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    const paragraphLines: string[] = [];
    const startIdx = i;
    while (i < lines.length && !startsBlock(lines[i] ?? "", lines[i + 1])) {
      paragraphLines.push(lines[i] ?? "");
      i += 1;
    }
    if (paragraphLines.length === 0) {
      paragraphLines.push(line);
      i += 1;
    }
    blocks.push(
      <p key={`p-${startIdx}`}>
        {paragraphLines.map((pLine, idx) => (
          <span key={idx}>
            {idx > 0 && <br />}
            {renderInline(pLine, `p-${startIdx}-${idx}`)}
          </span>
        ))}
      </p>,
    );
  }

  return <div className={`md-prose ${className ?? ""}`.trim()}>{blocks}</div>;
}
