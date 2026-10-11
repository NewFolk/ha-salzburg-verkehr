"""Small synthetic HAFAS responses, containing no captured user data."""

from datetime import UTC, datetime

from custom_components.salzburg_verkehr.api import Station, parse_board

STATION = Station("455082302", "Salzburg Salzachstraße", "A=1@O=Salzburg Salzachstraße@L=455082302@")
NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)


def response():
    return {
        "common": {
            "locL": [
                {"name": STATION.name, "extId": STATION.id},
                {"name": "Salzburg Süd", "extId": "destination"},
                {"name": "Salzburg Other platform", "extId": "another"},
            ],
            "prodL": [
                {"name": "Obus 7", "nameS": "7", "prodCtx": {"line": "7", "catOutL": "Obus"}},
                {"name": "Stadtbus 17", "nameS": "17", "prodCtx": {"line": "17", "catOutL": "Stadtbus"}},
            ],
        },
        "jnyL": [
            {
                "date": "20261011",
                "dirTxt": "Salzburg Süd",
                "prodX": 0,
                "stbStop": {"locX": 0, "idx": 3, "dTimeS": "120000", "dTimeR": "120300"},
                "stopL": [{"locX": 0, "idx": 3}, {"locX": 1, "idx": 4}],
            },
            {
                "date": "20261011",
                "dirTxt": "Ursulinen",
                "prodX": 1,
                "stbStop": {"locX": 2, "idx": 3, "dTimeS": "120100"},
                "stopL": [],
            },
        ],
    }


def board():
    return parse_board(response(), NOW)
