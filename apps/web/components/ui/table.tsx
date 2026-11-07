import * as React from "react";

import { cn } from "@/lib/utils";

/** Screen-reader friendly table: a caption, header cells with scope, and horizontal scroll
 * inside its own region on small screens (no page-level horizontal scroll). */
export function DataTable({
  caption,
  captionHidden = false,
  head,
  children,
  className,
}: {
  caption: string;
  captionHidden?: boolean;
  head: React.ReactNode[];
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("overflow-x-auto rounded-lg border", className)} role="region" aria-label={caption} tabIndex={0}>
      <table className="w-full min-w-[36rem] text-sm">
        <caption className={cn("p-3 text-left font-medium", captionHidden && "sr-only")}>{caption}</caption>
        <thead className="bg-muted/50 text-left">
          <tr>
            {head.map((h, i) => (
              <th key={i} scope="col" className="px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-2 align-top", className)} {...props} />;
}

export function Num({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-2 text-right align-top tabular-nums", className)} {...props} />;
}
