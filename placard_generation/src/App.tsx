import { useState, useEffect } from "react";
import {
  Bar,
  BarChart,
  LabelList,
  Legend,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import "./App.css";

const GOLD = "#DFCB63";
const WHITE = "#FFFFFF";
const MUTED = "#94A3B8";
const ORANGE = "#e07c3c";
const GREEN = "#22c55e";
const RED = "#ef4444";

/* ── CSV helpers ─────────────────────────────────────────────── */

function parseCSV(text: string): Record<string, string> {
  const map: Record<string, string> = {};
  const lines = text.trim().split("\n");
  // skip header row
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    // handle quoted values (e.g. "Oasis, 2/5")
    const match = line.match(/^([^,]+),\s*"?([^"]*)"?\s*$/);
    if (match) {
      map[match[1].trim()] = match[2].trim();
    }
  }
  return map;
}

function num(csv: Record<string, string>, key: string): number {
  return Number(csv[key]);
}

function buildEventData(csv: Record<string, string>) {
  const attendees = num(csv, "attendees");
  const firstTimers = num(csv, "firstTimers");
  const hasCostData = csv["hasCostData"] === "true";

  // Get cost values directly from CSV (may be "N/A")
  const totalCostStr = csv["totalCost"];
  const perAttendeeStr = csv["perAttendee"];
  const perFirstTimerStr = csv["perFirstTimer"];

  const totalCost = totalCostStr === "N/A" ? null : Number(totalCostStr);
  const perAttendee = perAttendeeStr === "N/A" ? null : Number(perAttendeeStr);
  const perFirstTimer = perFirstTimerStr === "N/A" ? null : Number(perFirstTimerStr);

  // collect retention pipeline rows dynamically
  const loyaltyPipeline: Array<{
    event: string;
    totalFromEvent: number;
    newFromEvent: number;
  }> = [];
  for (let i = 1; csv[`retention_event_${i}`]; i++) {
    loyaltyPipeline.push({
      event: csv[`retention_event_${i}`],
      totalFromEvent: num(csv, `retention_total_${i}`),
      newFromEvent: num(csv, `retention_new_${i}`),
    });
  }

  return {
    meta: {
      eventName: csv["eventName"] || "Event Name",
      venue: csv["venue"] || "",
      date: csv["date"],
      lastEvent: csv["lastEvent"],
      lastEventDate: csv["lastEventDate"],
    },
    stats: {
      rsvps: num(csv, "rsvps"),
      attendees,
      firstTimers,
    },
    statsDeltas: {
      rsvps: num(csv, "rsvpsDelta"),
      attendees: num(csv, "attendeesDelta"),
      firstTimers: num(csv, "firstTimersDelta"),
    },
    hasCostData,
    cost: {
      total: totalCost,
      perAttendee,
      perFirstTimer,
    },
    demographics: {
      gender: [
        { label: "Male", pct: num(csv, "malePct"), fill: GOLD },
        { label: "Female", pct: num(csv, "femalePct"), fill: MUTED },
      ],
      school: [
        { label: "MIT", pct: num(csv, "mitPct"), fill: GOLD },
        { label: "Harvard", pct: num(csv, "harvardPct"), fill: MUTED },
      ],
      year: [
        { label: "Underclassmen", pct: num(csv, "underclassmenPct"), fill: GOLD },
        { label: "Upperclassmen", pct: num(csv, "upperclassmenPct"), fill: MUTED },
      ],
      loyalty: [
        { label: "1st Event", pct: num(csv, "firstEventPct"), fill: GOLD },
        { label: "2–3 Events", pct: num(csv, "twoThreeEventsPct"), fill: ORANGE },
        { label: "4+ Events", pct: num(csv, "fourPlusEventsPct"), fill: MUTED },
      ],
    },
    loyaltyPipeline,
  };
}

/* ── Components ──────────────────────────────────────────────── */

