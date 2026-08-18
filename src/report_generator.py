"""Statistics computation engine for Strava club activities."""
from dataclasses import asdict, dataclass, field

from nominal_roll import NominalRoll


@dataclass
class AthleteStats:
    """Accumulated per-athlete totals for one report period."""

    name: str
    athlete_id: int | None = None
    unit: str = ""
    company: str = ""

    km: float = 0.0
    elev: float = 0.0
    time_s: float = 0.0
    speeds: list = field(default_factory=list)
    count_acts: int = 0
    longest: float = 0.0
    devices: set = field(default_factory=set)

    # Climber — only runs with >= 8 m+/km (real hills)
    climber_run_elev: float = 0.0
    climber_run_km: float = 0.0

    break_time: float = 0.0

    def add_activity(self, act: dict) -> tuple:
        """Accumulate one activity's stats. Returns (dist_km, elev) for the
        caller's running club-wide totals."""
        dist_km = act.get("distance", 0) / 1000
        elev = act.get("total_elevation_gain", 0)
        time_s = act.get("moving_time", 0)
        elapsed = act.get("elapsed_time", 0)
        speed = (act.get("distance", 0) / time_s) if time_s > 0 else 0
        dev = act.get("device_name", "")

        self.km += dist_km
        self.elev += elev
        self.time_s += time_s
        self.count_acts += 1
        if dist_km > self.longest:
            self.longest = dist_km
        if speed > 0 and dist_km > 0.5:
            self.speeds.append(speed)
        if dev:
            self.devices.add(dev)
        if dist_km >= 5 and dist_km > 0 and (elev / dist_km) >= 8:
            self.climber_run_elev += elev
            self.climber_run_km += dist_km

        self.break_time += max(0, elapsed - time_s)

        return dist_km, elev

    @property
    def avg_speed(self):
        return sum(self.speeds) / len(self.speeds) if self.speeds else None

    @staticmethod
    def fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"

    @staticmethod
    def spd_kmh(ms: float) -> str:
        return f"{ms * 3.6:.1f} km/h"

    def to_leaderboard_entry(self, leader_km: float) -> dict:
        gap = leader_km - self.km
        avg_speed = self.avg_speed
        entry = LeaderboardEntry(
            name=self.name,
            athlete_id=self.athlete_id,
            km=round(self.km, 1),
            elev=round(self.elev),
            time=self.fmt_time(self.time_s),
            time_s=int(self.time_s),
            acts=self.count_acts,
            avg_speed=self.spd_kmh(avg_speed) if avg_speed is not None else "–",
            avg_speed_ms=round(avg_speed, 4) if avg_speed is not None else 0,
            longest=round(self.longest, 1),
            gap=f"–{gap:.1f}" if gap > 0 else "leader",
            elev_per_km=round(self.elev / self.km, 1) if self.km > 0 else None,
            unit=self.unit,
            company=self.company,
        )
        return asdict(entry)


@dataclass
class LeaderboardEntry:
    name: str
    athlete_id: int | None
    km: float
    elev: float
    time: str
    time_s: int
    acts: int
    avg_speed: str
    avg_speed_ms: float
    longest: float
    gap: str
    elev_per_km: float | None
    unit: str
    company: str


@dataclass
class Award:
    name: str
    athlete_id: int | None
    value: str


@dataclass
class FunStat:
    name: str
    value: str


