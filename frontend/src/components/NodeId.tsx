import type { ComponentPropsWithoutRef } from "react";

import { cn } from "../lib/utils";

export const shortNodeId = (id: string) =>
  id.length <= 12 ? id : `${id.slice(0, 12)}…`;

export function NodeId({
  id,
  className,
  ...props
}: { id: string } & Omit<ComponentPropsWithoutRef<"span">, "children" | "title">) {
  return (
    <span
      className={cn("min-w-0 truncate font-mono", className)}
      title={id}
      {...props}
    >
      {shortNodeId(id)}
    </span>
  );
}
