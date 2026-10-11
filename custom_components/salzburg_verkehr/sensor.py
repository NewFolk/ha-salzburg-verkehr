"""The next usable departure and the number of usable departures."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SalzburgEntry
from .api import Departure
from .const import DOMAIN
from .coordinator import SalzburgCoordinator


def departure_attributes(departure: Departure) -> dict:
    """Keep lack of realtime explicit, including in JSON/card attributes."""
    return {
        "time": departure.time,
        "scheduled_time": departure.scheduled_time,
        "realtime_time": departure.realtime_time,
        "realtime_available": departure.realtime_time is not None,
        "delay_minutes": departure.delay_minutes,
        "line": departure.line,
        "product": departure.product,
        "category": departure.category,
        "direction": departure.direction,
        "boarding_stop": departure.boarding_stop,
        "stop_id": departure.stop_id,
        "cancelled": departure.cancelled,
        "intermediate_stops": list(departure.intermediate_stops),
    }


async def async_setup_entry(hass: HomeAssistant, entry: SalzburgEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([NextDeparture(entry.runtime_data, entry), DeparturesCount(entry.runtime_data, entry)])


class BoardSensor(CoordinatorEntity[SalzburgCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SalzburgCoordinator, entry: SalzburgEntry, key: str):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Salzburg Verkehr",
            model="Departure board",
        )


class NextDeparture(BoardSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus-clock"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "next_departure")

    @property
    def native_value(self):
        if self.coordinator.data:
            return next((d.time for d in self.coordinator.data.departures if not d.cancelled), None)
        return None

    @property
    def extra_state_attributes(self):
        board = self.coordinator.data
        if board is None:
            return {"departures": []}
        first = next((d for d in board.departures if not d.cancelled), None)
        return {
            **(departure_attributes(first) if first else {}),
            "departures": [departure_attributes(d) for d in board.departures],
            "last_updated": board.updated_at,
            "source": "Salzburg Verkehr",
            "source_board_truncated": board.truncated,
        }


class DeparturesCount(BoardSensor):
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "departures"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "departures_count")

    @property
    def native_value(self):
        return sum(not d.cancelled for d in self.coordinator.data.departures) if self.coordinator.data else 0
