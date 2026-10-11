"""Operator data, filters, error handling and public configuration caching."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.salzburg_verkehr.api import (
    BASE_URL,
    CONFIG_URL,
    SalzburgClient,
    VerkehrError,
    filter_board,
    parse_board,
    parse_time,
)

from .helpers import NOW, STATION, board, response


def test_order_and_unknown_forecast():
    rows = board().departures
    assert [d.line for d in rows] == ["17", "7"]
    assert rows[0].realtime_time is None
    assert rows[0].delay_minutes is None
    assert rows[1].delay_minutes == 3


def test_early_and_after_midnight():
    raw = response()
    raw["jnyL"][0]["stbStop"].update(dTimeS="235800", dTimeR="240300")
    departure = parse_board(raw, NOW).departures[-1]
    assert departure.realtime_time.day == 12
    assert departure.realtime_time.hour == 0
    assert departure.delay_minutes == 5
    raw["jnyL"][0]["stbStop"].update(dTimeS="120000", dTimeR="115900")
    assert parse_board(raw, NOW).departures[0].delay_minutes == -1


def test_delay_keeps_past_schedule_and_removes_past_forecast():
    raw = response()
    raw["jnyL"][0]["stbStop"].update(dTimeS="105900", dTimeR="110100")
    assert len(parse_board(raw, NOW).departures) == 2
    raw["jnyL"][0]["stbStop"]["dTimeR"] = "105900"
    assert len(parse_board(raw, NOW).departures) == 1


def test_cancellation_and_downstream_stops():
    raw = response()
    raw["jnyL"][0]["stbStop"]["dCncl"] = True
    first = parse_board(raw, NOW).departures[0]
    assert first.cancelled
    assert first.delay_minutes is None
    assert first.time == first.scheduled_time
    assert first.intermediate_stops == ("Salzburg Süd",)


@pytest.mark.parametrize("clock", ["126000", "120060", "no time", None])
def test_bad_clock(clock):
    with pytest.raises(ValueError):
        parse_time("20261011", clock)


@pytest.mark.parametrize("index", [-1, "0", 99])
def test_invalid_response_reference(index):
    raw = response()
    raw["jnyL"][0]["stbStop"]["locX"] = index
    with pytest.raises(VerkehrError):
        parse_board(raw, NOW)


def test_empty_board_and_truncation():
    raw = response()
    raw["jnyL"] = []
    assert parse_board(raw, NOW).departures == ()
    raw["jnyL"] = [response()["jnyL"][0]] * 100
    assert parse_board(raw, NOW).truncated


def test_filters_exact_lines_platform_category_and_via():
    sample = board()
    assert [d.line for d in filter_board(sample, lines="7").departures] == ["7"]
    assert [d.line for d in filter_board(sample, lines="Obus 7").departures] == ["7"]
    assert [d.line for d in filter_board(sample, categories=["STADTBUS"]).departures] == ["17"]
    assert [d.line for d in filter_board(sample, boarding_stop=STATION.name).departures] == ["7"]
    assert [d.line for d in filter_board(sample, directions="süd,another").departures] == ["7"]
    assert [d.line for d in filter_board(sample, via="Süd").departures] == ["7"]
    assert filter_board(sample, via=STATION.name).departures == ()
    assert filter_board(sample, lines="8").departures == ()
    assert len(filter_board(sample, limit=1).departures) == 1


async def test_http_client_and_cached_public_identifier(hass, aioclient_mock, freezer):
    freezer.move_to(NOW)
    aioclient_mock.get(CONFIG_URL, json={"urlMgate": "/hamm/gate", "hciAuth": {"aid": "synthetic-public-id"}})
    aioclient_mock.post(
        BASE_URL + "/hamm/gate",
        json={"err": "OK", "svcResL": [{"meth": "StationBoard", "err": "OK", "res": response()}]},
    )
    client = SalzburgClient(async_get_clientsession(hass))
    assert len((await client.get_board(STATION)).departures) == 2
    await client.get_board(STATION)
    assert aioclient_mock.call_count == 3
    payload = aioclient_mock.mock_calls[-1][2]
    assert payload["auth"]["aid"] == "synthetic-public-id"
    assert payload["svcReqL"][0]["req"]["type"] == "DEP"


async def test_search_station(hass):
    client = SalzburgClient(async_get_clientsession(hass))
    with patch.object(
        client,
        "_request",
        AsyncMock(
            return_value={
                "match": {"locL": [{"type": "S", "extId": STATION.id, "name": STATION.name, "lid": STATION.lid}]}
            }
        ),
    ):
        assert await client.search_stations("Salzach") == [STATION]


@pytest.mark.parametrize(
    "result", [{"match": None}, {"match": {"locL": "invalid"}}, {"match": {"locL": [{"type": "S"}]}}]
)
async def test_malformed_station_search(hass, result):
    client = SalzburgClient(async_get_clientsession(hass))
    with patch.object(client, "_request", AsyncMock(return_value=result)), pytest.raises(VerkehrError):
        await client.search_stations("Salzach")


@pytest.mark.parametrize("reply", [{}, {"urlMgate": "/other", "hciAuth": {"aid": "test"}}])
async def test_changed_settings(hass, reply):
    client = SalzburgClient(async_get_clientsession(hass))
    with patch.object(client, "_json", AsyncMock(return_value=reply)), pytest.raises(VerkehrError):
        await client.get_board(STATION)


@pytest.mark.parametrize("reply", [{}, {"svcResL": []}, {"svcResL": [{"meth": "StationBoard", "err": "FAIL"}]}])
async def test_service_error(hass, reply):
    client = SalzburgClient(async_get_clientsession(hass))
    with (
        patch.object(client, "_settings", AsyncMock(return_value={})),
        patch.object(client, "_json", AsyncMock(return_value=reply)),
        pytest.raises(VerkehrError),
    ):
        await client.get_board(STATION)


async def test_auth_refresh_once(hass, freezer):
    freezer.move_to(NOW)
    client = SalzburgClient(async_get_clientsession(hass))
    settings = {"urlMgate": "/hamm/gate", "hciAuth": {"aid": "test"}}
    with patch.object(
        client,
        "_json",
        AsyncMock(
            side_effect=[
                settings,
                {"err": "AUTH", "svcResL": [{}]},
                settings,
                {"err": "OK", "svcResL": [{"meth": "StationBoard", "err": "OK", "res": response()}]},
            ]
        ),
    ) as mock:
        assert len((await client.get_board(STATION)).departures) == 2
        assert mock.call_count == 4


@pytest.mark.parametrize("kind", ["http", "invalid_json", "network"])
async def test_safe_network_errors(hass, aioclient_mock, kind):
    client = SalzburgClient(async_get_clientsession(hass))
    if kind == "http":
        aioclient_mock.get(CONFIG_URL, status=503, text="private response body")
    elif kind == "invalid_json":
        aioclient_mock.get(CONFIG_URL, text="private response body")
    else:
        aioclient_mock.get(CONFIG_URL, exc=aiohttp.ClientConnectionError("private transport details"))
    with pytest.raises(VerkehrError) as error:
        await client.get_board(STATION)
    assert "private" not in str(error.value)
