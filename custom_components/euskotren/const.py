"""Constants for Euskotren."""

DOMAIN = "euskotren"
NAME = "Euskotren Topo"
VERSION = "0.3.1"
CONF_DEFAULT_ORIGIN = "default_origin"
TIME_ZONE = "Europe/Madrid"
FEED_BASE = "https://opendata.euskadi.eus/transport/moveuskadi/euskotren"
SCHEDULE_URL = f"{FEED_BASE}/gtfs_euskotren.zip"
REALTIME_URL = f"{FEED_BASE}/gtfsrt_euskotren_trip_updates.pb"
TOPO_LINES = frozenset({"T0", "T1"})
SHARED_LINE = "E1"
STATIC_URL = "/euskotren_static"
