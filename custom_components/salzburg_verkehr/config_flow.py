"""Search a stop, choose a platform and set up an independent departure board."""

from __future__ import annotations

import hashlib
import json

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .api import SalzburgClient, Station, VerkehrError, tokens
from .const import (
    CONF_BOARDING_STOP,
    CONF_CATEGORIES,
    CONF_DIRECTIONS,
    CONF_INTERVAL,
    CONF_LIMIT,
    CONF_LINES,
    CONF_VIA,
    DEFAULT_INTERVAL,
    DEFAULT_LIMIT,
    DOMAIN,
    MIN_INTERVAL,
)


def settings(values: dict) -> dict:
    """An omitted optional text field means clear it, not restore its old value."""
    return {
        CONF_LINES: values.get(CONF_LINES, "").strip(),
        CONF_DIRECTIONS: values.get(CONF_DIRECTIONS, "").strip(),
        CONF_VIA: values.get(CONF_VIA, "").strip(),
        CONF_BOARDING_STOP: values.get(CONF_BOARDING_STOP, ""),
        CONF_CATEGORIES: values.get(CONF_CATEGORIES, []),
        CONF_INTERVAL: values.get(CONF_INTERVAL, DEFAULT_INTERVAL),
        CONF_LIMIT: values.get(CONF_LIMIT, DEFAULT_LIMIT),
    }


def board_id(station_id: str, options: dict) -> str:
    identity = [
        station_id,
        options.get(CONF_BOARDING_STOP, ""),
        *[tokens(options.get(key, "")) for key in (CONF_LINES, CONF_DIRECTIONS, CONF_VIA)],
        sorted(category.casefold() for category in options.get(CONF_CATEGORIES, [])),
    ]
    digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
    return f"{station_id}_{digest}"


def title(station_name: str, options: dict) -> str:
    name = options.get(CONF_BOARDING_STOP) or station_name
    detail = " → ".join(options[key] for key in (CONF_LINES, CONF_DIRECTIONS) if options.get(key))
    return f"{name} ({detail})" if detail else name


def schema(defaults: dict, stop_names: list | tuple, categories: list | tuple) -> vol.Schema:
    fields = {
        vol.Optional(CONF_BOARDING_STOP, default=defaults.get(CONF_BOARDING_STOP, "")): SelectSelector(
            SelectSelectorConfig(
                options=[
                    {"value": "", "label": "All platforms"},
                    *[{"value": name, "label": name} for name in stop_names],
                ]
            )
        ),
    }
    for key in (CONF_LINES, CONF_DIRECTIONS, CONF_VIA):
        fields[vol.Optional(key, description={"suggested_value": defaults.get(key, "")})] = str
    fields[vol.Optional(CONF_CATEGORIES, default=defaults.get(CONF_CATEGORIES, []))] = SelectSelector(
        SelectSelectorConfig(options=sorted(set(categories) | set(defaults.get(CONF_CATEGORIES, []))), multiple=True)
    )
    fields[vol.Required(CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL))] = vol.All(
        vol.Coerce(int), vol.Range(min=MIN_INTERVAL, max=3600)
    )
    fields[vol.Required(CONF_LIMIT, default=defaults.get(CONF_LIMIT, DEFAULT_LIMIT))] = vol.All(
        vol.Coerce(int), vol.Range(min=1, max=100)
    )
    return vol.Schema(fields)


class SalzburgFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._stations: dict[str, Station] = {}
        self._station: Station | None = None
        self._board = None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                stations = await SalzburgClient(async_get_clientsession(self.hass)).search_stations(user_input["query"])
            except VerkehrError:
                errors["base"] = "cannot_connect"
            else:
                self._stations = {s.id: s for s in stations}
                if self._stations:
                    return await self.async_step_station()
                errors["base"] = "no_stations"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required("query"): str}), errors=errors)

    async def async_step_station(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._station = self._stations[user_input["station_id"]]
            try:
                self._board = await SalzburgClient(async_get_clientsession(self.hass)).get_board(self._station)
            except VerkehrError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_settings()
        return self.async_show_form(
            step_id="station",
            data_schema=vol.Schema(
                {vol.Required("station_id"): vol.In({key: station.name for key, station in self._stations.items()})}
            ),
            errors=errors,
        )

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            options = settings(user_input)
            await self.async_set_unique_id(board_id(self._station.id, options))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=title(self._station.name, options),
                data={
                    "station_id": self._station.id,
                    "station_name": self._station.name,
                    "station_lid": self._station.lid,
                    "stop_names": list(self._board.stop_names),
                    "categories": list(self._board.categories),
                },
                options=options,
            )
        return self.async_show_form(
            step_id="settings", data_schema=schema({}, self._board.stop_names, self._board.categories)
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SalzburgOptions()


class SalzburgOptions(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            options = settings(user_input)
            unique_id = board_id(self.config_entry.data["station_id"], options)
            if any(
                entry.entry_id != self.config_entry.entry_id and entry.unique_id == unique_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            ):
                return self.async_abort(reason="already_configured")
            self.hass.config_entries.async_update_entry(
                self.config_entry, unique_id=unique_id, title=title(self.config_entry.data["station_name"], options)
            )
            return self.async_create_entry(title="", data=options)
        stops = self.config_entry.data.get("stop_names", [])
        categories = self.config_entry.data.get("categories", [])
        if hasattr(self.config_entry, "runtime_data") and self.config_entry.runtime_data.data:
            stops = self.config_entry.runtime_data.data.stop_names
            categories = self.config_entry.runtime_data.data.categories
        if self.config_entry.options.get(CONF_BOARDING_STOP):
            stops = sorted(set(stops) | {self.config_entry.options[CONF_BOARDING_STOP]})
        return self.async_show_form(
            step_id="init", data_schema=schema(dict(self.config_entry.options), stops, categories)
        )
