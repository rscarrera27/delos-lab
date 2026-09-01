export function MetricValue({ name, value }: { name: string; value: string | number }) {
  return (
    <span data-metric={name} className="font-mono text-xs tabular-nums">
      {value}
    </span>
  );
}
