"""Single-entry setup for Euskotren."""

from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import TimetableUnavailable
from .const import CONF_DEFAULT_ORIGIN, DOMAIN, NAME

_NO_DEFAULT = "none"


class EuskotrenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the public timetable without credentials."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._async_abort_entries_match()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Open the station preference form."""
        return EuskotrenOptionsFlow()


class EuskotrenOptionsFlow(OptionsFlow):
    """Configure the default origin without reloading the integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = dict(self.config_entry.options)
            origin = user_input[CONF_DEFAULT_ORIGIN]
            if origin == _NO_DEFAULT:
                options.pop(CONF_DEFAULT_ORIGIN, None)
            else:
                options[CONF_DEFAULT_ORIGIN] = origin
            return self.async_create_entry(data=options)

        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="unavailable")
        try:
            schedule = await self.config_entry.runtime_data.async_schedule()
        except TimetableUnavailable:
            return self.async_abort(reason="unavailable")
        current = self.config_entry.options.get(CONF_DEFAULT_ORIGIN)
        if not isinstance(current, str) or current not in schedule.stations:
            current = _NO_DEFAULT
        choices = [SelectOptionDict(value=_NO_DEFAULT, label="No default origin")]
        choices.extend(
            SelectOptionDict(value=station.id, label=station.name)
            for station in sorted(
                schedule.stations.values(), key=lambda station: station.name.casefold()
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_DEFAULT_ORIGIN,
                        default=current,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=choices,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_DEFAULT_ORIGIN,
                        )
                    )
                }
            ),
        )
