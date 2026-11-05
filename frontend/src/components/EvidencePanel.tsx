import type { z } from "zod";
import type { ExceptionOut, ProposalOut } from "../api/schemas";
import { punchSchema, scheduleSchema } from "../api/schemas";
import { formatHours, formatTime } from "./common";
import { TimecardTimeline } from "./TimecardTimeline";

type Punch = z.infer<typeof punchSchema>;
type Schedule = z.infer<typeof scheduleSchema>;

const PUNCH_LABELS: Record<string, string> = {
  IN: "Clock in",
  OUT: "Clock out",
  BREAK_START: "Break start",
  BREAK_END: "Break end",
};

const SOURCE_LABELS: Record<string, string> = {
  device: "Time clock",
  "synthetic-device": "Time clock (sample data)",
  synthetic_device: "Time clock (sample data)",
  kiosk: "Kiosk",
  mobile: "Mobile app",
  manual: "Manual entry",
  correction: "Manual correction",
};

/** Where a punch came from, in words an HR reader recognises. */
export function sourceLabel(source: string | null | undefined): string {
  const key = (source ?? "").trim().toLowerCase();
  if (!key) return "Not recorded";
  if (SOURCE_LABELS[key]) return SOURCE_LABELS[key];
  const text = key.replaceAll(/[-_]+/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const minutesBetween = (from: string, to: string) => Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60000);

/** Key numbers for the day: net time worked, break taken, and the first/last punch. */
export function summarizeDay(punches: Punch[]) {
  const ordered = [...punches].sort((a, b) => a.ts.localeCompare(b.ts) || a.id - b.id);
  const firstIn = ordered.find((punch) => punch.kind === "IN");
  const lastOut = [...ordered].reverse().find((punch) => punch.kind === "OUT");
  let breakMinutes = 0;
  let breakLabel: string | null = null;
  let openBreak: Punch | undefined;
  for (const punch of ordered) {
    if (punch.kind === "BREAK_START") openBreak = punch;
    if (punch.kind === "BREAK_END" && openBreak) {
      breakMinutes += Math.max(0, minutesBetween(openBreak.ts, punch.ts));
      breakLabel = `${formatTime(openBreak.ts)} – ${formatTime(punch.ts)}`;
      openBreak = undefined;
    }
  }
  const worked = firstIn && lastOut ? Math.max(0, minutesBetween(firstIn.ts, lastOut.ts) - breakMinutes) : null;
  return { firstIn, lastOut, breakMinutes, breakLabel, worked };
}

/** One panel that answers: what was scheduled, what was recorded, and in what order. */
export function EvidencePanel({
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
  const dayPunches = punches
    .filter((punch) => punch.work_date === date)
    .sort((a, b) => a.ts.localeCompare(b.ts) || a.id - b.id);
  const activePunches = dayPunches.filter((punch) => !punch.voided);
  const schedule = schedules.find((item) => item.work_date === date);
  const summary = summarizeDay(activePunches);

  return (
    <section className="panel evidence-panel" aria-labelledby="evidence-heading">
      <div className="panel-heading">
        <div><p className="eyebrow">Evidence</p><h2 id="evidence-heading">What was recorded</h2></div>
        <span className="count-chip">{activePunches.length} {activePunches.length === 1 ? "punch" : "punches"}</span>
      </div>

      <div className="evidence-summary">
        <div className="evidence-stat">
          <span>Scheduled</span>
          <strong>{schedule ? `${formatTime(schedule.start)} – ${formatTime(schedule.end)}` : "No schedule"}</strong>
          <small>{schedule ? `${schedule.planned_break_minutes} min break planned` : "Nothing on file for this day"}</small>
        </div>
        <div className={`evidence-stat${summary.worked === null ? " is-warn" : ""}`}>
          <span>Time worked</span>
          <strong>{summary.worked === null ? "Incomplete" : formatHours(summary.worked)}</strong>
          <small>
            {summary.worked !== null && summary.firstIn && summary.lastOut
              ? `${formatTime(summary.firstIn.ts)} – ${formatTime(summary.lastOut.ts)}`
              : summary.firstIn ? `Clocked in ${formatTime(summary.firstIn.ts)}, no clock-out` : "No clock-in recorded"}
          </small>
        </div>
        <div className="evidence-stat">
          <span>Break taken</span>
          <strong>{summary.breakMinutes > 0 ? `${summary.breakMinutes} min` : "None recorded"}</strong>
          <small>{summary.breakLabel ?? (schedule?.planned_break_minutes ? `${schedule.planned_break_minutes} min planned` : "No break on file")}</small>
        </div>
      </div>

      <TimecardTimeline exception={exception} punches={punches} schedules={schedules} proposal={proposal} />

      <div className="punch-log">
        <h3 className="punch-log-title">Punch log</h3>
        {dayPunches.length ? (
          <ol className="punch-list" aria-label="Recorded punches">
            {dayPunches.map((punch) => (
              <li className={punch.voided ? "punch-voided" : ""} key={punch.id} title={`Record #${punch.id}`}>
                <time>{formatTime(punch.ts)}</time>
                <span className={`punch-kind kind-${punch.kind.toLowerCase()}`}>{PUNCH_LABELS[punch.kind] ?? punch.kind.replaceAll("_", " ")}</span>
                <span className="punch-source">{sourceLabel(punch.source)}</span>
                {punch.voided ? <span className="voided-label">Voided</span> : <span />}
              </li>
            ))}
          </ol>
        ) : (
          <p className="punch-empty">No punch records for this date.</p>
        )}
      </div>
    </section>
  );
}
