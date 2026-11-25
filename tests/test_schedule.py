"""Regression coverage for GTFS calendar, transfers and predictions."""

import unittest
from datetime import date, datetime, time
from io import BytesIO
from zipfile import ZipFile

from custom_components.euskotren.realtime import read_realtime
from custom_components.euskotren.schedule import (
    TZ,
    Call,
    Realtime,
    ShapePoint,
    TripUpdate,
    _route_geometry,
    read_schedule,
)
from google.transit import gtfs_realtime_pb2 as gtfs


def example_schedule():
    """A direct train, two compatible trains and an overnight service."""
    data = BytesIO()
    tables = {
        "routes": "route_id,route_short_name,route_type\nr,T1,2\n",
        "trips": "route_id,service_id,trip_id\nr,normal,direct\nr,normal,first\nr,normal,second\nr,normal,night\n",
        "stops": (
            "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
            "a,A,43.30,-1.99,1,\n"
            "a1,A platform,43.30,-1.99,0,a\n"
            "b,B,43.32,-1.97,1,\n"
            "b1,B platform 1,43.32,-1.97,0,b\n"
            "b2,B platform 2,43.32,-1.97,0,b\n"
            "c,C,43.33,-1.93,0,\n"
        ),
        "stop_times": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "direct,10:12:00,10:12:00,a1,1\n"
            "direct,10:20:00,10:21:00,b1,2\n"
            "direct,10:34:00,10:34:00,c,3\n"
            "first,10:00:00,10:00:00,a1,1\n"
            "first,10:08:00,10:08:00,b1,2\n"
            "second,10:10:00,10:10:00,b2,1\n"
            "second,10:20:00,10:20:00,c,2\n"
            "night,24:15:00,24:15:00,a1,1\n"
            "night,24:39:00,24:39:00,c,2\n"
        ),
        "calendar": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "normal,1,1,1,1,1,0,0,20261123,20261204\n"
        ),
        "calendar_dates": "service_id,date,exception_type\nnormal,20261126,2\nnormal,20261128,1\n",
        "feed_info": "feed_start_date,feed_end_date\n20261123,20261130\n",
    }
    with ZipFile(data, "w") as archive:
        for name, contents in tables.items():
            archive.writestr(f"{name}.txt", contents)
    return read_schedule(data.getvalue())


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.schedule = example_schedule()

    def journeys(self, day=date(2026, 11, 25), after=time(9), live=None):
        return self.schedule.search("a", "c", day, after, 10, live)["journeys"]

    def test_station_catalog_contains_the_full_line_path(self):
        self.assertEqual(
            self.schedule.catalog()["line_paths"],
            {"T1": [(43.30, -1.99), (43.32, -1.97), (43.33, -1.93)]},
        )

    def test_same_station_transfer_between_platforms(self):
        first, direct = self.journeys()
        self.assertEqual(first["transfers"], 1)
        self.assertEqual(first["duration_minutes"], 20)
        self.assertEqual(
            first["legs"][0]["destination"]["id"], first["legs"][1]["origin"]["id"]
        )
        self.assertEqual(direct["transfers"], 0)
        self.assertEqual(
            direct["legs"][0]["polyline"],
            [[43.30, -1.99], [43.32, -1.97], [43.33, -1.93]],
        )

    def test_calendar_exceptions_coverage_and_after_midnight(self):
        self.assertEqual(self.journeys(date(2026, 11, 26)), [])
        self.assertTrue(self.journeys(date(2026, 11, 28)))
        overnight = self.journeys(date(2026, 11, 29), time())[0]
        self.assertEqual(overnight["departure"], "2026-11-29T00:15:00+01:00")
        self.assertEqual(overnight["arrival"], "2026-11-29T00:39:00+01:00")
        with self.assertRaisesRegex(ValueError, "date_out_of_range"):
            self.journeys(date(2026, 12, 1))

    def test_earlier_boarding_can_connect_to_the_later_direct_train(self):
        self.schedule.trips = tuple(
            trip for trip in self.schedule.trips if trip.id != "second"
        )
        connection, direct = self.journeys()
        self.assertEqual(connection["transfers"], 1)
        self.assertEqual(connection["legs"][0]["destination"]["name"], "B")
        self.assertEqual(
            connection["legs"][1]["departure"], "2026-11-25T10:21:00+01:00"
        )
        self.assertEqual(connection["arrival"], direct["arrival"])
        self.assertEqual(direct["transfers"], 0)

    def test_delays_invalidate_a_transfer_and_cancellations_are_date_specific(self):
        feed = gtfs.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.timestamp = 1795605601
        delayed = feed.entity.add(id="delayed").trip_update
        delayed.trip.trip_id = "first"
        delayed.trip.start_date = "20261125"
        delayed.stop_time_update.add(stop_sequence=2).arrival.delay = 180
        cancelled = feed.entity.add(id="cancelled").trip_update
        cancelled.trip.trip_id = "direct"
        cancelled.trip.start_date = "20261124"
        cancelled.trip.schedule_relationship = gtfs.TripDescriptor.CANCELED
        live = read_realtime(feed.SerializeToString())
        connection, direct = self.journeys(live=live)
        self.assertEqual(
            connection["legs"][1]["departure"], "2026-11-25T10:21:00+01:00"
        )
        self.assertEqual(connection["arrival"], direct["arrival"])
        self.assertEqual(
            [
                journey["transfers"]
                for journey in self.journeys(date(2026, 11, 24), live=live)
            ],
            [1],
        )

    def test_skipped_stop_cannot_be_used_as_destination(self):
        feed = gtfs.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.timestamp = 1795605601
        update = feed.entity.add(id="skipped").trip_update
        update.trip.trip_id = "direct"
        update.trip.start_date = "20261125"
        update.stop_time_update.add(
            stop_id="b1", schedule_relationship=gtfs.TripUpdate.StopTimeUpdate.SKIPPED
        )
        live = read_realtime(feed.SerializeToString())
        direct = self.journeys(live=live)[1]
        self.assertTrue(direct["legs"][0]["stops"][1]["skipped"])
        to_b = self.schedule.search("a", "b", date(2026, 11, 25), time(9), 10, live)
        self.assertEqual(len(to_b["journeys"]), 1)
        self.assertEqual(to_b["journeys"][0]["departure"], "2026-11-25T10:00:00+01:00")

    def test_map_keeps_the_continuous_alignment_in_both_directions(self):
        pasaia = (43.3171, -1.9177)
        galtzaraborda = (43.3124, -1.9069)
        tunnel = (43.3149, -1.9125)
        approach = (43.3125, -1.9080)
        jump = (43.3205, -1.9356)
        former_alignment = (43.3192, -1.9250)
        published = [pasaia, tunnel, approach, jump, former_alignment, galtzaraborda]
        for coordinates, stops in (
            (published, [pasaia, galtzaraborda]),
            (list(reversed(published)), [galtzaraborda, pasaia]),
        ):
            with self.subTest(origin=stops[0]):
                points = [
                    ShapePoint(i, *location) for i, location in enumerate(coordinates)
                ]
                first = Call("a", "a", 1, 0, 0, True, True, 0)
                last = Call("b", "b", 2, 0, 0, True, True, len(points) - 1)
                line = _route_geometry(points, (first, last), stops)
                self.assertEqual(line[0], stops[0])
                self.assertEqual(line[-1], stops[-1])
                self.assertIn(tunnel, line)
                self.assertNotIn(jump, line)
                self.assertNotIn(former_alignment, line)

    def test_train_positions_move_between_stops_and_wait_at_a_station(self):
        at = datetime(2026, 11, 25, 10, 20, 30, tzinfo=TZ)
        trains = self.schedule.train_positions(at)["trains"]
        self.assertEqual(len(trains), 1)
        self.assertEqual(trains[0]["at_station"], "B")
        self.assertEqual(trains[0]["next_station"], "C")
        self.assertEqual(
            trains[0]["arrival_timestamp"],
            int(at.replace(minute=34, second=0).timestamp()),
        )
        self.assertEqual(trains[0]["latitude"], 43.32)
        moving = self.schedule.train_positions(at.replace(minute=27))["trains"][0]
        self.assertEqual(moving["id"], trains[0]["id"])
        self.assertEqual(moving["next_station"], "C")
        self.assertEqual(moving["arrival_timestamp"], trains[0]["arrival_timestamp"])
        self.assertGreater(moving["longitude"], -1.97)
        self.assertLess(moving["longitude"], -1.93)
        self.assertEqual(moving["path"][0], (43.32, -1.97, 0.0))
        self.assertFalse(moving["realtime"])
        self.assertEqual(
            self.schedule.train_positions(at.replace(hour=9))["trains"], []
        )

    def test_train_positions_use_delays_and_exclude_cancelled_and_finished_trips(self):
        at = datetime(2026, 11, 25, 10, 35, tzinfo=TZ)
        self.assertEqual(self.schedule.train_positions(at)["trains"], [])
        key = ("direct", at.date())
        delay = Realtime(int(at.timestamp()), {key: TripUpdate(False, 300, ())})
        moving = self.schedule.train_positions(at, delay)["trains"][0]
        self.assertTrue(moving["realtime"])
        self.assertEqual(moving["next_station"], "C")
        self.assertEqual(
            moving["arrival_timestamp"], int(at.replace(minute=39).timestamp())
        )
        cancellation = Realtime(int(at.timestamp()), {key: TripUpdate(True, None, ())})
        self.assertEqual(
            self.schedule.train_positions(at.replace(minute=27), cancellation)[
                "trains"
            ],
            [],
        )

    def test_train_positions_include_an_overnight_trip_from_the_previous_service_day(
        self,
    ):
        at = datetime(2026, 11, 29, 0, 25, tzinfo=TZ)
        trains = self.schedule.train_positions(at)["trains"]
        self.assertEqual(len(trains), 1)
        self.assertTrue(trains[0]["id"].startswith("night|"))
        self.assertEqual(trains[0]["next_station"], "C")
        self.assertEqual(
            trains[0]["arrival_timestamp"], int(at.replace(minute=39).timestamp())
        )
