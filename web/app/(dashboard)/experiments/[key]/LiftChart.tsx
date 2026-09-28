"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { liftTicks, signedPercent } from "@/lib/format";

export interface LiftPoint {
  time: number; // ms since the epoch
  lift: number;
  band: [number, number]; // the CI
}

const SERIES = "#2a78d6"; // the validated palette's first color
const RULE = "#dce1e7";
const MUTED = "#5b6676";

const UTC = { timeZone: "UTC" } as const;

/** Axis labels: times of day while the series spans under two days, dates after that. */
function axisLabel(span: number): (time: number) => string {
  const format: Intl.DateTimeFormatOptions =
    span < 2 * 86_400_000 ? { hour: "numeric", minute: "2-digit", ...UTC } : { month: "short", day: "numeric", ...UTC };
  return (time) => new Date(time).toLocaleString("en-US", format);
}

function dateTime(time: number): string {
  return `${new Date(time).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", ...UTC })} UTC`;
}

/** The relative lift over time, with its confidence interval as a band. */
export function LiftChart({ points, intervalName }: { points: LiftPoint[]; intervalName: string }) {
  const ticks = liftTicks(Math.min(...points.map((p) => p.band[0])), Math.max(...points.map((p) => p.band[1])));
  const times = points.map((p) => p.time);
  const timeLabel = axisLabel(Math.max(...times) - Math.min(...times));
  return (
    <figure>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
            <CartesianGrid stroke={RULE} vertical={false} />
            <XAxis
              dataKey="time"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={timeLabel}
              stroke={MUTED}
              tick={{ fontSize: 12 }}
            />
            <YAxis
              ticks={ticks}
              domain={[Math.min(...ticks), Math.max(...ticks)]}
              tickFormatter={(value: number) => signedPercent(value, 0)}
              stroke={MUTED}
              tick={{ fontSize: 12 }}
              width={56}
            />
            <ReferenceLine y={0} stroke={MUTED} />
            <Area dataKey="band" stroke="none" fill={SERIES} fillOpacity={0.15} isAnimationActive={false} />
            <Line dataKey="lift" stroke={SERIES} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Tooltip
              labelFormatter={(value) => dateTime(Number(value))}
              formatter={(value, name) => {
                if (name === "band" && Array.isArray(value)) {
                  return [`${signedPercent(Number(value[0]))} to ${signedPercent(Number(value[1]))}`, intervalName];
                }
                return [signedPercent(Number(value)), "Lift"];
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 text-sm text-muted">
        The line is the lift at each snapshot; the shaded band is its {intervalName}. Zero means no difference.
      </figcaption>
    </figure>
  );
}