def compute_stats(activities: list, members: list = None, roll: NominalRoll = None) -> dict:
    """
    Compute all statistics from club activities.
    members: optional member list (for building Strava profile links).
    roll: optional NominalRoll for name resolution + unit/company lookup.
    """
    # Build name -> athlete id map from members (if available)
    member_map = {}
    if members:
        for m in members:
            key = NominalRoll.full_name(m).lower()
            if m.get("id"):
                member_map[key] = m["id"]
    if not activities and not members:
        return {}

    total_km = 0.0
    total_elev = 0.0
    athletes: dict = {}

    def get_athlete(name):
        if name not in athletes:
            uc = roll.unit_company(name) if roll else {}
            athletes[name] = AthleteStats(name, unit=uc.get("unit", ""), company=uc.get("company", ""))
        return athletes[name]

    for act in activities:
        athlete_dict = act.get("athlete", {})
        raw_name = NominalRoll.full_name(athlete_dict, default_firstname="?")
        name = roll.resolve(raw_name) if roll else raw_name

        stats = get_athlete(name)
        if stats.athlete_id is None and raw_name.lower() in member_map:
            stats.athlete_id = member_map[raw_name.lower()]

        dist_km, elev = stats.add_activity(act)
        total_km += dist_km
        total_elev += elev

    def top(d, reverse=True):
        return sorted(d.items(), key=lambda x: x[1], reverse=reverse)

    # Device stats — count unique runners per device
    device_athlete_count: dict = {}
    for a in athletes.values():
        for dev in a.devices:
            device_athlete_count[dev] = device_athlete_count.get(dev, 0) + 1

    def _device_sort(item):
        d, c = item
        dl = d.lower()
        if "strava" in dl:
            return (3, 0, dl)
        if "rouvy" in dl:
            return (2, 0, dl)
        if "zwift" in dl:
            return (1, 0, dl)
        return (0, -c, dl)

    device_stats = [
        {"device": d, "count": c}
        for d, c in sorted(device_athlete_count.items(), key=_device_sort)
        if d
    ]

    km_rank = top({a.name: a.km for a in athletes.values()})

    # Award rankings
    award_km_rank = top({a.name: a.km for a in athletes.values() if a.count_acts > 0})
    award_elev_rank = top({a.name: a.elev for a in athletes.values() if a.count_acts > 0})
    award_time_rank = top({a.name: a.time_s for a in athletes.values() if a.count_acts > 0})
    award_fast_rank = top({a.name: a.avg_speed for a in athletes.values() if a.avg_speed is not None})
    award_long_rank = top({a.name: a.longest for a in athletes.values() if a.count_acts > 0})

    # Leaderboard — all runners sorted by km
    leader_km = km_rank[0][1] if km_rank else 0
    leaderboard = [athletes[name].to_leaderboard_entry(leader_km) for name, _km in km_rank]

    # Add zero-row entries for members with no activities this period
    active_names = set(athletes.keys())
    if members:
        for m in members:
            name = NominalRoll.full_name(m)
            if roll:
                name = roll.resolve(name)
            if name and name not in active_names:
                uc = roll.unit_company(name) if roll else {}
                zero_stats = AthleteStats(
                    name,
                    athlete_id=member_map.get(name.lower()),
                    unit=uc.get("unit", ""),
                    company=uc.get("company", ""),
                )
                leaderboard.append(zero_stats.to_leaderboard_entry(leader_km))
                active_names.add(name)

    def award(rank, val_fn):
        if not rank:
            return None
        name, val = rank[0]
        return asdict(Award(name=name, athlete_id=athletes[name].athlete_id, value=val_fn(val)))

    # Climber — avg m+/km from runs with >= 8 m+/km, at least 30 km total
    elev_per_km = {
        a.name: a.climber_run_elev / a.climber_run_km
        for a in athletes.values()
        if a.climber_run_km >= 30
    }

    break_rank = top({a.name: a.break_time for a in athletes.values()})
    climber_rank = top(elev_per_km)

    def fun(name, value):
        return asdict(FunStat(name=name, value=value))

    fun_stats = {
        "breaks": fun(
            break_rank[0][0],
            f"{int(break_rank[0][1] // 60)} min of rest",
        ) if break_rank and break_rank[0][1] > 60 else None,
    }

    climber_award = None
    if climber_rank and climber_rank[0][1] > 5:
        name, val = climber_rank[0]
        climber_award = asdict(Award(name=name, athlete_id=athletes[name].athlete_id, value=f"{val:.1f} m+/km"))

    # Flat runner — lowest m+/km, at least 50 km
    flatrunner_rank = top(
        {a.name: a.elev / a.km for a in athletes.values() if a.km >= 50},
        reverse=False
    )
    flatrunner_award = None
    if flatrunner_rank:
        name, val = flatrunner_rank[0]
        flatrunner_award = asdict(Award(name=name, athlete_id=athletes[name].athlete_id, value=f"{val:.1f} m+/km"))

    return {
        "total_km": total_km,
        "total_elev": total_elev,
        "run_count": len(activities),
        "athlete_count": len(members or []),
        "leaderboard": leaderboard,
        "fun_stats": fun_stats,
        # Main awards
        "king_km":     award(award_km_rank,    lambda v: f"{v:.1f} km"),
        "king_elev":   award(award_elev_rank,  lambda v: f"{v:,.0f} m elevation".replace(",", " ")),
        "marathoner":  award(award_time_rank,  lambda v: AthleteStats.fmt_time(v)),
        "fastest":     award(award_fast_rank,  lambda v: AthleteStats.spd_kmh(v)),
        "longest":     award(award_long_rank,  lambda v: f"{v:.1f} km"),
        "climber":     climber_award,
        "flatrunner":  flatrunner_award,
        "device_stats": device_stats,
    }
