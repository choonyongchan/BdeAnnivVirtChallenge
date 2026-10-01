"""Leaderboard, award and fun statistics over weekly.csv / activities.csv / members.csv rows.
Totals come from Strava's weekly figures; the best-effort ledger only feeds the per-activity awards.
Athletes join by athlete_id (weekly/activities <-> members) before falling back to name resolution."""
from collections import Counter
from dataclasses import dataclass, field

from .names import NominalRoll


def _num(value) -> float:
    """A CSV cell as a float; blank or unparseable -> 0.0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class AthleteStats:
    """Accumulated per-athlete totals for one report period."""

    name: str                  # canonical roster name, or the raw Strava name when unmatched
    unit: str = ""
    company: str = ""          # unit-qualified, e.g. "40SAR/Cougar"

    km: float = 0.0
    elev: float = 0.0          # metres
    time_s: float = 0.0        # moving time
    speeds: list = field(default_factory=list)  # m/s, runs over 0.5 km only (feeds avg_speed)
    count_acts: int = 0
    longest: float = 0.0       # km
    devices: set = field(default_factory=set)

    climber_run_elev: float = 0.0  # from "real hill" runs only (>= 5 km and >= 8 m+/km)
    climber_run_km: float = 0.0

    break_time: float = 0.0    # elapsed minus moving, i.e. time spent stopped

    def add_week(self, week: dict) -> None:
        """Accumulate one weekly.csv row into the totals."""
        self.km += _num(week.get("distance_m")) / 1000
        self.elev += _num(week.get("elev_gain_m"))
        self.time_s += _num(week.get("moving_time_s"))
        self.count_acts += int(_num(week.get("activities")))

    def add_activity(self, act: dict) -> None:
        """Accumulate one ledger activity into the per-activity awards (totals come from add_week)."""
        dist_m = _num(act.get("distance_m"))
        dist_km = dist_m / 1000
        elev = _num(act.get("elev_gain_m"))
        time_s = _num(act.get("moving_time_s"))
        elapsed = _num(act.get("elapsed_time_s"))
        speed = (dist_m / time_s) if time_s > 0 else 0
        dev = act.get("device_name", "") or ""

        if dist_km > self.longest:
            self.longest = dist_km
        if speed > 0 and dist_km > 0.5:
            self.speeds.append(speed)
        if dev:
            self.devices.add(dev)
        if dist_km >= 5 and (elev / dist_km) >= 8:
            self.climber_run_elev += elev
            self.climber_run_km += dist_km

        self.break_time += max(0, elapsed - time_s)

    @property
    def avg_speed(self):
        """Mean speed in m/s across qualifying activities, or None."""
        return sum(self.speeds) / len(self.speeds) if self.speeds else None

    @staticmethod
    def fmt_time(seconds: float) -> str:
        """Format a duration as ``"2h 5m"``."""
        h, rem = divmod(int(seconds), 3600)
        return f"{h}h {rem // 60}m"

    @staticmethod
    def spd_kmh(ms: float) -> str:
        """Format a m/s speed as ``"12.3 km/h"``."""
        return f"{ms * 3.6:.1f} km/h"

    def to_leaderboard_entry(self, leader_km: float) -> dict:
        """Render these totals as one display-ready leaderboard row."""
        gap = leader_km - self.km
        avg_speed = self.avg_speed
        return {
            "name": self.name,
            "km": round(self.km, 1),
            "elev": round(self.elev),
            "time": self.fmt_time(self.time_s),
            "time_s": int(self.time_s),
            "acts": self.count_acts,
            "avg_speed": self.spd_kmh(avg_speed) if avg_speed is not None else "–",
            "avg_speed_ms": round(avg_speed, 4) if avg_speed is not None else 0,
            "longest": round(self.longest, 1),
            "gap": f"–{gap:.1f}" if gap > 0 else "leader",
            "elev_per_km": round(self.elev / self.km, 1) if self.km > 0 else None,
            "unit": self.unit,
            "company": self.company,
        }


@dataclass
class ReportStats:
    """Everything one reporting period contributes to the dashboard; an empty period is ReportStats().
    Each award is {"name", "value"}, or None when nobody qualified."""

    total_km: float = 0.0
    total_elev: float = 0.0
    run_count: int = 0
    athlete_count: int = 0     # members.csv rows, runners or not
    no_unit_count: int = 0     # roster-matched athletes whose roll Unit is blank
    leaderboard: list = field(default_factory=list)  # km-ranked, incl. zero rows for non-runners
    fun_stats: dict = field(default_factory=dict)
    king_km: dict | None = None
    king_elev: dict | None = None
    marathoner: dict | None = None  # most moving time
    fastest: dict | None = None
    longest: dict | None = None
    climber: dict | None = None     # steepest sustained climber (>= 30 hill-km)
    flatrunner: dict | None = None  # flattest route over >= 50 km
    device_stats: list = field(default_factory=list)  # runners per device, hardware first


def _device_sort(item):
    """Sort key placing real hardware ahead of virtual platforms."""
    d, c = item
    dl = d.lower()
    if "strava" in dl:
        return (3, 0, dl)
    if "rouvy" in dl:
        return (2, 0, dl)
    if "zwift" in dl:
        return (1, 0, dl)
    return (0, -c, dl)


def _new_athlete(name: str, roll: NominalRoll) -> AthleteStats:
    """Empty accumulator with unit/company filled in from the roll."""
    uc = roll.unit_company(name)
    return AthleteStats(
        name,
        unit=uc.get("unit", ""),
        company=uc.get("company", ""),
    )


def _roster_name(act: dict, roll: NominalRoll, member_by_id: dict) -> str:
    """The roster name for an activity: via its athlete_id's member, else its own name."""
    member = member_by_id.get(str(act.get("athlete_id") or ""))
    raw = member.get("name", "") if member else act.get("athlete_name", "")
    return roll.resolve(raw)