// Custom label component with smart positioning (inside/outside with arrows)
function SmartBarLabel(props: any) {
  const { x, y, width, height, value } = props;

  // Threshold for deciding if label should go inside or outside (in pixels)
  const MIN_BAR_HEIGHT_FOR_INSIDE = 35;

  const labelText = `${value}%`;
  const fontSize = 11;

  const shouldPlaceInside = height > MIN_BAR_HEIGHT_FOR_INSIDE;

  if (shouldPlaceInside) {
    // Place label inside the bar, centered vertically
    return (
      <text
        x={x + width / 2}
        y={y + height / 2}
        textAnchor="middle"
        dominantBaseline="middle"
        fill={WHITE}
        fontSize={fontSize}
        fontWeight="500"
      >
        {labelText}
      </text>
    );
  } else {
    // Place label outside (above) with a downward arrow
    const arrowStartY = y - 15; // Position above the bar
    const arrowEndY = y - 3; // End near the top of the bar
    const arrowX = x + width / 2;

    return (
      <g>
        {/* Label text above the bar */}
        <text
          x={arrowX}
          y={arrowStartY - 2}
          textAnchor="middle"
          dominantBaseline="auto"
          fill={WHITE}
          fontSize={fontSize}
          fontWeight="500"
        >
          {labelText}
        </text>

        {/* Arrow line pointing down to the bar */}
        <line
          x1={arrowX}
          y1={arrowStartY}
          x2={arrowX}
          y2={arrowEndY}
          stroke={WHITE}
          strokeWidth={1}
          opacity={0.7}
        />

        {/* Arrowhead (small triangle pointing down) */}
        <polygon
          points={`${arrowX},${arrowEndY} ${arrowX - 3},${arrowEndY - 4} ${arrowX + 3},${arrowEndY - 4}`}
          fill={WHITE}
          opacity={0.7}
        />
      </g>
    );
  }
}

function Delta({
  value,
  suffix = " from last event",
}: {
  value: number;
  suffix?: string;
}) {
  const isPos = value >= 0;
  const arrow = isPos ? "↑ " : "↓ ";
  return (
    <span
      className="text-sm font-semibold mt-1 block"
      style={{ color: isPos ? GREEN : RED }}
    >
      {arrow}{isPos ? "+" : ""}
      {value}%{suffix}
    </span>
  );
}

