"""Shared fixtures for the mijnTED tests."""

from __future__ import annotations

import annotationlib
import inspect
import types
import unittest.mock
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mijnted.const import DOMAIN

from .fake_portal import ACCOUNT_ID, EMAIL, PASSWORD, FakePortal

if TYPE_CHECKING:
    from collections.abc import Generator


class _ForwardRefInspect(types.ModuleType):
    """`inspect` for unittest.mock that leaves unresolvable annotations as forward refs.

    pytest-homeassistant-custom-component autospecs recorder functions whose annotations
    name classes imported only under TYPE_CHECKING. On Python 3.14.2, autospec evaluates
    those annotations and fails with NameError.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(inspect, name)

    @staticmethod
    def signature(obj: Any, **kwargs: Any) -> inspect.Signature:
        """Return the signature, leaving unresolvable annotations unevaluated."""
        kwargs.setdefault("annotation_format", annotationlib.Format.FORWARDREF)
        return inspect.signature(obj, **kwargs)


unittest.mock.inspect = _ForwardRefInspect("inspect")  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(  # pylint: disable=unused-argument
    recorder_mock: Any, enable_custom_integrations: None
) -> None:
    """Load the integration from custom_components, with the recorder it depends on.

    The recorder has to be set up before `hass`, so it is requested here first.
    """


@pytest.fixture(name="amsterdam")
async def amsterdam_fixture(hass: Any) -> None:
    """Run Home Assistant in the portal's time zone."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")


@pytest.fixture(autouse=True)
def no_backfill_delay() -> Generator[None]:
    """Run the previous-season backfill without its pause between requests."""
    with patch("custom_components.mijnted.season_tracker.BACKFILL_DELAY", 0):
        yield


@pytest.fixture(name="portal")
def portal_fixture() -> FakePortal:
    """Return a fake portal with no values yet."""
    return FakePortal()


@pytest.fixture(name="mock_client")
def mock_client_fixture(portal: FakePortal) -> Generator[FakePortal]:
    """Use the fake portal for config entries and the config flow."""
    with (
        patch("custom_components.mijnted.MijntedClient", return_value=portal),
        patch(
            "custom_components.mijnted.config_flow.MijntedClient", return_value=portal
        ),
    ):
        yield portal


@pytest.fixture(name="config_entry")
def config_entry_fixture() -> MockConfigEntry:
    """Return a configured mijnTED entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=ACCOUNT_ID,
        data={"email": EMAIL, "password": PASSWORD},
    )