def _accumulate_athletes(weeks: list, activities: list, roll: NominalRoll, member_by_id: dict) -> dict:
    """Fold every weekly row and ledger activity into per-athlete accumulators keyed by resolved name."""
    athletes: dict = {}

    def athlete(row):
        name = _roster_name(row, roll, member_by_id)
        if name not in athletes:
            athletes[name] = _new_athlete(name, roll)
        return athletes[name]

    for week in weeks:
        athlete(week).add_week(week)
    for act in activities:
        athlete(act).add_activity(act)
    return athletes


def _build_device_stats(athletes: dict) -> list:
    """Count distinct runners per recording device, unnamed devices dropped."""
    counts = Counter(dev for a in athletes.values() for dev in a.devices)
    return [
        {"device": d, "count": c}
        for d, c in sorted(counts.items(), key=_device_sort)
        if d
    ]


def _build_leaderboard(athletes: dict, members: list, roll: NominalRoll) -> list:
    """The km-ranked leaderboard, plus a zero row per non-running member so it shows the whole unit."""
    ranked = sorted(athletes.values(), key=lambda a: a.km, reverse=True)
    leader_km = ranked[0].km if ranked else 0
    leaderboard = [a.to_leaderboard_entry(leader_km) for a in ranked]

    listed = set(athletes.keys())
    for m in members:
        name = roll.resolve(m.get("name", ""))
        if name and name not in listed:
            leaderboard.append(_new_athlete(name, roll).to_leaderboard_entry(leader_km))
            listed.add(name)

    return leaderboard


def _award(values: dict, val_fn, pick=max) -> dict | None:
    """Winner of one category, or None when nobody qualified."""
    if not values:
        return None
    name = pick(values, key=values.get)
    return {"name": name, "value": val_fn(values[name])}


def _compute_awards(athletes: dict) -> dict:
    """Pick the winner of every award category."""
    active = [a for a in athletes.values() if a.count_acts > 0]

    climber = {
        a.name: a.climber_run_elev / a.climber_run_km
        for a in athletes.values()
        if a.climber_run_km >= 30 and a.climber_run_elev / a.climber_run_km > 5
    }
    flat = {a.name: a.elev / a.km for a in athletes.values() if a.km >= 50}

    return {
        "king_km":    _award({a.name: a.km for a in active}, lambda v: f"{v:.1f} km"),
        "king_elev":  _award({a.name: a.elev for a in active},
                             lambda v: f"{v:,.0f} m elevation".replace(",", " ")),
        "marathoner": _award({a.name: a.time_s for a in active}, AthleteStats.fmt_time),
        "fastest":    _award({a.name: a.avg_speed for a in athletes.values()
                              if a.avg_speed is not None}, AthleteStats.spd_kmh),
        "longest":    _award({a.name: a.longest for a in active}, lambda v: f"{v:.1f} km"),
        "climber":    _award(climber, lambda v: f"{v:.1f} m+/km"),
        "flatrunner": _award(flat, lambda v: f"{v:.1f} m+/km", pick=min),
    }


def _compute_fun_stats(athletes: dict) -> dict:
    """Novelty statistics shown alongside the main awards."""
    breaks = None
    times = {a.name: a.break_time for a in athletes.values()}
    if times:
        name = max(times, key=times.get)
        if times[name] > 60:
            breaks = {"name": name, "value": f"{int(times[name] // 60)} min of rest"}
    return {"breaks": breaks}


def compute_stats(weeks: list, activities: list, members: list, roll: NominalRoll) -> ReportStats:
    """All leaderboard, award and fun statistics for one period: totals from weekly rows,
    per-activity awards from the ledger. members adds zero rows for non-runners and the athlete_id -> name join."""
    if not weeks and not activities and not members:
        return ReportStats()

    member_by_id = {str(m.get("athlete_id") or ""): m for m in members}
    athletes = _accumulate_athletes(weeks, activities, roll, member_by_id)
    leaderboard = _build_leaderboard(athletes, members, roll)

    return ReportStats(
        total_km=sum(a.km for a in athletes.values()),
        total_elev=sum(a.elev for a in athletes.values()),
        run_count=sum(a.count_acts for a in athletes.values()),
        athlete_count=len(members),
        # unit_company() is empty only for someone off the roll, never for a blank unit.
        no_unit_count=sum(1 for r in leaderboard
                          if roll.unit_company(r["name"]) and not r["unit"]),
        leaderboard=leaderboard,
        fun_stats=_compute_fun_stats(athletes),
        device_stats=_build_device_stats(athletes),
        **_compute_awards(athletes),
    )
