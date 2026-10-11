"""Salzburg Verkehr departure boards for Home Assistant."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SalzburgClient, Station
from .coordinator import SalzburgCoordinator

type SalzburgEntry = ConfigEntry[SalzburgCoordinator]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SalzburgEntry) -> bool:
    """Start polling and let HA retry automatically if the service is offline."""
    client = SalzburgClient(async_get_clientsession(hass))
    station = Station(entry.data["station_id"], entry.data["station_name"], entry.data["station_lid"])
    coordinator = SalzburgCoordinator(hass, entry, client, station)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: SalzburgEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SalzburgEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
