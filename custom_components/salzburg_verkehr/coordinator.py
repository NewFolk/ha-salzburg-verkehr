"""Refresh operator data and apply this board's saved filters."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Board, SalzburgClient, Station, VerkehrError, filter_board
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
)


class SalzburgCoordinator(DataUpdateCoordinator[Board]):
    """Retain no live state when a refresh fails; HA marks entities unavailable."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: SalzburgClient, station: Station):
        super().__init__(
            hass,
            logging.getLogger(__name__),
            config_entry=entry,
            name=f"Salzburg Verkehr: {entry.title}",
            update_interval=timedelta(seconds=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)),
        )
        self.client, self.station = client, station

    async def _async_update_data(self) -> Board:
        try:
            board = await self.client.get_board(self.station)
        except VerkehrError as error:
            raise UpdateFailed(str(error)) from error
        options = self.config_entry.options
        return filter_board(
            board,
            lines=options.get(CONF_LINES, ""),
            directions=options.get(CONF_DIRECTIONS, ""),
            via=options.get(CONF_VIA, ""),
            categories=options.get(CONF_CATEGORIES, []),
            boarding_stop=options.get(CONF_BOARDING_STOP, ""),
            limit=options.get(CONF_LIMIT, DEFAULT_LIMIT),
        )
