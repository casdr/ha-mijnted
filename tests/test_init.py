"""Setup tests: devices, reauth, unsupported sign-in pages, disabled export."""

from __future__ import annotations

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mijnted.api import (
    MijntedAuthError,
    MijntedUnexpectedPageError,
)
from custom_components.mijnted.const import DOMAIN

from .fake_portal import EMAIL, FakePortal
from .helpers import at, setup_entry

pytestmark = pytest.mark.usefixtures("amsterdam")


@pytest.mark.usefixtures("mock_client")
async def test_devices_and_names(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """Meters are named after their room plus number and linked to the apartment."""
    at(freezer, "2026-11-14 12:00:00")
    await setup_entry(hass, config_entry)
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, config_entry.entry_id)
    hub = next(
        device for device in devices if (DOMAIN, "unit_100001") in device.identifiers
    )
    meters = [device for device in devices if device.via_device_id == hub.id]
    assert len(meters) == 6
    names = sorted(device.name for device in meters)
    assert names[0] == "Kitchen 90000004"
    assert "Living room 90000001" in names
    assert hub.model == "F71"


@pytest.mark.usefixtures("mock_client")
async def test_bad_credentials_start_reauth(
    hass: HomeAssistant, portal: FakePortal, config_entry: MockConfigEntry
) -> None:
    portal.fail_with = MijntedAuthError("rejected")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.usefixtures("mock_client")
async def test_unexpected_page_at_setup(
    hass: HomeAssistant, portal: FakePortal, config_entry: MockConfigEntry
) -> None:
    """An MFA or captcha page fails setup without retrying and raises a repair issue."""
    portal.fail_with = MijntedUnexpectedPageError("confirm")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"unexpected_sign_in_page_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"step": "confirm", "title": EMAIL}


@pytest.mark.usefixtures("mock_client")
async def test_export_disabled_raises_issue(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    at(freezer, "2026-11-14 12:00:00")
    portal.export_active = False
    await setup_entry(hass, config_entry)
    assert config_entry.runtime_data.data.meters == {}
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "export_disabled_100001") is not None
    )
