import { cn } from "@/lib/utils";

const tones = {
  healthy: "bg-green-500",
  warning: "bg-amber-500",
  danger: "bg-red-500",
  neutral: "bg-zinc-500",
} as const;

export function StatusIndicator({
  label,
  tone = "neutral",
  compact = false,
}: {
  label: string;
  tone?: keyof typeof tones;
  compact?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap text-xs",
        compact ? "justify-center" : "gap-2",
      )}
      role={compact ? "img" : undefined}
      aria-label={compact ? label : undefined}
      title={compact ? label : undefined}
    >
      <span className={cn("size-2 rounded-full", tones[tone])} aria-hidden="true" />
      {!compact && label}
    </span>
  );
}
