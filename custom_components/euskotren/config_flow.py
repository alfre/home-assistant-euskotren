"""Single-entry setup for Euskotren."""

from typing import Any

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, NAME


class EuskotrenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the public timetable without credentials."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._async_abort_entries_match()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user")
