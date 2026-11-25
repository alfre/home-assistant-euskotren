"""Read and query the Euskotren GTFS timetable."""

import csv
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO, TextIOWrapper
from itertools import pairwise
from math import cos, hypot, radians
from typing import NamedTuple
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from .const import SHARED_LINE, TIME_ZONE, TOPO_LINES

TZ = ZoneInfo(TIME_ZONE)
DAY = 86400
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
MAX_TRANSFER = 3600
MAX_SHAPE_GAP_METERS = 1000
MAX_STOP_OFFSET_METERS = 250

type Coordinates = tuple[float, float]


def _rows(
    archive: ZipFile, name: str, *, optional: bool = False
) -> Iterator[dict[str, str]]:
    filename = f"{name}.txt"
    if optional and filename not in archive.namelist():
        return
    if archive.getinfo(filename).file_size > 32 * 1024 * 1024:
        raise ValueError(f"GTFS table too large: {filename}")
    with TextIOWrapper(
        archive.open(filename), encoding="utf-8-sig", newline=""
    ) as source:
        yield from csv.DictReader(source)


def _seconds(value: str) -> int:
    hours, minutes, seconds = map(int, value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _service_epoch(day: date) -> int:
    # GTFS defines 00:00 as noon on the service date minus twelve elapsed hours.
    return int(datetime.combine(day, time(12), TZ).timestamp()) - 12 * 3600


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, TZ).isoformat()


class Station(NamedTuple):
    id: str
    name: str
    latitude: float
    longitude: float


class Call(NamedTuple):
    stop: str
    station: str
    sequence: int
    arrival: int
    departure: int
    pickup: bool
    dropoff: bool
    distance: float | None


class Trip(NamedTuple):
    id: str
    line: str
    service: str
    headsign: str
    shape: str
    calls: tuple[Call, ...]


class Calendar(NamedTuple):
    start: date
    end: date
    weekdays: tuple[bool, ...]


class ShapePoint(NamedTuple):
    distance: float
    latitude: float
    longitude: float


class Prediction(NamedTuple):
    arrival: int
    departure: int
    skipped: bool = False
    live: bool = False
    arrival_live: bool = False
    departure_live: bool = False


class StopUpdate(NamedTuple):
    sequence: int | None
    stop: str
    relationship: int
    arrival: tuple[int | None, int | None]
    departure: tuple[int | None, int | None]


class TripUpdate(NamedTuple):
    cancelled: bool
    delay: int | None
    stops: tuple[StopUpdate, ...]


class Realtime(NamedTuple):
    timestamp: int
    trips: dict[tuple[str, date], TripUpdate]


class Run(NamedTuple):
    trip: Trip
    epoch: int
    times: tuple[Prediction, ...]


class Leg(NamedTuple):
    run: Run
    start: int
    end: int

    @property
    def departure(self) -> int:
        return self.run.times[self.start].departure

    @property
    def arrival(self) -> int:
        return self.run.times[self.end].arrival


