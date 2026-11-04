"""Pure reconstruction of shifts from punch and schedule records."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

from app.schemas import PunchKind


@dataclass(frozen=True)
class Punch:
    """A recorded timekeeping event associated with a work date."""

    id: int
    employee_id: str
    work_date: date
    ts: datetime
    kind: PunchKind
    voided: bool = False


@dataclass(frozen=True)
class Sched:
    """A planned shift and its planned unpaid break duration."""

    id: int
    employee_id: str
    work_date: date
    start: datetime
    end: datetime
    planned_break_minutes: int


@dataclass
class Shift:
    """Reconstructed shift with parse anomalies and derived minute totals."""

    employee_id: str
    work_date: date
    sched: Sched | None
    punches: list[Punch]
    duplicates: list[tuple[Punch, Punch]]
    in_ts: datetime | None
    out_ts: datetime | None
    breaks: list[tuple[Punch, Punch | None]]
    anomalies: list[str]
    expected_start: datetime
    expected_end: datetime

    @property
    def complete(self) -> bool:
        """Whether both boundary punches exist and the sequence is parseable."""
        return self.in_ts is not None and self.out_ts is not None and not self.anomalies

    @property
    def gross_minutes(self) -> int:
        """Return elapsed minutes between IN and OUT, or zero if incomplete."""
        if self.in_ts is None or self.out_ts is None:
            return 0
        return max(0, _whole_minutes(self.out_ts - self.in_ts))

    @property
    def break_minutes(self) -> int:
        """Return the total duration of closed breaks."""
        return sum(
            max(0, _whole_minutes(end.ts - start.ts))
            for start, end in self.breaks
            if end is not None
        )

    @property
    def worked_minutes(self) -> int:
        """Return gross elapsed time less closed break durations."""
        return max(0, self.gross_minutes - self.break_minutes)

    @property
    def planned_worked_minutes(self) -> int:
        """Return scheduled duration less the planned unpaid break."""
        if self.sched is None:
            return 0
        return max(
            0,
            _whole_minutes(self.sched.end - self.sched.start)
            - self.sched.planned_break_minutes,
        )


def _whole_minutes(duration: timedelta) -> int:
    return int(duration.total_seconds() // 60)


def _deduplicate(
    punches: list[Punch], dup_window_minutes: int
) -> tuple[list[Punch], list[tuple[Punch, Punch]]]:
    """Drop near-adjacent same-kind device events and retain their evidence."""
    kept: list[Punch] = []
    duplicates: list[tuple[Punch, Punch]] = []
    window = timedelta(minutes=dup_window_minutes)
    for punch in punches:
        if (
            kept
            and punch.kind is kept[-1].kind
            and timedelta(0) <= punch.ts - kept[-1].ts <= window
        ):
            duplicates.append((kept[-1], punch))
            continue
        kept.append(punch)
    return kept, duplicates


def _parse_sequence(
    punches: list[Punch],
) -> tuple[
    datetime | None,
    datetime | None,
    list[tuple[Punch, Punch | None]],
    list[str],
]:
    """Parse the expected IN / break-pair / OUT grammar and collect anomalies."""
    anomalies: list[str] = []
    breaks: list[tuple[Punch, Punch | None]] = []
    in_ts: datetime | None = None
    out_ts: datetime | None = None
    pending_break: Punch | None = None
    seen_out = False
    started = False

    def add_anomaly(name: str) -> None:
        if name not in anomalies:
            anomalies.append(name)

    if punches and punches[0].kind is PunchKind.OUT:
        add_anomaly("MISSING_IN")

    for punch in punches:
        if seen_out:
            add_anomaly("UNPARSEABLE")
            continue

        if pending_break is not None:
            if punch.kind is PunchKind.BREAK_END:
                breaks.append((pending_break, punch))
                pending_break = None
                continue
            breaks.append((pending_break, None))
            pending_break = None
            add_anomaly("OPEN_BREAK")
            if punch.kind is not PunchKind.OUT:
                add_anomaly("UNPARSEABLE")

        if punch.kind is PunchKind.IN:
            if started:
                add_anomaly("UNPARSEABLE")
            else:
                started = True
                in_ts = punch.ts
        elif punch.kind is PunchKind.BREAK_START:
            if not started:
                add_anomaly("UNPARSEABLE")
            pending_break = punch
        elif punch.kind is PunchKind.BREAK_END:
            if not started:
                add_anomaly("UNPARSEABLE")
            else:
                # A BREAK_END without a pending BREAK_START is invalid.
                add_anomaly("UNPARSEABLE")
        elif punch.kind is PunchKind.OUT:
            if out_ts is None:
                out_ts = punch.ts
            seen_out = True

    if pending_break is not None:
        breaks.append((pending_break, None))
        add_anomaly("OPEN_BREAK")
    if punches and punches[-1].kind is not PunchKind.OUT:
        add_anomaly("MISSING_OUT")
    return in_ts, out_ts, breaks, anomalies


def _expected_times(
    work_date: date,
    sched: Sched | None,
    punches: list[Punch],
    in_ts: datetime | None,
    out_ts: datetime | None,
) -> tuple[datetime, datetime]:
    """Derive reference bounds, using an eight-hour shift when unscheduled."""
    if sched is not None:
        return sched.start, sched.end
    if in_ts is not None:
        return in_ts, in_ts + timedelta(hours=8)
    if out_ts is not None:
        return out_ts - timedelta(hours=8), out_ts
    if punches:
        return punches[0].ts, punches[0].ts + timedelta(hours=8)
    midnight = datetime.combine(work_date, time.min)
    return midnight, midnight + timedelta(hours=8)


def build_shifts(
    punches: Iterable[Punch],
    schedules: Iterable[Sched],
    dup_window_minutes: int,
) -> dict[date, Shift]:
    """Build one reconstructed shift per work date without modifying inputs."""
    if dup_window_minutes < 0:
        raise ValueError("dup_window_minutes must be non-negative")

    punch_groups: dict[date, list[Punch]] = {}
    schedule_by_date: dict[date, Sched] = {}
    for punch in punches:
        if not punch.voided:
            punch_groups.setdefault(punch.work_date, []).append(punch)
    for sched in schedules:
        schedule_by_date[sched.work_date] = sched

    shifts: dict[date, Shift] = {}
    for work_date in sorted(punch_groups.keys() | schedule_by_date.keys()):
        raw_punches = sorted(
            punch_groups.get(work_date, []), key=lambda item: (item.ts, item.id)
        )
        deduped, duplicates = _deduplicate(raw_punches, dup_window_minutes)
        in_ts, out_ts, breaks, anomalies = _parse_sequence(deduped)
        sched = schedule_by_date.get(work_date)
        expected_start, expected_end = _expected_times(
            work_date, sched, deduped, in_ts, out_ts
        )
        employee_id = (
            sched.employee_id
            if sched is not None
            else deduped[0].employee_id
            if deduped
            else ""
        )
        shifts[work_date] = Shift(
            employee_id=employee_id,
            work_date=work_date,
            sched=sched,
            punches=deduped,
            duplicates=duplicates,
            in_ts=in_ts,
            out_ts=out_ts,
            breaks=breaks,
            anomalies=anomalies,
            expected_start=expected_start,
            expected_end=expected_end,
        )
    return shifts
