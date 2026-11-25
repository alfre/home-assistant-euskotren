"""Read Euskotren GTFS Realtime trip predictions."""

from datetime import date, datetime

from google.transit import gtfs_realtime_pb2 as gtfs

from .schedule import TZ, Realtime, StopUpdate, TripUpdate


def read_realtime(payload: bytes) -> Realtime:
    """Index scheduled predictions and cancellations by trip and service date."""
    feed = gtfs.FeedMessage.FromString(payload)
    timestamp = feed.header.timestamp
    if not timestamp or feed.header.incrementality != gtfs.FeedHeader.FULL_DATASET:
        raise ValueError("Expected a timestamped full GTFS Realtime feed")
    fallback_date = datetime.fromtimestamp(timestamp, TZ).date()
    trips = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        trip = update.trip
        cancelled = trip.schedule_relationship in (
            gtfs.TripDescriptor.CANCELED,
            gtfs.TripDescriptor.DELETED,
        )
        if not trip.trip_id or (
            not cancelled
            and trip.schedule_relationship != gtfs.TripDescriptor.SCHEDULED
        ):
            continue
        try:
            day = (
                date.fromisoformat(trip.start_date)
                if trip.start_date
                else fallback_date
            )
        except ValueError:
            continue
        stops = tuple(
            StopUpdate(
                stop.stop_sequence if stop.HasField("stop_sequence") else None,
                stop.stop_id,
                stop.schedule_relationship,
                _event(stop.arrival),
                _event(stop.departure),
            )
            for stop in update.stop_time_update
        )
        trips[trip.trip_id, day] = TripUpdate(
            cancelled, update.delay if update.HasField("delay") else None, stops
        )
    return Realtime(timestamp, trips)


def _event(event: gtfs.TripUpdate.StopTimeEvent) -> tuple[int | None, int | None]:
    return (
        event.time if event.HasField("time") else None,
        event.delay if event.HasField("delay") else None,
    )
