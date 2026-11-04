import type { ExceptionOut, ProposalOut } from "../api/schemas";
import type { z } from "zod";
import { punchSchema, scheduleSchema } from "../api/schemas";

type Punch = z.infer<typeof punchSchema>;
type Schedule = z.infer<typeof scheduleSchema>;

interface Segment {
  start: number;
  end: number;
  kind: "work" | "break";
}

const LEFT = 92;
const RIGHT = 948;
const WIDTH = RIGHT - LEFT;

export function TimecardTimeline({
  exception,
  punches,
  schedules,
  proposal,
}: {
  exception: ExceptionOut;
  punches: Punch[];
  schedules: Schedule[];
  proposal?: ProposalOut | null;
}) {
  const date = exception.work_date;
  const daySchedules = schedules.filter((schedule) => schedule.work_date === date);
  const dayPunches = punches.filter((punch) => punch.work_date === date && !punch.voided);
  const segments = makeSegments(dayPunches, date);
  const maxHour = Math.max(
    24,
    ...daySchedules.map((item) => hoursFromDay(item.end, date)),
    ...dayPunches.map((item) => hoursFromDay(item.ts, date)),
  ) > 24 ? 48 : 24;
  const toX = (hour: number) => LEFT + (Math.max(0, Math.min(maxHour, hour)) / maxHour) * WIDTH;
  const window = exception.constraints.ts_window;
  const hasMissingPunch = ["PUNCH-01", "PUNCH-02", "PUNCH-04", "BRK-01"].includes(exception.rule_id);
  const missingTime = hasMissingPunch ? readExpectedTime(exception.evidence, window?.earliest ?? null) : null;
  const proposedTime = proposal?.proposal.proposed_correction.ts ?? null;
  const ticks = maxHour === 24 ? [0, 6, 12, 18, 24] : [0, 6, 12, 18, 24, 30, 36, 42, 48];

  return (
    <section className="panel timeline-panel" aria-labelledby="timeline-title">
      <div className="panel-heading">
        <div><p className="eyebrow">Timecard view</p><h2 id="timeline-title">Shift timeline</h2></div>
        <span className="timeline-date">{date}</span>
      </div>
      <div className="timeline-legend" aria-label="Timeline legend">
        <span><i className="legend-swatch scheduled" />Scheduled</span>
        <span><i className="legend-swatch worked" />Worked</span>
        <span><i className="legend-swatch break" />Break</span>
        <span><i className="legend-swatch safe" />Allowed window</span>
        <span><i className="legend-swatch missing" />Missing punch</span>
        {proposedTime ? <span><i className="legend-swatch proposed" />Proposal</span> : null}
      </div>
      <div className="timeline-scroll">
        <svg className="timeline-svg" viewBox="0 0 1000 196" role="img" aria-labelledby="timeline-title timeline-description">
          <desc id="timeline-description">A time scale showing scheduled hours, recorded work and breaks, the safe correction window, and any proposed correction.</desc>
          <defs>
            <pattern id="breakHatch" width="7" height="7" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
              <rect width="7" height="7" fill="#f8e8c8" />
              <line x1="0" y1="0" x2="0" y2="7" stroke="#c8913e" strokeWidth="2" />
            </pattern>
          </defs>
          {ticks.map((hour) => {
            const x = toX(hour);
            return (
              <g key={hour}>
                <line className="axis-tick" x1={x} x2={x} y1="24" y2="169" />
                <text className="axis-label" x={x} y="187" textAnchor={hour === 0 ? "start" : hour === maxHour ? "end" : "middle"}>
                  {String(hour % 24).padStart(2, "0")}:00{hour === 24 ? " +1d" : hour > 24 && hour % 24 === 0 ? " +2d" : ""}
                </text>
              </g>
            );
          })}
          {window ? (
            <g aria-label={`Allowed correction window from ${window.earliest} to ${window.latest}`}>
              <rect className="safe-window-band" x={toX(hoursFromDay(window.earliest, date))} y="31" width={Math.max(2, toX(hoursFromDay(window.latest, date)) - toX(hoursFromDay(window.earliest, date)))} height="125" rx="6" />
            </g>
          ) : null}
          <text className="lane-label" x="6" y="60">Schedule</text>
          <text className="lane-label" x="6" y="96">Worked</text>
          <text className="lane-label" x="6" y="132">Breaks</text>
          {daySchedules.map((schedule) => {
            const startX = toX(hoursFromDay(schedule.start, date));
            const endX = toX(hoursFromDay(schedule.end, date));
            return <rect key={schedule.id} className="scheduled-bar" x={startX} y="46" width={Math.max(3, endX - startX)} height="20" rx="5" aria-label={`Scheduled from ${schedule.start} to ${schedule.end}`} />;
          })}
          {segments.map((segment, index) => (
            <rect
              key={`${segment.kind}-${index}`}
              className={segment.kind === "work" ? "worked-bar" : "break-bar"}
              x={toX(segment.start)}
              y={segment.kind === "work" ? 82 : 118}
              width={Math.max(3, toX(segment.end) - toX(segment.start))}
              height="20"
              rx="4"
              aria-label={`${segment.kind} from ${segment.start} to ${segment.end}`}
            />
          ))}
          {dayPunches.map((punch) => {
            const x = toX(hoursFromDay(punch.ts, date));
            return (
              <g key={punch.id} aria-label={`${punch.kind} punch at ${punch.ts}`}>
                <line className="punch-line" x1={x} x2={x} y1="72" y2="113" />
                <circle className={`punch-dot punch-${punch.kind.toLowerCase()}`} cx={x} cy="76" r="5" />
              </g>
            );
          })}
          {missingTime ? (
            <g aria-label={`Missing punch expected around ${missingTime}`}>
              <line className="missing-line" x1={toX(hoursFromDay(missingTime, date))} x2={toX(hoursFromDay(missingTime, date))} y1="31" y2="151" />
              <text className="missing-mark" x={toX(hoursFromDay(missingTime, date))} y="42" textAnchor="middle">?</text>
            </g>
          ) : null}
          {proposedTime ? (
            <g aria-label={`Proposed time ${proposedTime}`}>
              <line className="proposal-line" x1={toX(hoursFromDay(proposedTime, date))} x2={toX(hoursFromDay(proposedTime, date))} y1="26" y2="154" />
              <path className="proposal-diamond" d={`M ${toX(hoursFromDay(proposedTime, date))} 19 l 7 7 -7 7 -7 -7 z`} />
              <text className="proposal-label" x={toX(hoursFromDay(proposedTime, date))} y="16" textAnchor="middle">Proposed</text>
            </g>
          ) : null}
        </svg>
      </div>
      <table className="timeline-alt-table">
        <caption>Text summary of the timecard timeline</caption>
        <thead><tr><th>Record</th><th>Time</th><th>Details</th></tr></thead>
        <tbody>
          {daySchedules.map((schedule) => <tr key={`schedule-${schedule.id}`}><td>Scheduled shift</td><td>{schedule.start} – {schedule.end}</td><td>{schedule.planned_break_minutes} minute planned break</td></tr>)}
          {dayPunches.map((punch) => <tr key={`punch-${punch.id}`}><td>{punch.kind} punch</td><td>{punch.ts}</td><td>Source: {punch.source}</td></tr>)}
          {window ? <tr><td>Allowed correction window</td><td>{window.earliest} – {window.latest}</td><td>Validator-safe timestamps</td></tr> : null}
          {proposedTime ? <tr><td>Proposed time</td><td>{proposedTime}</td><td>Manager review required</td></tr> : null}
          {daySchedules.length === 0 && dayPunches.length === 0 ? <tr><td colSpan={3}>No punches or schedule are recorded for this work date.</td></tr> : null}
        </tbody>
      </table>
    </section>
  );
}

