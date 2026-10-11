"""Load real Home Assistant entities, options flows and update failures."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.salzburg_verkehr.api import VerkehrError, parse_board
from custom_components.salzburg_verkehr.config_flow import board_id, settings
from custom_components.salzburg_verkehr.const import DOMAIN

from .helpers import NOW, STATION, board, response

CLIENT = "custom_components.salzburg_verkehr.api.SalzburgClient"


def entry(options=None, title=STATION.name):
    options = settings(options or {})
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            "station_id": STATION.id,
            "station_name": STATION.name,
            "station_lid": STATION.lid,
            "stop_names": [STATION.name],
            "categories": ["Obus", "Stadtbus"],
        },
        options=options,
        unique_id=board_id(STATION.id, options),
    )


async def test_setup_attributes_unload_and_options(hass):
    config = entry({"lines": "7"})
    config.add_to_hass(hass)
    with patch(CLIENT + ".get_board", AsyncMock(return_value=board())):
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        registered = er.async_entries_for_config_entry(er.async_get(hass), config.entry_id)
        assert len(registered) == 3
        next_state = next(
            hass.states.get(e.entity_id) for e in registered if e.unique_id == f"{config.entry_id}_next_departure"
        )
        assert next_state.attributes["line"] == "7"
        assert next_state.attributes["delay_minutes"] == 3
        assert next_state.attributes["delay_time"] == 3
        assert next_state.attributes["delay"] is True
        assert next_state.attributes["realtime_available"] is True
        assert len(next_state.attributes["departures"]) == 1
        flow = await hass.config_entries.options.async_init(config.entry_id)
        assert flow["step_id"] == "init"
        await hass.config_entries.options.async_configure(
            flow["flow_id"], user_input={"update_interval": 120, "max_departures": 10}
        )
        await hass.async_block_till_done()
        assert config.options["lines"] == ""
        assert config.runtime_data.update_interval.total_seconds() == 120
        assert await hass.config_entries.async_unload(config.entry_id)
        await hass.async_block_till_done()
        assert config.state is ConfigEntryState.NOT_LOADED


async def test_initial_failure_retries(hass):
    config = entry()
    config.add_to_hass(hass)
    with patch(CLIENT + ".get_board", AsyncMock(side_effect=VerkehrError("Cannot connect"))):
        assert not await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        assert config.state is ConfigEntryState.SETUP_RETRY


async def test_refresh_failure_and_recovery(hass):
    config = entry()
    config.add_to_hass(hass)
    with patch(CLIENT + ".get_board", AsyncMock(return_value=board())) as mock:
        await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        mock.side_effect = VerkehrError("Cannot connect")
        await config.runtime_data.async_refresh()
        await hass.async_block_till_done()
        registered = er.async_entries_for_config_entry(er.async_get(hass), config.entry_id)
        assert all(hass.states.get(e.entity_id).state == "unavailable" for e in registered)
        mock.side_effect = None
        await config.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert all(hass.states.get(e.entity_id).state != "unavailable" for e in registered)


async def test_cancelled_not_next_and_empty_board(hass):
    config = entry()
    config.add_to_hass(hass)
    raw = response()
    raw["jnyL"][1]["isCncl"] = True
    with patch(CLIENT + ".get_board", AsyncMock(return_value=parse_board(raw, NOW))) as mock:
        await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        registered = er.async_entries_for_config_entry(er.async_get(hass), config.entry_id)
        states = {e.unique_id.removeprefix(f"{config.entry_id}_"): hass.states.get(e.entity_id) for e in registered}
        assert states["next_departure"].attributes["line"] == "7"
        assert states["departures_count"].state == "1"
        assert states["after_next_departure"].state == "unknown"
        assert "delay" not in states["after_next_departure"].attributes
        assert any(d["cancelled"] for d in states["next_departure"].attributes["departures"])
        raw["jnyL"] = []
        mock.return_value = parse_board(raw, NOW)
        await config.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(states["next_departure"].entity_id).state == "unknown"
        assert hass.states.get(states["after_next_departure"].entity_id).state == "unknown"
        assert hass.states.get(states["departures_count"].entity_id).state == "0"


async def test_after_next_forecast_order_filters_and_limit(hass):
    config = entry(
        {"lines": "7", "directions": "Salzachsee", "max_departures": 1}, title="Salzburg Salzachstraße (7 → Salzachsee)"
    )
    config.add_to_hass(hass)
    raw = response()
    raw["jnyL"][0]["dirTxt"] = "Salzachsee"
    earlier = deepcopy(raw["jnyL"][0])
    earlier["stbStop"].update(dTimeS="120400", dTimeR="120200")
    cancelled = deepcopy(earlier)
    cancelled["stbStop"].update(dTimeS="120000", dCncl=True)
    raw["jnyL"].extend([earlier, cancelled])
    with patch(CLIENT + ".get_board", AsyncMock(return_value=parse_board(raw, NOW))) as mock:
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        registered = er.async_entries_for_config_entry(er.async_get(hass), config.entry_id)
        states = {e.unique_id.removeprefix(f"{config.entry_id}_"): hass.states.get(e.entity_id) for e in registered}
        first, second = states["next_departure"], states["after_next_departure"]
        assert second.entity_id == "sensor.salzburg_salzachstrasse_7_salzachsee_after_next_departure"
        assert first.entity_id == "sensor.salzburg_salzachstrasse_7_salzachsee_next_departure"
        assert first.attributes["realtime_time"].minute == 2
        assert first.attributes["delay"] is False
        assert first.attributes["delay_time"] == -2
        assert second.attributes["realtime_time"].minute == 3
        assert second.attributes["delay"] is True
        assert second.attributes["delay_time"] == 3
        assert len(second.attributes["departures"]) == 1
        # A subsequent refresh with no second match must clear its old attributes.
        raw["jnyL"] = [earlier]
        mock.return_value = parse_board(raw, NOW)
        await config.runtime_data.async_refresh()
        await hass.async_block_till_done()
        second = hass.states.get(second.entity_id)
        assert second.state == "unknown"
        assert "delay_time" not in second.attributes


async def test_config_flow_and_duplicate(hass):
    with (
        patch(CLIENT + ".search_stations", AsyncMock(return_value=[STATION])),
        patch(CLIENT + ".get_board", AsyncMock(return_value=board())),
    ):
        flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert flow["step_id"] == "user"
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={"query": "Salzach"})
        assert flow["step_id"] == "station"
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={"station_id": STATION.id})
        assert flow["step_id"] == "settings"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], user_input={"lines": "7", "update_interval": 90, "max_departures": 20}
        )
        assert flow["type"] == "create_entry"
        await hass.async_block_till_done()
        assert flow["result"].state is ConfigEntryState.LOADED
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data={"query": "Salzach"}
        )
        duplicate = await hass.config_entries.flow.async_configure(
            duplicate["flow_id"], user_input={"station_id": STATION.id}
        )
        duplicate = await hass.config_entries.flow.async_configure(
            duplicate["flow_id"], user_input={"lines": "7", "update_interval": 120, "max_departures": 10}
        )
        assert duplicate["type"] == "abort"
        assert duplicate["reason"] == "already_configured"


async def test_search_error_and_empty(hass):
    with patch(CLIENT + ".search_stations", AsyncMock(side_effect=VerkehrError("offline"))) as mock:
        flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={"query": "Salzach"})
        assert flow["errors"] == {"base": "cannot_connect"}
        mock.side_effect = None
        mock.return_value = []
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={"query": "unknown"})
        assert flow["errors"] == {"base": "no_stations"}


async def test_station_preview_failure(hass):
    with (
        patch(CLIENT + ".search_stations", AsyncMock(return_value=[STATION])),
        patch(CLIENT + ".get_board", AsyncMock(side_effect=VerkehrError("offline"))),
    ):
        flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={"query": "Salzach"})
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input={"station_id": STATION.id})
        assert flow["step_id"] == "station"
        assert flow["errors"] == {"base": "cannot_connect"}


async def test_options_do_not_duplicate_other_board(hass):
    first, second = entry({"lines": "7"}), entry({"lines": "17"})
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(second.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input={"lines": "7", "update_interval": 90, "max_departures": 20}
    )
    assert flow["type"] == "abort"
    assert flow["reason"] == "already_configured"