@dataclass
class Schedule:
    """In-memory index of the Topo and the corridor it shares with E1."""

    stations: dict[str, Station]
    trips: tuple[Trip, ...]
    calendar: dict[str, Calendar]
    exceptions: dict[tuple[str, date], bool]
    transfers: dict[str, int | None]
    shapes: dict[str, list[ShapePoint]]
    first_day: date
    last_day: date
    latest_offset: int
    line_paths: dict[str, list[Coordinates]] = field(init=False)

    def __post_init__(self) -> None:
        """Build each line's overview from its most complete published trip."""
        longest: dict[str, Trip] = {}
        for trip in self.trips:
            previous = longest.get(trip.line)
            if previous is None or (len(trip.calls), trip.shape in self.shapes) > (
                len(previous.calls),
                previous.shape in self.shapes,
            ):
                longest[trip.line] = trip
        self.line_paths = {}
        for line, trip in longest.items():
            stations = [self.stations[call.station] for call in trip.calls]
            self.line_paths[line] = _route_geometry(
                self.shapes.get(trip.shape, []),
                trip.calls,
                [(station.latitude, station.longitude) for station in stations],
            )

    def catalog(self) -> dict:
        return {
            "first_date": self.first_day.isoformat(),
            "last_date": self.last_day.isoformat(),
            "timezone": TIME_ZONE,
            "line_paths": self.line_paths,
            "stations": [
                station._asdict()
                for station in sorted(
                    self.stations.values(), key=lambda item: item.name.casefold()
                )
            ],
        }

    def _runs(self, day: date, live: Realtime | None) -> Iterator[Run]:
        first = day - timedelta(days=max(1, self.latest_offset // DAY))
        last = min(self.last_day, day + timedelta(days=1))
        for offset in range((last - first).days + 1):
            service_day = first + timedelta(days=offset)
            epoch = _service_epoch(service_day)
            for trip in self.trips:
                active = self.exceptions.get((trip.service, service_day))
                if active is None:
                    rule = self.calendar.get(trip.service)
                    active = (
                        rule is not None
                        and rule.start <= service_day <= rule.end
                        and rule.weekdays[service_day.weekday()]
                    )
                if not active:
                    continue
                update = live.trips.get((trip.id, service_day)) if live else None
                if update and update.cancelled:
                    continue
                yield Run(trip, epoch, _predictions(trip.calls, epoch, update))

    def search(
        self,
        origin: str,
        destination: str,
        day: date,
        after: time,
        limit: int,
        live: Realtime | None = None,
    ) -> dict:
        if origin not in self.stations or destination not in self.stations:
            raise ValueError("unknown_station")
        if origin == destination:
            raise ValueError("same_station")
        if not self.first_day <= day <= self.last_day:
            raise ValueError("date_out_of_range")
        start = int(datetime.combine(day, after, TZ).timestamp())
        end = int(datetime.combine(day + timedelta(days=1), time(), TZ).timestamp())
        direct: list[tuple[Leg, ...]] = []
        first_legs: list[Leg] = []
        connections: dict[str, list[Leg]] = defaultdict(list)
        for run in self._runs(day, live):
            boardings: list[int] = []
            for index, (call, prediction) in enumerate(
                zip(run.trip.calls, run.times, strict=True)
            ):
                if prediction.skipped:
                    continue
                if call.dropoff and call.station == destination:
                    for boarding in boardings:
                        leg = Leg(run, boarding, index)
                        if run.trip.calls[boarding].station == origin:
                            if start <= leg.departure < end:
                                direct.append((leg,))
                        else:
                            connections[run.trip.calls[boarding].station].append(leg)
                elif call.dropoff and call.station != origin:
                    first_legs.extend(
                        Leg(run, boarding, index)
                        for boarding in boardings
                        if run.trip.calls[boarding].station == origin
                        and start <= run.times[boarding].departure < end
                    )
                if call.pickup and call.station != destination:
                    boardings.append(index)

        for legs in connections.values():
            legs.sort(key=lambda leg: leg.departure)
        departure_index = {
            station: [leg.departure for leg in legs]
            for station, legs in connections.items()
        }
        direct_boardings = {_boarding_key(legs[0]) for legs in direct}
        best_connection: dict[tuple[str, int, int], tuple[Leg, Leg]] = {}
        for first in first_legs:
            key = _boarding_key(first)
            if key in direct_boardings:
                continue
            station = first.run.trip.calls[first.end].station
            transfer = self.transfers.get(station, 120)
            if station not in connections or transfer is None:
                continue
            departures = departure_index[station]
            left = bisect_left(departures, first.arrival + transfer)
            right = bisect_right(departures, first.arrival + MAX_TRANSFER)
            visited = {
                call.station for call in first.run.trip.calls[first.start : first.end]
            }
            for second in connections[station][left:right]:
                if first.run is second.run or second.arrival < second.departure:
                    continue
                if any(
                    call.station in visited
                    for call in second.run.trip.calls[second.start + 1 : second.end + 1]
                ):
                    continue
                current = best_connection.get(key)
                if current is None or _connection_rank(
                    first, second
                ) < _connection_rank(*current):
                    best_connection[key] = (first, second)

        # Keep an earlier boarding that catches a later direct train at a shared stop.
        options = sorted(
            [*direct, *best_connection.values()],
            key=lambda legs: (-legs[0].departure, legs[-1].arrival, len(legs)),
        )
        best_direct_arrival = float("inf")
        journeys = []
        for legs in options:
            if legs[-1].arrival < legs[0].departure:
                continue
            if len(legs) == 1:
                best_direct_arrival = min(best_direct_arrival, legs[-1].arrival)
            elif legs[-1].arrival > best_direct_arrival:
                continue
            journeys.append(legs)
        journeys.sort(key=lambda legs: (legs[0].departure, legs[-1].arrival, len(legs)))
        return {
            "date": day.isoformat(),
            "timezone": TIME_ZONE,
            "realtime_updated_at": _iso(live.timestamp) if live else None,
            "journeys": [self._journey(legs) for legs in journeys[:limit]],
        }

    def train_positions(self, now: datetime, live: Realtime | None = None) -> dict:
        """Estimate positions for active trips at the current local time."""
        timestamp = int(now.timestamp())
        trains = []
        for run in self._runs(now.astimezone(TZ).date(), live):
            calls = [
                (index, prediction)
                for index, prediction in enumerate(run.times)
                if not prediction.skipped
            ]
            if len(calls) < 2 or not (
                calls[0][1].departure <= timestamp < calls[-1][1].arrival
            ):
                continue
            for (start, before), (end, after) in pairwise(calls):
                if before.departure <= timestamp < after.arrival:
                    trains.append(self._train_in_motion(run, start, end, timestamp))
                    break
                if before.arrival <= timestamp < before.departure:
                    station = self.stations[run.trip.calls[start].station]
                    next_station = self.stations[run.trip.calls[end].station]
                    trains.append(
                        {
                            **_train_identity(run),
                            "latitude": station.latitude,
                            "longitude": station.longitude,
                            "at_station": station.name,
                            "next_station": next_station.name,
                            "arrival_timestamp": after.arrival,
                            "realtime": before.live or after.arrival_live,
                        }
                    )
                    break
        return {
            "calculated_at": timestamp,
            "realtime_updated_at": _iso(live.timestamp) if live else None,
            "trains": trains,
        }

    def _train_in_motion(self, run: Run, start: int, end: int, timestamp: int) -> dict:
        calls = run.trip.calls[start : end + 1]
        stations = [self.stations[call.station] for call in calls]
        points = _route_geometry(
            self.shapes.get(run.trip.shape, []),
            calls,
            [(station.latitude, station.longitude) for station in stations],
        )
        path = [(*points[0], 0.0)]
        for a, b in pairwise(points):
            distance = _meters(a, b)
            if distance:
                path.append((*b, path[-1][2] + distance))
        before, after = run.times[start], run.times[end]
        fraction = (timestamp - before.departure) / (after.arrival - before.departure)
        latitude, longitude = _position_at_fraction(path, fraction)
        return {
            **_train_identity(run),
            "latitude": latitude,
            "longitude": longitude,
            "next_station": stations[-1].name,
            "departure_timestamp": before.departure,
            "arrival_timestamp": after.arrival,
            "path": path,
            "realtime": before.departure_live or after.arrival_live,
        }

    def _journey(self, legs: tuple[Leg, ...]) -> dict:
        first, last = legs[0], legs[-1]
        serialized = [self._leg(leg) for leg in legs]
        return {
            "departure": _iso(first.departure),
            "arrival": _iso(last.arrival),
            "scheduled_departure": serialized[0]["scheduled_departure"],
            "scheduled_arrival": serialized[-1]["scheduled_arrival"],
            "duration_minutes": round((last.arrival - first.departure) / 60, 1),
            "realtime": any(leg["realtime"] for leg in serialized),
            "departure_realtime": serialized[0]["departure_realtime"],
            "arrival_realtime": serialized[-1]["arrival_realtime"],
            "transfers": len(legs) - 1,
            "legs": serialized,
        }

    def _leg(self, leg: Leg) -> dict:
        trip, epoch, times = leg.run
        calls = trip.calls[leg.start : leg.end + 1]
        predictions = times[leg.start : leg.end + 1]
        stops = [
            {
                **self.stations[call.station]._asdict(),
                "arrival": _iso(prediction.arrival),
                "departure": _iso(prediction.departure),
                "skipped": prediction.skipped,
                "realtime": prediction.live,
            }
            for call, prediction in zip(calls, predictions, strict=True)
        ]
        line = _route_geometry(
            self.shapes.get(trip.shape, []),
            calls,
            [(stop["latitude"], stop["longitude"]) for stop in stops],
        )
        return {
            "line": trip.line,
            "headsign": trip.headsign,
            "origin": stops[0],
            "destination": stops[-1],
            "departure": _iso(leg.departure),
            "arrival": _iso(leg.arrival),
            "scheduled_departure": _iso(epoch + calls[0].departure),
            "scheduled_arrival": _iso(epoch + calls[-1].arrival),
            "realtime": (
                predictions[0].departure_live
                or predictions[-1].arrival_live
                or any(item.live or item.skipped for item in predictions[1:-1])
            ),
            "departure_realtime": predictions[0].departure_live,
            "arrival_realtime": predictions[-1].arrival_live,
            "stops": stops,
            "polyline": [list(point) for point in line],
        }


def _boarding_key(leg: Leg) -> tuple[str, int, int]:
    return leg.run.trip.id, leg.run.epoch, leg.start


def _connection_rank(first: Leg, second: Leg) -> tuple[int, int, int]:
    return second.arrival, -first.end, second.departure


def _train_identity(run: Run) -> dict:
    return {
        "id": f"{run.trip.id}|{run.epoch}",
        "line": run.trip.line,
        "headsign": run.trip.headsign,
    }


def _position_at_fraction(
    path: list[tuple[float, float, float]], fraction: float
) -> Coordinates:
    distance = path[-1][2] * fraction
    index = bisect_left(path, distance, key=lambda point: point[2])
    b = path[index]
    if index == 0:
        return b[0], b[1]
    a = path[index - 1]
    progress = (distance - a[2]) / (b[2] - a[2])
    return (a[0] + (b[0] - a[0]) * progress, a[1] + (b[1] - a[1]) * progress)


def _meters(a: Coordinates, b: Coordinates) -> float:
    east = radians(b[1] - a[1]) * cos(radians((a[0] + b[0]) / 2))
    north = radians(b[0] - a[0])
    return 6371000 * hypot(east, north)


def _shape_coordinates(
    points: list[ShapePoint], start: float | None, end: float | None
) -> list[Coordinates]:
    if start is None or end is None:
        return []
    left = bisect_left(points, start, key=lambda point: point.distance)
    right = bisect_right(points, end, key=lambda point: point.distance)
    return [(point.latitude, point.longitude) for point in points[left:right]]


def _continuous_parts(points: list[Coordinates]) -> list[list[Coordinates]]:
    cuts = [
        i
        for i, (a, b) in enumerate(pairwise(points), 1)
        if _meters(a, b) > MAX_SHAPE_GAP_METERS
    ]
    return [points[left:right] for left, right in pairwise([0, *cuts, len(points)])]


def _route_geometry(
    points: list[ShapePoint], calls: tuple[Call, ...], stops: list[Coordinates]
) -> list[Coordinates]:
    """Keep continuous track between published stops when a shape jumps between alignments."""
    shape = _shape_coordinates(points, calls[0].distance, calls[-1].distance)
    if not shape:
        return stops
    if len(_continuous_parts(shape)) == 1:
        return [stops[0], *shape, stops[-1]]

    path = [stops[0]]
    for (start, origin), (end, destination) in pairwise(zip(calls, stops, strict=True)):
        candidates = []
        for part in _continuous_parts(
            _shape_coordinates(points, start.distance, end.distance)
        ):
            if len(part) < 2:
                continue
            start_gap = _meters(origin, part[0])
            end_gap = _meters(destination, part[-1])
            if max(start_gap, end_gap) <= MAX_STOP_OFFSET_METERS:
                candidates.append((start_gap + end_gap, part))
        # A duplicated alignment often begins or ends at one of the stops.
        # Keep a single continuous part that reaches both; otherwise join the stops.
        segment = (
            min(candidates, key=lambda candidate: candidate[0])[1]
            if candidates
            else [origin, destination]
        )
        for point in segment:
            if point != path[-1]:
                path.append(point)
    if path[-1] != stops[-1]:
        path.append(stops[-1])
    return path


def _predictions(
    calls: tuple[Call, ...], epoch: int, update: TripUpdate | None
) -> tuple[Prediction, ...]:
    by_sequence = (
        {stop.sequence: stop for stop in update.stops if stop.sequence is not None}
        if update
        else {}
    )
    by_stop = (
        {stop.stop: stop for stop in update.stops if stop.sequence is None}
        if update
        else {}
    )
    delay = update.delay if update else None
    predictions = []
    for call in calls:
        scheduled_arrival = epoch + call.arrival
        scheduled_departure = epoch + call.departure
        stop = by_sequence.get(call.sequence) or by_stop.get(call.stop)
        if stop and stop.relationship == 2:  # NO_DATA resets predictions at this stop.
            delay = None
        skipped = bool(stop and stop.relationship == 1)
        arrival_delay = departure_delay = None
        if stop and stop.relationship == 0:
            arrival, arrival_offset = stop.arrival
            departure, departure_offset = stop.departure
            arrival_delay = (
                arrival - scheduled_arrival if arrival is not None else arrival_offset
            )
            departure_delay = (
                departure - scheduled_departure
                if departure is not None
                else departure_offset
            )
        effective_arrival = arrival_delay if arrival_delay is not None else delay
        if departure_delay is None:
            departure_delay = effective_arrival
        if (
            stop
            and stop.relationship == 0
            and (arrival_delay is not None or stop.departure != (None, None))
        ):
            delay = departure_delay
        arrival = scheduled_arrival + (effective_arrival or 0)
        departure = max(arrival, scheduled_departure + (departure_delay or 0))
        predictions.append(
            Prediction(
                arrival,
                departure,
                skipped,
                effective_arrival is not None or departure_delay is not None,
                effective_arrival is not None,
                departure_delay is not None,
            )
        )
    return tuple(predictions)


def _read_trips(
    archive: ZipFile, stations: dict[str, Station], platform_to_station: dict[str, str]
) -> tuple[Trip, ...]:
    routes = {
        row["route_id"]: row["route_short_name"].upper()
        for row in _rows(archive, "routes")
        if row["route_type"] == "2"
        and row["route_short_name"].upper() in TOPO_LINES | {SHARED_LINE}
    }
    definitions = {
        row["trip_id"]: row
        for row in _rows(archive, "trips")
        if row["route_id"] in routes
    }
    calls = defaultdict(list)
    for row in _rows(archive, "stop_times"):
        stop = row["stop_id"]
        if row["trip_id"] not in definitions or stop not in platform_to_station:
            continue
        if not row["arrival_time"] or not row["departure_time"]:
            continue
        distance = row.get("shape_dist_traveled")
        calls[row["trip_id"]].append(
            Call(
                stop,
                platform_to_station[stop],
                int(row["stop_sequence"]),
                _seconds(row["arrival_time"]),
                _seconds(row["departure_time"]),
                row.get("pickup_type", "") in ("", "0"),
                row.get("drop_off_type", "") in ("", "0"),
                float(distance) if distance else None,
            )
        )
    topo_stations = {
        call.station
        for trip_id, trip_calls in calls.items()
        if routes[definitions[trip_id]["route_id"]] in TOPO_LINES
        for call in trip_calls
    }
    if not topo_stations:
        raise ValueError("No Topo trains in the timetable")
    trips = []
    used_stations = set()
    for trip_id, trip_calls in calls.items():
        row = definitions[trip_id]
        line = routes[row["route_id"]]
        trip_calls.sort(key=lambda call: call.sequence)
        if line == SHARED_LINE:
            indices = [
                index
                for index, call in enumerate(trip_calls)
                if call.station in topo_stations
            ]
            if not indices:
                continue
            trip_calls = trip_calls[indices[0] : indices[-1] + 1]
        if len(trip_calls) > 1:
            trips.append(
                Trip(
                    trip_id,
                    line,
                    row["service_id"],
                    row.get("trip_headsign", ""),
                    row.get("shape_id", ""),
                    tuple(trip_calls),
                )
            )
            used_stations.update(call.station for call in trip_calls)
    for station in stations.keys() - used_stations:
        del stations[station]
    if not trips:
        raise ValueError("No usable Topo trips")
    return tuple(trips)


def read_schedule(payload: bytes) -> Schedule:
    """Parse a provider ZIP without extracting any local files."""
    with ZipFile(BytesIO(payload)) as archive:
        rows = {row["stop_id"]: row for row in _rows(archive, "stops")}
        stations = {}
        platform_to_station = {}
        for stop, row in rows.items():
            if row.get("location_type", "") not in ("", "0", "1"):
                continue
            parent = row.get("parent_station") or stop
            station = rows.get(parent, row)
            lat = station["stop_lat"] or row["stop_lat"]
            lon = station["stop_lon"] or row["stop_lon"]
            if lat and lon:
                stations.setdefault(
                    parent,
                    Station(parent, station["stop_name"], float(lat), float(lon)),
                )
                platform_to_station[stop] = parent
        trips = _read_trips(archive, stations, platform_to_station)
        services = {trip.service for trip in trips}
        calendar = {
            row["service_id"]: Calendar(
                _date(row["start_date"]),
                _date(row["end_date"]),
                tuple(row[weekday] == "1" for weekday in WEEKDAYS),
            )
            for row in _rows(archive, "calendar", optional=True)
            if row["service_id"] in services
        }
        exceptions = {
            (row["service_id"], _date(row["date"])): row["exception_type"] == "1"
            for row in _rows(archive, "calendar_dates", optional=True)
            if row["service_id"] in services
        }
        days = [day for (_, day), added in exceptions.items() if added]
        starts = [entry.start for entry in calendar.values()] + days
        ends = [entry.end for entry in calendar.values()] + days
        info = next(_rows(archive, "feed_info", optional=True), {})
        first_day = (
            max(min(starts), _date(info["feed_start_date"]))
            if info.get("feed_start_date")
            else min(starts)
        )
        last_day = (
            min(max(ends), _date(info["feed_end_date"]))
            if info.get("feed_end_date")
            else max(ends)
        )
        if first_day > last_day:
            raise ValueError("Invalid Topo calendar")
        transfers = {}
        for row in _rows(archive, "transfers", optional=True):
            stop = row["from_stop_id"]
            qualifiers = ("from_route_id", "to_route_id", "from_trip_id", "to_trip_id")
            if (
                stop not in stations
                or stop != row["to_stop_id"]
                or any(row.get(key) for key in qualifiers)
            ):
                continue
            if row["transfer_type"] == "3":
                transfers[stop] = None
            elif row["transfer_type"] == "2":
                transfers[stop] = int(row["min_transfer_time"])
        shapes = defaultdict(list)
        shape_ids = {trip.shape for trip in trips}
        for row in _rows(archive, "shapes", optional=True):
            if row["shape_id"] in shape_ids and row.get("shape_dist_traveled"):
                shapes[row["shape_id"]].append(
                    ShapePoint(
                        float(row["shape_dist_traveled"]),
                        float(row["shape_pt_lat"]),
                        float(row["shape_pt_lon"]),
                    )
                )
        for points in shapes.values():
            points.sort(key=lambda point: point.distance)
    return Schedule(
        stations,
        trips,
        calendar,
        exceptions,
        transfers,
        dict(shapes),
        first_day,
        last_day,
        max(call.departure for trip in trips for call in trip.calls),
    )


def local_now() -> datetime:
    """Current time at the stations, independent of the HA timezone."""
    return datetime.now(UTC).astimezone(TZ)
