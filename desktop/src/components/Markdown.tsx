import { Fragment, type ReactNode } from "react";

import { cx } from "./ui";

/** Headings, lists, tables, **bold** and `code`: enough for the briefs and reviews the agents write. */
export function Markdown({ text }: { text: string }) {
  const inline = (line: string, key: string): ReactNode[] =>
    line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
      if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={`${key}-${i}`} className="font-semibold text-ink">{part.slice(2, -2)}</strong>;
      if (/^`[^`]+`$/.test(part)) return <code key={`${key}-${i}`} className="rounded bg-raised px-1 font-mono text-[0.92em] text-ink">{part.slice(1, -1)}</code>;
      return <Fragment key={`${key}-${i}`}>{part}</Fragment>;
    });
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = (lines[i] ?? "").trimEnd();
    if (line.trim().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && (lines[i] ?? "").trim().startsWith("|")) {
        const cells = (lines[i] ?? "").trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      i--;
      const [head, ...body] = rows;
      blocks.push(
        <div key={`t${i}`} className="my-1 overflow-x-auto rounded-md border border-line">
          <table className="w-full border-collapse text-left text-[11.5px]">
            {head && (
              <thead className="bg-raised text-ink-2">
                <tr>{head.map((c, j) => <th key={j} className="px-2 py-1 font-medium whitespace-nowrap">{inline(c, `th${i}${j}`)}</th>)}</tr>
              </thead>
            )}
            <tbody>
              {body.map((r, k) => (
                <tr key={k} className="border-t border-line align-top">
                  {r.map((c, j) => <td key={j} className="px-2 py-1 text-ink-2">{inline(c, `td${i}${k}${j}`)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (!line.trim()) {
      blocks.push(<div key={i} className="h-1.5" />);
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1]?.length ?? 2;
      blocks.push(<div key={i} className={cx("font-semibold text-ink", level <= 1 ? "mt-1 text-[17px]" : level === 2 ? "mt-3 text-[14.5px]" : "mt-2 text-[13px]")}>{inline(heading[2] ?? "", `h${i}`)}</div>);
      continue;
    }
    const bullet = /^(\s*)(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      blocks.push(
        <div key={i} className="grid grid-cols-[12px_1fr] gap-1" style={{ paddingLeft: Math.min(24, (bullet[1]?.length ?? 0) * 6) }}>
          <span className="text-ink-3">·</span>
          <span>{inline(bullet[2] ?? "", `b${i}`)}</span>
        </div>,
      );
      continue;
    }
    blocks.push(<div key={i}>{inline(line, `p${i}`)}</div>);
  }
  return <div className="selectable grid gap-1 font-sans text-[13px] leading-relaxed text-ink-2">{blocks}</div>;
}
