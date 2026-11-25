"""Home Assistant integration for Euskotren's Topo."""

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .client import EuskotrenClient, TimetableUnavailable
from .const import DOMAIN, NAME, STATIC_URL, VERSION
from .services import async_setup_services

type EuskotrenConfigEntry = ConfigEntry[EuskotrenClient]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions and the bundled panel assets."""
    async_setup_services(hass)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL, str(Path(__file__).parent / "frontend"), False
            ),
        ]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EuskotrenConfigEntry) -> bool:
    """Load the timetable before exposing the planner."""
    client = EuskotrenClient(hass)
    try:
        await client.async_schedule()
    except TimetableUnavailable as err:
        raise ConfigEntryNotReady("Could not download the Euskotren timetable") from err
    entry.runtime_data = client
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="euskotren-planner",
        frontend_url_path=DOMAIN,
        sidebar_title=NAME,
        sidebar_icon="mdi:train",
        module_url=f"{STATIC_URL}/planner.js?v={VERSION}",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EuskotrenConfigEntry) -> bool:
    """Remove the planner when the configuration entry is unloaded."""
    frontend.async_remove_panel(hass, DOMAIN)
    return True
