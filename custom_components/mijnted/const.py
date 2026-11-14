"""Constants for the mijnTED integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mijnted"
MANUFACTURER: Final = "Techem"

CONF_UPDATE_INTERVAL: Final = "update_interval"
DEFAULT_UPDATE_INTERVAL_HOURS: Final = 6
MIN_UPDATE_INTERVAL_HOURS: Final = 1
MAX_UPDATE_INTERVAL_HOURS: Final = 24

# Days of history fetched and imported as statistics on every update.
HISTORY_DAYS: Final = 30
# Exponential back-off on transient errors, capped at this interval.
MAX_BACKOFF: Final = timedelta(hours=24)

HEATING_DELIVERY_TYPE: Final = 1
UNIT_UNITS: Final = "units"

ISSUE_UNEXPECTED_PAGE: Final = "unexpected_sign_in_page"
ISSUE_EXPORT_DISABLED: Final = "export_disabled"

# Room codes as the portal translates them (Dutch), from the portal's own i18n table.
ROOMS_NL: Final[dict[str, str]] = {
    "ALG": "Algemeen",
    "B": "Badkamer",
    "BA": "Badkamer",
    "BBE": "Badkamer beneden",
    "BBO": "Badkamer boven",
    "BDR": "Bedrijfsruimte",
    "BER": "Berging",
    "BIB": "Bibliotheek",
    "BUE": "Bureau",
    "D": "Douche",
    "EET": "Eetkamer",
    "EIN": "Ingang",
    "ENT": "Entree",
    "F": "Overloop",
    "G": "Garage",
    "GAN": "Hal",
    "GS": "Logeerkamer",
    "HA": "Hal",
    "HAL": "Hal",
    "HY": "Hobbykamer",
    "K": "Keuken",
    "KA": "Kamer / Slaapkamer",
    "KAN": "Kantoor",
    "KEL": "Kelder",
    "KEU": "Keuken",
    "KL": "Kelder",
    "ONB": "Onbekend",
    "S": "Slaapkamer",
    "SKA": "Kamer / Slaapkamer",
    "T": "Trappenhuis",
    "TV": "Tv-kamer",
    "W": "Woonkamer",
    "WC": "WC",
    "WE": "Atelier",
    "WK": "Waskeuken",
    "WKA": "Woonkamer",
    "WW": "Warm water",
    "Z": "Kamer / Slaapkamer",
    "ZIT": "Zitkamer",
    "ZOL": "Zolder",
}

ROOMS_EN: Final[dict[str, str]] = {
    "ALG": "General",
    "B": "Bathroom",
    "BA": "Bathroom",
    "BBE": "Bathroom downstairs",
    "BBO": "Bathroom upstairs",
    "BDR": "Business space",
    "BER": "Storage",
    "BIB": "Library",
    "BUE": "Study",
    "D": "Shower",
    "EET": "Dining room",
    "EIN": "Entrance",
    "ENT": "Entrance",
    "F": "Landing",
    "G": "Garage",
    "GAN": "Hallway",
    "GS": "Guest room",
    "HA": "Hallway",
    "HAL": "Hallway",
    "HY": "Hobby room",
    "K": "Kitchen",
    "KA": "Room / Bedroom",
    "KAN": "Office",
    "KEL": "Basement",
    "KEU": "Kitchen",
    "KL": "Basement",
    "ONB": "Unknown",
    "S": "Bedroom",
    "SKA": "Room / Bedroom",
    "T": "Stairwell",
    "TV": "TV room",
    "W": "Living room",
    "WC": "Toilet",
    "WE": "Studio",
    "WK": "Utility room",
    "WKA": "Living room",
    "WW": "Hot water",
    "Z": "Room / Bedroom",
    "ZIT": "Sitting room",
    "ZOL": "Attic",
}
