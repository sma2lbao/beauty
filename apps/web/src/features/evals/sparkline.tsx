/**
 * 内联 SVG sparkline:不引图表库,只画通过率的最近趋势。
 */
export function Sparkline({
  values,
  width = 72,
  height = 20,
  label,
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
  label?: string;
}) {
  const points = values.filter((value): value is number => value !== null);
  if (points.length < 2) {
    return <span className="text-[11px] text-ha-faint">—</span>;
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = points.length > 1 ? width / (points.length - 1) : width;
  const coordinates = points.map((value, index) => {
    const x = index * step;
    const y = height - 2 - ((value - min) / span) * (height - 4);
    return x.toFixed(1) + ',' + y.toFixed(1);
  });

  return (
    <svg
      role="img"
      aria-label={label ?? '通过率趋势'}
      viewBox={'0 0 ' + width + ' ' + height}
      width={width}
      height={height}
      className="overflow-visible text-ha-accent-soft"
    >
      <polyline
        points={coordinates.join(' ')}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle
        cx={coordinates[coordinates.length - 1]?.split(',')[0]}
        cy={coordinates[coordinates.length - 1]?.split(',')[1]}
        r="1.8"
        fill="currentColor"
      />
    </svg>
  );
}
