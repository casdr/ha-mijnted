"""Config flow tests: sign-in, bad password, reauth, reconfigure and options."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mijnted.api import (
    MijntedAuthError,
    MijntedConnectionError,
    MijntedUnexpectedPageError,
    SignInStep,
)
from custom_components.mijnted.const import CONF_UPDATE_INTERVAL, DOMAIN

from .fake_portal import ACCOUNT_ID, EMAIL, PASSWORD, FakePortal

if TYPE_CHECKING:
    from collections.abc import Generator

    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("mock_client", "mock_setup_entry")


@pytest.fixture(name="mock_setup_entry")
def mock_setup_entry_fixture() -> Generator[AsyncMock]:
    """Skip setting up the entry after the flow."""
    with patch(
        "custom_components.mijnted.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def test_user_flow_happy_path(hass: HomeAssistant, portal: FakePortal) -> None:
    """Test user flow happy path."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": f" {EMAIL} ", "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {"email": EMAIL, "password": PASSWORD}
    assert result["result"].unique_id == ACCOUNT_ID
    assert portal.sign_ins == 1


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (MijntedAuthError("rejected"), "invalid_auth"),
        (MijntedConnectionError("timeout"), "cannot_connect"),
        (MijntedUnexpectedPageError(SignInStep.CONFIRM), "unexpected_page"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant, portal: FakePortal, error: Exception, key: str
) -> None:
    """A bad password shows an error; the form keeps the e-mail and a retry succeeds."""
    portal.fail_with = error  # type: ignore[assignment]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    portal.fail_with = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_without_residential_units(
    hass: HomeAssistant, portal: FakePortal
) -> None:
    """Test user flow without residential units."""
    portal.async_get_residential_unit_ids = AsyncMock(return_value=[])  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["errors"] == {"base": "no_residential_units"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test user flow already configured."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "other@example.com", "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, portal: FakePortal, config_entry: MockConfigEntry
) -> None:
    """Test reauth."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == EMAIL

    portal.fail_with = MijntedAuthError("rejected")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    portal.fail_with = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {"email": EMAIL, "password": "new-password"}


async def test_reauth_with_other_account(hass: HomeAssistant) -> None:
    """Test reauth with other account."""
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="someone-else",
        data={"email": EMAIL, "password": PASSWORD},
    )
    other.add_to_hass(hass)
    result = await other.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reconfigure(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Test reconfigure."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "new@example.com", "password": "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {"email": "new@example.com", "password": "new-password"}
    assert config_entry.title == "new@example.com"


async def test_options_update_interval(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test options update interval."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 3}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_UPDATE_INTERVAL: 3}


async def test_options_reject_below_one_hour(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test options reject below one hour."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData, match="update_interval"):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_UPDATE_INTERVAL: 0.5}
        )