function DemographicsRow({
  segments,
}: {
  segments: Array<{ label: string; pct: number; fill: string }>;
}) {
  // Threshold: hide label text if segment is too narrow (percentage too low)
  const MIN_PCT_FOR_LABEL = 15;

  return (
    <div className="demographics-bar">
      {segments.map(({ label, pct, fill }) => {
        const isMuted = fill === MUTED;
        const isOrange = fill === ORANGE;
        const showLabel = pct >= MIN_PCT_FOR_LABEL;

        return (
          <div
            key={label}
            className={`demographics-bar-segment${isMuted ? " demographics-bar-segment--muted" : ""}${isOrange ? " demographics-bar-segment--orange" : ""}`}
            style={{ width: `${pct}%`, background: fill }}
          >
            {isMuted ? (
              <>
                <span className="demographics-bar-pct">{pct}%</span>
                {showLabel && <span className="demographics-bar-label">{label}</span>}
              </>
            ) : (
              <>
                {showLabel && <span className="demographics-bar-label">{label}</span>}
                <span className="demographics-bar-pct">{pct}%</span>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

type EventData = ReturnType<typeof buildEventData>;

export default function App() {
  const [eventData, setEventData] = useState<EventData | null>(null);

  useEffect(() => {
    fetch("/event_data.csv")
      .then((r) => r.text())
      .then((text) => setEventData(buildEventData(parseCSV(text))))
      .catch((err) => console.error("Failed to load event_data.csv", err));
  }, []);

  if (!eventData) {
    return (
      <div className="min-h-screen flex items-center justify-center text-white" style={{ background: "#0D121D" }}>
        <p className="text-lg">Loading…</p>
      </div>
    );
  }

  const { meta, stats, statsDeltas, hasCostData, cost, demographics, loyaltyPipeline } =
    eventData;
  const attendeePct = ((stats.attendees / stats.rsvps) * 100).toFixed(1);

  return (
    <div className="min-h-screen text-white p-6 font-sans" style={{ background: "#0D121D" }}>
      {/* Header: Camel logo (negative space left), title centered 30% larger + extra bold, thumbnail right matched height */}
      <header className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 mb-8">
        <div className="flex justify-start">
          <img
            src="/camel-logo.png"
            alt="Camel"
            className="header-logo w-auto object-contain"
          />
        </div>
        <div className="text-center">
          <h1 className="dashboard-title text-[#DFCB63] font-extrabold tracking-tight m-0 leading-tight">
            {meta.eventName}
          </h1>
          <p className="text-white font-bold mt-1 text-lg">
            {meta.venue ? `@ ${meta.venue.split(' ')[0].replace(/,/g, '')}, ` : ""}{meta.date}
          </p>
          <p className="text-[#94A3B8] text-sm mt-1">
            Last event: {meta.lastEvent}, {meta.lastEventDate}
          </p>
        </div>
        <div className="flex justify-end">
          <img
            src="/camel-logo.png"
            alt="Event flyer"
            className="header-thumbnail w-auto object-cover rounded max-w-[173px]"
          />
        </div>
      </header>

      {/* Metric row: 4 identical cards – first three hero cards with divided layout */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
        <div className="hero-metric-card">
          <div className="hero-metric-top">
            <div className="hero-metric-value">{stats.rsvps}</div>
            <div className="hero-metric-label">RSVPS</div>
          </div>
          <div className="hero-metric-divider" />
          <div className="hero-metric-delta">
            <Delta value={statsDeltas.rsvps} />
          </div>
        </div>
        <div className="hero-metric-card">
          <div className="hero-metric-top">
            <div className="hero-metric-value">{stats.attendees}</div>
            <div className="hero-metric-label">ATTENDEES ({attendeePct}%)</div>
          </div>
          <div className="hero-metric-divider" />
          <div className="hero-metric-delta">
            <Delta value={statsDeltas.attendees} />
          </div>
        </div>
        <div className="hero-metric-card">
          <div className="hero-metric-top">
            <div className="hero-metric-value">{stats.firstTimers}</div>
            <div className="hero-metric-label">FIRST TIMERS</div>
          </div>
          <div className="hero-metric-divider" />
          <div className="hero-metric-delta">
            <Delta value={statsDeltas.firstTimers} />
          </div>
        </div>
        {hasCostData && (
          <div className="financials-card-outer">
            <div className="financials-card-header">FINANCIALS</div>
            <div className="financials-triangular">
              <div className="financials-inset financials-total-box">
                <div className="financials-total-value">
                  ${cost.total !== null && cost.total >= 1000 ? (cost.total / 1000).toFixed(1) + "k" : cost.total}
                </div>
                <div className="financials-total-label">TOTAL COST</div>
              </div>
              <div className="financials-unit-row">
                <div className="financials-inset financials-unit-box">
                  <div className="financials-unit-value">${cost.perAttendee !== null ? Math.round(cost.perAttendee) : "N/A"}</div>
                  <div className="financials-unit-label">PER ATTENDEE</div>
                </div>
                <div className="financials-inset financials-unit-box">
                  <div className="financials-unit-value">${cost.perFirstTimer !== null ? Math.round(cost.perFirstTimer) : "N/A"}</div>
                  <div className="financials-unit-label">PER FIRST TIMER</div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Bottom row: 50/50 split - exact halves */}
      <div className="bottom-section-container">
        <section className="bottom-section-half demographics-section">
          <h2 className="section-header">
            Demographics
          </h2>
          <div className="demographics-bars-container">
            <DemographicsRow segments={demographics.gender} />
            <DemographicsRow segments={demographics.school} />
            <DemographicsRow segments={demographics.year} />
            <DemographicsRow segments={demographics.loyalty} />
          </div>
        </section>

        <section className="bottom-section-half retention-section">
          <h2 className="section-header">
            Retention: % From Event that Attended Today
          </h2>
          <div className="retention-legend">
            <span className="retention-legend-item">
              <span className="retention-legend-swatch" style={{ background: GOLD }} />
              Total Attendees
            </span>
            <span className="retention-legend-item">
              <span className="retention-legend-swatch" style={{ background: MUTED }} />
              First Timers
            </span>
          </div>
          <div className="retention-chart-wrap">
            <ResponsiveContainer width="100%" height={280}>
              <BarChart
                data={loyaltyPipeline}
                margin={{ top: 20, right: 30, left: 0, bottom: 80 }}
              >
                <XAxis
                  dataKey="event"
                  tick={{ fill: WHITE, fontSize: 11 }}
                  axisLine={{ stroke: MUTED }}
                  angle={-45}
                  textAnchor="end"
                  height={80}
                />
                <YAxis
                  width={40}
                  tick={{ fill: WHITE, fontSize: 11 }}
                  axisLine={{ stroke: MUTED }}
                  tickFormatter={(v) => `${v}%`}
                  domain={[0, 'auto']}
                />
                <Legend wrapperStyle={{ display: "none" }} />
                <Bar
                  dataKey="totalFromEvent"
                  name="Total Attendees"
                  fill={GOLD}
                  radius={[4, 4, 0, 0]}
                >
                  <LabelList
                    dataKey="totalFromEvent"
                    content={SmartBarLabel}
                  />
                </Bar>
                <Bar
                  dataKey="newFromEvent"
                  name="First Timers"
                  fill={MUTED}
                  radius={[4, 4, 0, 0]}
                >
                  <LabelList
                    dataKey="newFromEvent"
                    content={SmartBarLabel}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>
    </div>
  );
}
