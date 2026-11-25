"""Read and query the Euskotren GTFS timetable."""

import csv
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO, TextIOWrapper
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

    def catalog(self) -> dict:
        return {
            "first_date": self.first_day.isoformat(),
            "last_date": self.last_day.isoformat(),
            "timezone": TIME_ZONE,
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
        best_connection: dict[tuple[str, int, int], tuple[Leg, Leg]] = {}
        for first in first_legs:
            station = first.run.trip.calls[first.end].station
            transfer = self.transfers.get(station, 120)
            if station not in connections or transfer is None:
                continue
            departures = departure_index[station]
            left = bisect_left(departures, first.arrival + transfer)
            right = bisect_right(departures, first.arrival + MAX_TRANSFER)
            for second in connections[station][left:right]:
                if first.run is second.run or second.arrival < second.departure:
                    continue
                key = (first.run.trip.id, first.run.epoch, first.start)
                current = best_connection.get(key)
                if current is None or (second.arrival, second.departure) < (
                    current[1].arrival,
                    current[1].departure,
                ):
                    best_connection[key] = (first, second)

        # Connections are useful only if they beat every direct train leaving as late or later.
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
            elif legs[-1].arrival >= best_direct_arrival:
                continue
            journeys.append(legs)
        journeys.sort(key=lambda legs: (legs[0].departure, legs[-1].arrival, len(legs)))
        return {
            "date": day.isoformat(),
            "timezone": TIME_ZONE,
            "realtime_updated_at": _iso(live.timestamp) if live else None,
            "journeys": [self._journey(legs) for legs in journeys[:limit]],
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
        points = self.shapes.get(trip.shape, [])
        first_distance, last_distance = calls[0].distance, calls[-1].distance
        if first_distance is not None and last_distance is not None:
            left = bisect_right(
                points, first_distance, key=lambda point: point.distance
            )
            right = bisect_left(points, last_distance, key=lambda point: point.distance)
            points = points[left:right]
        else:
            points = []
        line = [[stop["latitude"], stop["longitude"]] for stop in stops]
        if points:
            line = [
                line[0],
                *[[point.latitude, point.longitude] for point in points],
                line[-1],
            ]
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
            "polyline": line,
        }


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
