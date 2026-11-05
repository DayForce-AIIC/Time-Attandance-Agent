import type { ExceptionOut, ProposalOut } from "../api/schemas";
import type { z } from "zod";
import { punchSchema, scheduleSchema } from "../api/schemas";
import { formatTime } from "./common";

type Punch = z.infer<typeof punchSchema>;
type Schedule = z.infer<typeof scheduleSchema>;

interface Segment {
  start: number;
  end: number;
  kind: "work" | "break";
}

interface Label {
  x: number;
  text: string;
}

const VIEW_WIDTH = 760;
const LEFT = 92;
const RIGHT = 724;
const WIDTH = RIGHT - LEFT;
const SCHEDULE_Y = 32;
const RECORDED_Y = 80;
const BAR_HEIGHT = 26;
const LABEL_GAP = 62;

/** Chart of one work day: the scheduled shift, what was actually recorded, and any proposed fix. */
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
  const window = exception.constraints.ts_window;
  const hasMissingPunch = ["PUNCH-01", "PUNCH-02", "PUNCH-04", "BRK-01"].includes(exception.rule_id);
  // Only flag a missing punch while it is actually missing (not after a correction added it).
  const requiredKind: Record<string, Punch["kind"]> = { "PUNCH-01": "OUT", "PUNCH-02": "IN", "PUNCH-04": "BREAK_END" };
  const stillMissing = hasMissingPunch && (exception.rule_id === "BRK-01"
    ? !segments.some((segment) => segment.kind === "break")
    : !dayPunches.some((punch) => punch.kind === requiredKind[exception.rule_id]));
  const missingTime = stillMissing ? readExpectedTime(exception.evidence, window?.earliest ?? null) : null;
  const proposedTime = proposal?.proposal.proposed_correction.ts ?? null;

  // Fit the time axis to the shift instead of always showing a mostly empty 24-hour day.
  const marks = [
    ...daySchedules.flatMap((item) => [hoursFromDay(item.start, date), hoursFromDay(item.end, date)]),
    ...dayPunches.map((item) => hoursFromDay(item.ts, date)),
    ...(window ? [hoursFromDay(window.earliest, date), hoursFromDay(window.latest, date)] : []),
    ...(missingTime ? [hoursFromDay(missingTime, date)] : []),
    ...(proposedTime ? [hoursFromDay(proposedTime, date)] : []),
  ];
  const { startHour, endHour } = fitAxis(marks);
  const span = endHour - startHour;
  const toX = (hour: number) => LEFT + ((Math.max(startHour, Math.min(endHour, hour)) - startHour) / span) * WIDTH;
  const step = span <= 12 ? 1 : span <= 18 ? 2 : 4;
  const ticks: number[] = [];
  for (let hour = Math.ceil(startHour / step) * step; hour <= endHour; hour += step) ticks.push(hour);

  // Time labels under the bar: clock-in and clock-out first, then the break, skipping any that would collide.
  const ordered = [...dayPunches].sort((a, b) => a.ts.localeCompare(b.ts) || a.id - b.id);
  const firstIn = ordered.find((punch) => punch.kind === "IN");
  const lastOut = [...ordered].reverse().find((punch) => punch.kind === "OUT");
  const labels: Label[] = [];
  const addLabel = (label: Label) => {
    if (labels.every((existing) => Math.abs(existing.x - label.x) >= LABEL_GAP)) labels.push(label);
  };
  if (firstIn) addLabel({ x: toX(hoursFromDay(firstIn.ts, date)), text: formatTime(firstIn.ts) });
  if (lastOut) addLabel({ x: toX(hoursFromDay(lastOut.ts, date)), text: formatTime(lastOut.ts) });
  const breakSegment = segments.find((segment) => segment.kind === "break");
  if (breakSegment) {
    const minutes = Math.round((breakSegment.end - breakSegment.start) * 60);
    addLabel({ x: (toX(breakSegment.start) + toX(breakSegment.end)) / 2, text: `Break ${minutes}m` });
  }
  const anchorFor = (x: number) => (x < LEFT + 28 ? "start" : x > RIGHT - 28 ? "end" : "middle");
  const drawOrder = [...segments].sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "work" ? -1 : 1));

  return (
    <div className="timeline">
      <div className="timeline-legend" aria-label="Timeline legend">
        <span><i className="legend-swatch scheduled" />Scheduled shift</span>
        <span><i className="legend-swatch worked" />Time worked</span>
        <span><i className="legend-swatch break" />Break</span>
        {window ? <span><i className="legend-swatch safe" />Allowed correction window</span> : null}
        {missingTime ? <span><i className="legend-swatch missing" />Missing punch</span> : null}
        {proposedTime ? <span><i className="legend-swatch proposed" />Suggested time</span> : null}
      </div>
      <div className="timeline-scroll">
        <svg className="timeline-svg" viewBox={`0 0 ${VIEW_WIDTH} 170`} role="img" aria-label="Shift timeline chart" aria-describedby="timeline-description">
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
                <line className="axis-tick" x1={x} x2={x} y1="24" y2="140" />
                <text className="axis-label" x={x} y="160" textAnchor={hour === startHour ? "start" : "middle"}>
                  {formatHourLabel(hour)}
                </text>
              </g>
            );
          })}
          {window ? (
            <g aria-label={`Allowed correction window from ${window.earliest} to ${window.latest}`}>
              <rect className="safe-window-band" x={toX(hoursFromDay(window.earliest, date))} y="26" width={Math.max(2, toX(hoursFromDay(window.latest, date)) - toX(hoursFromDay(window.earliest, date)))} height="110" rx="6" />
            </g>
          ) : null}

          <text className="lane-label" x="0" y={SCHEDULE_Y + BAR_HEIGHT / 2 + 4}>Scheduled</text>
          {daySchedules.length ? daySchedules.map((schedule) => {
            const startX = toX(hoursFromDay(schedule.start, date));
            const endX = toX(hoursFromDay(schedule.end, date));
            const wide = endX - startX > 150;
            return (
              <g key={schedule.id} aria-label={`Scheduled from ${schedule.start} to ${schedule.end}`}>
                <rect className="scheduled-bar" x={startX} y={SCHEDULE_Y} width={Math.max(3, endX - startX)} height={BAR_HEIGHT} rx="6" />
                {wide ? <text className="bar-label" x={startX + 10} y={SCHEDULE_Y + BAR_HEIGHT / 2 + 4}>{formatTime(schedule.start)}</text> : null}
                {wide ? <text className="bar-label" x={endX - 10} y={SCHEDULE_Y + BAR_HEIGHT / 2 + 4} textAnchor="end">{formatTime(schedule.end)}</text> : null}
              </g>
            );
          }) : (
            <g>
              <rect className="no-schedule-bar" x={LEFT} y={SCHEDULE_Y} width={WIDTH} height={BAR_HEIGHT} rx="6" />
              <text className="no-schedule-text" x={(LEFT + RIGHT) / 2} y={SCHEDULE_Y + BAR_HEIGHT / 2 + 4} textAnchor="middle">No schedule on file for this day</text>
            </g>
          )}

          <text className="lane-label" x="0" y={RECORDED_Y + BAR_HEIGHT / 2 + 4}>Recorded</text>
          {drawOrder.map((segment, index) => (
            <rect
              key={`${segment.kind}-${index}`}
              className={segment.kind === "work" ? "worked-bar" : "break-bar"}
              x={toX(segment.start)}
              y={RECORDED_Y}
              width={Math.max(3, toX(segment.end) - toX(segment.start))}
              height={BAR_HEIGHT}
              rx={segment.kind === "work" ? 6 : 3}
              aria-label={`${segment.kind} from ${segment.start} to ${segment.end}`}
            />
          ))}
          {dayPunches.map((punch) => (
            <circle key={punch.id} className={`punch-dot punch-${punch.kind.toLowerCase()}`} cx={toX(hoursFromDay(punch.ts, date))} cy={RECORDED_Y + BAR_HEIGHT / 2} r="6" aria-label={`${punch.kind} punch at ${punch.ts}`} />
          ))}
          {labels.map((label) => (
            <text key={`${label.text}-${label.x}`} className="punch-time" x={label.x} y={RECORDED_Y + BAR_HEIGHT + 18} textAnchor={anchorFor(label.x)}>{label.text}</text>
          ))}

          {missingTime ? (
            <g aria-label={`Missing punch expected around ${missingTime}`}>
              <line className="missing-line" x1={toX(hoursFromDay(missingTime, date))} x2={toX(hoursFromDay(missingTime, date))} y1="26" y2="130" />
              <text className="missing-mark" x={toX(hoursFromDay(missingTime, date))} y="22" textAnchor="middle">?</text>
              <text className="missing-label" x={toX(hoursFromDay(missingTime, date))} y={RECORDED_Y + BAR_HEIGHT + 34} textAnchor={anchorFor(toX(hoursFromDay(missingTime, date)))}>Missing punch</text>
            </g>
          ) : null}
          {proposedTime ? (
            <g aria-label={`Proposed time ${proposedTime}`}>
              <line className="proposal-line" x1={toX(hoursFromDay(proposedTime, date))} x2={toX(hoursFromDay(proposedTime, date))} y1="22" y2="132" />
              <path className="proposal-diamond" d={`M ${toX(hoursFromDay(proposedTime, date))} 12 l 7 7 -7 7 -7 -7 z`} />
              <text className="proposal-label" x={toX(hoursFromDay(proposedTime, date)) + 12} y="22">Suggested {formatTime(proposedTime)}</text>
            </g>
          ) : null}
        </svg>
      </div>
      <table className="timeline-alt-table visually-hidden">
        <caption>Text summary of the timecard timeline</caption>
        <thead><tr><th>Record</th><th>Time</th><th>Details</th></tr></thead>
        <tbody>
          {daySchedules.map((schedule) => <tr key={`schedule-${schedule.id}`}><td>Scheduled shift</td><td>{schedule.start} – {schedule.end}</td><td>{schedule.planned_break_minutes} minute planned break</td></tr>)}
          {dayPunches.map((punch) => <tr key={`punch-${punch.id}`}><td>{punch.kind} punch</td><td>{punch.ts}</td><td>Source: {punch.source ?? "not recorded"}</td></tr>)}
          {window ? <tr><td>Allowed correction window</td><td>{window.earliest} – {window.latest}</td><td>Validator-safe timestamps</td></tr> : null}
          {proposedTime ? <tr><td>Proposed time</td><td>{proposedTime}</td><td>Manager review required</td></tr> : null}
          {daySchedules.length === 0 && dayPunches.length === 0 ? <tr><td colSpan={3}>No punches or schedule are recorded for this work date.</td></tr> : null}
        </tbody>
      </table>
    </div>
  );
}

function fitAxis(marks: number[]): { startHour: number; endHour: number } {
  if (marks.length === 0) return { startHour: 6, endHour: 18 };
  let startHour = Math.floor(Math.min(...marks)) - 1;
  let endHour = Math.ceil(Math.max(...marks)) + 1;
  const missing = 8 - (endHour - startHour);
  if (missing > 0) {
    startHour -= Math.floor(missing / 2);
    endHour += Math.ceil(missing / 2);
  }
  return { startHour, endHour };
}

function formatHourLabel(hour: number): string {
  const dayOffset = Math.floor(hour / 24);
  const clock = ((hour % 24) + 24) % 24;
  const display = clock % 12 === 0 ? 12 : clock % 12;
  const suffix = clock >= 12 ? "PM" : "AM";
  const day = dayOffset > 0 ? ` +${dayOffset}d` : dayOffset < 0 ? ` ${dayOffset}d` : "";
  return `${display} ${suffix}${day}`;
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
