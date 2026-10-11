# Salzburg Verkehr for Home Assistant

<p><img src="assets/salzburg-verkehr.svg" alt="Salzburg Verkehr" width="96" height="96"></p>

Departure boards powered exclusively by **Salzburg Verkehr**, with UI setup and no personal API key. Written as an independent integration, inspired by the functionality of [agentcny/ha-oebb](https://github.com/agentcny/ha-oebb).

## Features

- Search for a stop by name and select a result.
- Choose a boarding stop/platform within a transport hub, or show the whole hub.
- Filter by exact line number, destination, later intermediate stop, and transport type.
- Configure multiple independent boards, including different directions at the same stop.
- Change filters, update interval and board length after setup.
- Three sensors per board: **Next departure**, **After next departure**, and **Departures count**.
- Scheduled and realtime times remain separate; missing realtime means unknown delay.
- Predictions before the scheduled time are retained, including negative delay values.
- Departures are sorted by their predicted time when available; past departures are excluded.
- Cancelled departures remain visible in board attributes but are excluded from the next usable departure and count.
- HAFAS day offsets such as `01000600` (next-day 00:06) and extended hours such as `24:03` are handled correctly.
- English, German and Russian UI translations.

## Installation

Requires **Home Assistant 2026.1.0 or newer**. Tested with Home Assistant 2026.1.0 on Python 3.13 and Home Assistant 2026.10.0 on Python 3.14.

The supplied integration icon/logo appears automatically on Home Assistant **2026.3 and newer**, which supports local `brand/` images. Earlier versions retain their generic integration icon until the domain is added to the shared Home Assistant Brands catalog. Sensor functionality remains available from 2026.1.0. The source logo and attribution are in [assets](assets/README.md).

### HACS

1. Open HACS → ⋮ → **Custom repositories**.
2. Add `https://github.com/NewFolk/ha-salzburg-verkehr` with category **Integration**.
3. Download **Salzburg Verkehr**, then restart Home Assistant.

### Manual installation

Copy `custom_components/salzburg_verkehr/` into your Home Assistant `config/custom_components/` directory, then restart Home Assistant. Release archives contain this directory structure.

## Setup

Open **Settings → Devices & services → Add integration → Salzburg Verkehr**.

Search for a stop, select it, then configure the board. Examples:

| Setting | Example | Behaviour |
|---|---|---|
| Boarding stop / platform | Salzburg Ferdinand-Hanusch-Platz (Griesgasse) | Include only departures boarding at that named stop |
| Lines | `7` or `7, 17` | Exact line/product match; `7` does not include `17` |
| Directions | `Salzachsee` | Case-insensitive part of a destination name |
| Intermediate stops | `Hauptbahnhof` | Match a later stop in the journey, not an already passed stop |
| Transport types | Obus, Stadtbus | Choose from categories returned by the operator; empty means all |
| Update interval | `90` | Seconds, between 30 and 3600 |
| Maximum departures | `20` | Matching rows to expose, between 1 and 100 |

Comma-separated alternatives within a text filter are combined with OR. Different filter fields are combined with AND. Clear a filter to remove it. Add the integration again for another board; an identical station/filter combination is rejected as a duplicate.

The operator can return an entire hub even when a specific platform is requested. This integration filters by the **actual boarding stop in each departure**, so selecting Griesgasse does not silently include Fischmarkt. Stops sharing the same name are grouped together. Platform and transport choices are populated from the current board, so a platform with no departures in that response may not appear initially.

## Sensors and cards

Both departure sensors are timestamps, using predictions when available and schedules otherwise. `next_departure` selects the first usable matching departure; `after_next_departure` selects the second. They use the same filters and skip cancelled departures. The first two usable matches are selected before the display limit, so the second sensor still works when Maximum departures is set to 1.

Each sensor's top-level attributes describe its own selected departure. The `departures` attribute contains the configured display list:

```yaml
line: "7"
product: Obus 7
direction: Salzachsee
scheduled_time: "2026-10-11T12:05:00+02:00"
realtime_time: "2026-10-11T12:06:00+02:00"
realtime_available: true
delay_minutes: 1
delay: true
delay_time: 1
cancelled: false
boarding_stop: Salzburg Salzachstraße
intermediate_stops:
  - Salzburg Salzachsee
departures: []  # Each row has the same departure fields above.
```

This is an illustrative example, not live data. `delay_time` is `(realtime_time - scheduled_time)` in minutes; negative values mean an earlier prediction. `delay` is `true` only for a known positive delay. Without realtime, `delay` is `false`, while `delay_time`, `delay_minutes` and `realtime_time` are `null`, and `realtime_available` is `false`. A false `delay` with no realtime must not be displayed as “on time”. Cancelled rows have no applicable delay. The existing `delay_minutes` attribute is retained for compatibility and has the same value as `delay_time`.

`last_updated` is the last successful fetch time. All departure timestamps carry the Europe/Vienna timezone, including daylight-saving changes. If there is only one usable matching departure, `after_next_departure` is `unknown` and has no selected-departure fields.

Select the actual entity IDs created in your installation when adding a dashboard card. A built-in Entities card works without another custom card:

```yaml
type: entities
title: Salzburg departures
entities:
  - sensor.your_board_next_departure
  - sensor.your_board_after_next_departure
  - sensor.your_board_departures_count
```

If there are no usable departures, the next departure is `unknown` and the count is zero. If fetching fails, both sensors become `unavailable`; Home Assistant retries automatically. This distinguishes an empty board from a network outage. The count excludes cancellations and covers only the configured list, not the entire day's timetable.

## Troubleshooting

If versions up to 0.1.2 report `Unexpected departure data from Salzburg Verkehr` when the board reaches departures after midnight, update to **0.1.3 or newer**. The API can encode next-day departures with an eight-digit day offset, for example `01000600`. Version 0.1.3 fixes parsing this valid format; it does not require changing filters, credentials or polling intervals.

## Data source and limits

This is an **unofficial** integration using the same web interface as Salzburg Verkehr's journey planner. It is not affiliated with Salzburg Verkehr. No ÖBB API is used. The public webapp identifier is loaded from the operator's public settings and cached for a day; it is not embedded in the repository or saved to configuration entries. A rejected identifier triggers one settings refresh.

Each refresh requests the first 100 departures from the operator before applying local filters. A busy hub or restrictive filter can therefore show fewer than your requested maximum. `source_board_truncated: true` indicates that the operator response reached that request limit. A server may return slightly more than 100 rows at a time boundary. The validated website product filter is used unchanged; the integration does not promise every transport mode or service in Austria.

The default interval is 90 seconds. There is no documented service availability guarantee or rate limit for this web interface. A server-side change may require an integration update. Forecasts are predictions, not confirmation that a vehicle actually departed at that time.

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
```

The default development requirements use Home Assistant 2026.10.0. To test the minimum supported release, create a Python 3.13 environment and install `requirements-test-2026.1.txt` instead. Set `LITELLM_LOCAL_MODEL_COST_MAP=True` for that environment to keep an unrelated cloud-library import offline.

The test suite runs against real Home Assistant, with network access disabled and synthetic fixtures. It covers setup, sensors, configuration/options flows, retries and recovery, API errors, filtering, unknown forecasts, delays, cancellations and midnight rollover. GitHub Actions tests both Home Assistant versions and additionally runs hassfest and HACS validation. Manual live checks were performed against Salzburg Verkehr for Salzachstraße, Ferdinand-Hanusch-Platz and Mirabellplatz; these are not part of the automated suite.

## Credits and license

Functional inspiration: [ÖBB Departures by agentcny](https://github.com/agentcny/ha-oebb). This project has its own API client and Home Assistant implementation, targeting Salzburg Verkehr exclusively. MIT license; see [LICENSE](LICENSE).
