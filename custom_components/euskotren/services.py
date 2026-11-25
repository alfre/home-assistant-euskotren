"""Euskotren actions for the panel, automations and scripts."""

from datetime import time, timedelta
from functools import partial

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .client import EuskotrenClient, TimetableUnavailable
from .const import DOMAIN
from .schedule import local_now

_SEARCH_SCHEMA = probatio.Schema(
    {
        probatio.Required("origin"): probatio.All(
            cv.string, probatio.Length(min=1, max=160)
        ),
        probatio.Required("destination"): probatio.All(
            cv.string, probatio.Length(min=1, max=160)
        ),
        probatio.Optional("date"): cv.date,
        probatio.Optional("time"): cv.time,
        probatio.Optional("limit", default=10): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1, max=30)
        ),
    }
)


def _client(hass: HomeAssistant) -> EuskotrenClient:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_loaded")


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Expose station lookup and journey planning as response-only actions."""

    async def list_stations(call: ServiceCall) -> dict:
        try:
            schedule = await _client(hass).async_schedule()
        except TimetableUnavailable as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="unavailable"
            ) from err
        return schedule.catalog()

    async def search_journeys(call: ServiceCall) -> dict:
        client = _client(hass)
        try:
            schedule = await client.async_schedule()
        except TimetableUnavailable as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="unavailable"
            ) from err
        now = local_now()
        day = call.data.get("date", now.date())
        after = call.data.get("time", now.time() if day == now.date() else time())
        if not schedule.first_day <= day <= schedule.last_day:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="date_out_of_range",
                translation_placeholders={
                    "first": schedule.first_day.isoformat(),
                    "last": schedule.last_day.isoformat(),
                },
            )
        live = (
            await client.async_realtime()
            if abs(day - now.date()) <= timedelta(days=1)
            else None
        )
        try:
            return await hass.async_add_executor_job(
                partial(
                    schedule.search,
                    call.data["origin"],
                    call.data["destination"],
                    day,
                    after,
                    call.data["limit"],
                    live,
                )
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=str(err)
            ) from err

    hass.services.async_register(
        DOMAIN,
        "list_stations",
        list_stations,
        schema=probatio.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "search_journeys",
        search_journeys,
        schema=_SEARCH_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