function makeSegments(punches: Punch[], workDate: string): Segment[] {
  const ordered = [...punches].sort((a, b) => a.ts.localeCompare(b.ts) || a.id - b.id);
  const segments: Segment[] = [];
  let inAt: number | null = null;
  let breakAt: number | null = null;
  for (const punch of ordered) {
    const at = hoursFromDay(punch.ts, workDate);
    if (punch.kind === "IN" && inAt === null) inAt = at;
    if (punch.kind === "BREAK_START") breakAt = at;
    if (punch.kind === "BREAK_END" && breakAt !== null) {
      segments.push({ start: breakAt, end: at, kind: "break" });
      breakAt = null;
    }
    if (punch.kind === "OUT" && inAt !== null) {
      segments.push({ start: inAt, end: at, kind: "work" });
      inAt = null;
    }
  }
  return segments;
}

function hoursFromDay(value: string, workDate: string): number {
  const [day, clock] = value.split("T");
  const [year, month, date] = (day ?? workDate).split("-").map(Number);
  const [hour, minute] = (clock ?? "00:00").split(":").map(Number);
  const [workYear, workMonth, workDay] = workDate.split("-").map(Number);
  const dayOffset = (Date.UTC(year ?? 0, (month ?? 1) - 1, date ?? 1) - Date.UTC(workYear ?? 0, (workMonth ?? 1) - 1, workDay ?? 1)) / 86_400_000;
  return dayOffset * 24 + (hour ?? 0) + (minute ?? 0) / 60;
}

function readExpectedTime(evidence: Record<string, unknown>, fallback: string | null): string | null {
  const keys = ["scheduled_end", "expected_ts", "break_end_ts", "scheduled_start"];
  for (const key of keys) {
    const value = evidence[key];
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return value;
  }
  return fallback;
}
