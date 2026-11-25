"""Fetch and cache public Euskotren timetable data."""

import asyncio
import logging
from base64 import b64decode, b64encode
from time import monotonic
from zipfile import BadZipFile

from aiohttp import ClientError, ClientTimeout
from google.protobuf.message import DecodeError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, REALTIME_URL, SCHEDULE_URL
from .realtime import read_realtime
from .schedule import Realtime, Schedule, local_now, read_schedule

_LOGGER = logging.getLogger(__name__)
_SCHEDULE_INTERVAL = 6 * 3600
_RETRY_INTERVAL = 300
_REALTIME_INTERVAL = 60
_REALTIME_MAX_AGE = 300
_SCHEDULE_ERRORS = (
    ClientError,
    TimeoutError,
    BadZipFile,
    ValueError,
    KeyError,
    OSError,
)


class TimetableUnavailable(Exception):
    """No valid timetable is available."""


class EuskotrenClient:
    """Share one timetable and one realtime feed across concurrent requests."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self.store: Store[dict[str, str]] = Store(hass, 1, f"{DOMAIN}.schedule")
        self.schedule: Schedule | None = None
        self.realtime: Realtime | None = None
        self.next_schedule = 0.0
        self.next_realtime = 0.0
        self.schedule_lock = asyncio.Lock()
        self.realtime_lock = asyncio.Lock()

    async def async_schedule(self) -> Schedule:
        """Use the last valid timetable during a temporary source outage."""
        async with self.schedule_lock:
            if monotonic() >= self.next_schedule:
                try:
                    payload = await self._download(SCHEDULE_URL, 12 * 1024 * 1024)
                    self.schedule = await self.hass.async_add_executor_job(
                        read_schedule, payload
                    )
                except _SCHEDULE_ERRORS as err:
                    self.next_schedule = monotonic() + _RETRY_INTERVAL
                    _LOGGER.debug("Could not refresh Euskotren timetable: %s", err)
                    if self.schedule is None:
                        await self._restore()
                else:
                    self.next_schedule = monotonic() + _SCHEDULE_INTERVAL
                    encoded = await self.hass.async_add_executor_job(b64encode, payload)
                    await self.store.async_save({"zip": encoded.decode("ascii")})
            if self.schedule is None:
                raise TimetableUnavailable
            return self.schedule

    async def _restore(self) -> None:
        saved = await self.store.async_load()
        if not saved:
            return
        try:
            self.schedule = await self.hass.async_add_executor_job(
                _decode_schedule, saved["zip"]
            )
        except _SCHEDULE_ERRORS as err:
            _LOGGER.debug("Could not restore cached Euskotren timetable: %s", err)

    async def async_realtime(self) -> Realtime | None:
        """Return a fresh feed if the provider has published one."""
        async with self.realtime_lock:
            if monotonic() >= self.next_realtime:
                try:
                    payload = await self._download(REALTIME_URL, 8 * 1024 * 1024)
                    self.realtime = await self.hass.async_add_executor_job(
                        read_realtime, payload
                    )
                except (
                    ClientError,
                    TimeoutError,
                    ValueError,
                    DecodeError,
                    OSError,
                ) as err:
                    _LOGGER.debug("Could not refresh Euskotren predictions: %s", err)
                self.next_realtime = monotonic() + _REALTIME_INTERVAL
            if (
                self.realtime
                and -60
                <= local_now().timestamp() - self.realtime.timestamp
                <= _REALTIME_MAX_AGE
            ):
                return self.realtime
            return None

    async def _download(self, url: str, max_bytes: int) -> bytes:
        async with self.session.get(url, timeout=ClientTimeout(total=20)) as response:
            response.raise_for_status()
            payload = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise ValueError("Euskotren feed exceeds maximum size")
        return bytes(payload)


def _decode_schedule(encoded: str) -> Schedule:
    return read_schedule(b64decode(encoded, validate=True))
