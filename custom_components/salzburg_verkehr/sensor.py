"""The first two usable departures and the number of displayed departures."""

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
        "delay": departure.delay,
        "delay_time": departure.delay_minutes,
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
    async_add_entities(
        [
            NextDeparture(entry.runtime_data, entry),
            AfterNextDeparture(entry.runtime_data, entry),
            DeparturesCount(entry.runtime_data, entry),
        ]
    )


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


class DepartureSensor(BoardSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus-clock"

    def __init__(self, coordinator, entry, key: str, position: int):
        super().__init__(coordinator, entry, key)
        self._position = position

    @property
    def _departure(self) -> Departure | None:
        board = self.coordinator.data
        if board is None or len(board.next_departures) <= self._position:
            return None
        return board.next_departures[self._position]

    @property
    def native_value(self):
        departure = self._departure
        return departure.time if departure else None

    @property
    def extra_state_attributes(self):
        board = self.coordinator.data
        if board is None:
            return {"departures": []}
        selected = self._departure
        return {
            **(departure_attributes(selected) if selected else {}),
            "departures": [departure_attributes(d) for d in board.departures],
            "last_updated": board.updated_at,
            "source": "Salzburg Verkehr",
            "source_board_truncated": board.truncated,
        }


class NextDeparture(DepartureSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "next_departure", 0)


class AfterNextDeparture(DepartureSensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "after_next_departure", 1)


class DeparturesCount(BoardSensor):
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "departures"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "departures_count")

    @property
    def native_value(self):
        return sum(not d.cancelled for d in self.coordinator.data.departures) if self.coordinator.data else 0
