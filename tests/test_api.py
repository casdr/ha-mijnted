"""Tests for the mijnTED client against recorded, anonymised portal responses."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from datetime import date
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.mijnted.api import (
    MijntedAuthError,
    MijntedClient,
    MijntedConnectionError,
    MijntedUnexpectedPageError,
)

from .conftest import ACCOUNT_ID, BILLING_UNIT_ID, EMAIL, PASSWORD, UNIT_ID, load_json, load_text

TZ = ZoneInfo("Europe/Amsterdam")
AUTHORIZE = re.compile(r"/oauth2/v2\.0/authorize")
SELF_ASSERTED = re.compile(r"/SelfAsserted")
CONFIRMED = re.compile(r"/api/CombinedSigninAndSignup/confirmed")
TOKEN = re.compile(r"/oauth2/v2\.0/token")
API = "https://ted-prod-function-app.azurewebsites.net/api"


def fake_jwt(claims: dict[str, object]) -> str:
    """An unsigned JWT; the client only reads claims."""

    def part(data: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


class TokenEndpoint:
    """Answers the B2C token endpoint by grant type; set `refuse_refresh` to reject refreshes."""

    def __init__(self) -> None:
        self.grants: list[dict[str, Any]] = []
        self.refuse_refresh = False

    async def __call__(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        self.grants.append(dict(data))
        if data["grant_type"] == "authorization_code":
            body: dict[str, Any] = {"refresh_token": "refresh-1", "refresh_token_expires_in": 86400}
            return AiohttpClientMockResponse(method, url, json=body)
        if self.refuse_refresh:
            self.refuse_refresh = False
            return AiohttpClientMockResponse(
                method, url, status=400, json={"error": "invalid_grant"}
            )
        body = {
            "access_token": fake_jwt({"sub": ACCOUNT_ID, "scp": "API.Call"}),
            "expires_in": 3600,
            "refresh_token": "refresh-2",
            "refresh_token_expires_in": 86400,
        }
        return AiohttpClientMockResponse(method, url, json=body)


@pytest.fixture
def mocker() -> AiohttpClientMocker:
    return AiohttpClientMocker()


@pytest.fixture
async def client(mocker: AiohttpClientMocker) -> AsyncGenerator[MijntedClient]:
    session = mocker.create_session(asyncio.get_running_loop())
    yield MijntedClient(session, EMAIL, PASSWORD, TZ)
    await session.close()


@pytest.fixture
def token_endpoint() -> TokenEndpoint:
    return TokenEndpoint()


def mock_sign_in(
    mocker: AiohttpClientMocker,
    token_endpoint: TokenEndpoint,
    *,
    page: str | None = None,
    self_asserted: str = '{"status":"200"}',
    confirmed_status: int = 302,
) -> None:
    mocker.get(AUTHORIZE, text=load_text("authorize.html") if page is None else page)
    mocker.post(SELF_ASSERTED, text=self_asserted)
    mocker.get(
        CONFIRMED,
        status=confirmed_status,
        headers={"Location": "https://mijnted.nl/#state=x&client_info=y&code=the-code"}
        if confirmed_status == 302
        else None,
        text="<html>Enter the code we sent you</html>" if confirmed_status != 302 else None,
    )
    mocker.post(TOKEN, side_effect=token_endpoint)


def calls_to(mocker: AiohttpClientMocker, fragment: str) -> list[tuple[Any, ...]]:
    return [call for call in mocker.mock_calls if fragment in str(call[1])]


async def test_sign_in_happy_path(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    """A full sign-in posts the credentials with the page's CSRF token and gets an API token."""
    mock_sign_in(mocker, token_endpoint)
    assert await client.async_sign_in() == ACCOUNT_ID

    _, _, data, headers = calls_to(mocker, "SelfAsserted")[0]
    assert headers["X-CSRF-TOKEN"] == "ZmFrZS1jc3JmLXRva2Vu"
    assert data["email"] == EMAIL
    assert data["request_type"] == "RESPONSE"
    code_grant, refresh_grant = token_endpoint.grants
    assert code_grant["grant_type"] == "authorization_code"
    assert code_grant["code"] == "the-code"
    assert code_grant["scope"] == "openid profile offline_access"
    assert refresh_grant["grant_type"] == "refresh_token"
    assert "API.Call" in refresh_grant["scope"]
    assert calls_to(mocker, "/token")[0][3]["Origin"] == "https://mijnted.nl"
    assert client.account_id == ACCOUNT_ID


async def test_bad_password(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    """B2C answers status 400 for a wrong password."""
    mock_sign_in(
        mocker,
        token_endpoint,
        self_asserted='{"status":"400","message":"Your password is incorrect."}',
    )
    with pytest.raises(MijntedAuthError):
        await client.async_sign_in()
    assert token_endpoint.grants == []


@pytest.mark.parametrize(
    "page",
    [
        "<html>no settings here</html>",
        load_text("authorize.html").replace(
            '"api":"CombinedSigninAndSignup"', '"api":"Phonefactor"'
        ),
    ],
    ids=["layout_changed", "mfa_page"],
)
async def test_unexpected_authorize_page(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint, page: str
) -> None:
    mock_sign_in(mocker, token_endpoint, page=page)
    with pytest.raises(MijntedUnexpectedPageError) as err:
        await client.async_sign_in()
    assert err.value.step == "authorize"
    assert calls_to(mocker, "SelfAsserted") == []


async def test_extra_step_after_credentials(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    """A page instead of the redirect after confirming (MFA, terms of use) is unexpected."""
    mock_sign_in(mocker, token_endpoint, confirmed_status=200)
    with pytest.raises(MijntedUnexpectedPageError) as err:
        await client.async_sign_in()
    assert err.value.step == "confirm"


async def test_server_error_is_transient(
    client: MijntedClient, mocker: AiohttpClientMocker
) -> None:
    mocker.get(AUTHORIZE, status=503, text="busy")
    with pytest.raises(MijntedConnectionError):
        await client.async_sign_in()


async def test_network_error_is_transient(
    client: MijntedClient, mocker: AiohttpClientMocker
) -> None:
    mocker.get(AUTHORIZE, exc=aiohttp.ClientConnectionError("boom"))
    with pytest.raises(MijntedConnectionError):
        await client.async_sign_in()


async def test_token_is_reused(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    """Two API calls after one sign-in do not sign in again."""
    mock_sign_in(mocker, token_endpoint)
    mocker.get(f"{API}/profiles/me/access", json=load_json("access.json"))
    assert await client.async_get_residential_unit_ids() == [UNIT_ID]
    assert await client.async_get_residential_unit_ids() == [UNIT_ID]
    assert len(calls_to(mocker, "SelfAsserted")) == 1
    assert len(token_endpoint.grants) == 2


async def test_expired_access_token_uses_refresh_token(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    mock_sign_in(mocker, token_endpoint)
    mocker.get(f"{API}/profiles/me/access", json=load_json("access.json"))
    await client.async_sign_in()
    assert client._tokens is not None
    client._tokens.access_expires = client._tokens.access_expires.replace(year=2000)

    await client.async_get_residential_unit_ids()
    assert len(calls_to(mocker, "SelfAsserted")) == 1
    assert [grant["grant_type"] for grant in token_endpoint.grants][-1] == "refresh_token"


async def test_refused_refresh_falls_back_to_sign_in(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    mock_sign_in(mocker, token_endpoint)
    mocker.get(f"{API}/profiles/me/access", json=load_json("access.json"))
    await client.async_sign_in()
    assert client._tokens is not None
    client._tokens.access_expires = client._tokens.access_expires.replace(year=2000)
    token_endpoint.refuse_refresh = True

    assert await client.async_get_residential_unit_ids() == [UNIT_ID]
    assert len(calls_to(mocker, "SelfAsserted")) == 2


async def test_rejected_api_token_is_dropped(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    mock_sign_in(mocker, token_endpoint)
    mocker.get(f"{API}/profiles/me/access", status=401)
    with pytest.raises(MijntedConnectionError):
        await client.async_get_residential_unit_ids()
    assert client.account_id is None


async def test_discovery_endpoints(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    mock_sign_in(mocker, token_endpoint)
    mocker.get(f"{API}/residentialUnitDetailItem/{UNIT_ID}", json=load_json("residential_unit.json"))
    mocker.get(f"{API}/address/deliveryTypes/{UNIT_ID}", json=load_json("delivery_types.json"))
    mocker.get(
        f"{API}/residentialUnit/{UNIT_ID}/billingPeriods", json=load_json("billing_periods.json")
    )
    mocker.get(f"{API}/activeModel/{UNIT_ID}/1", json="F71")
    mocker.get(f"{API}/getLastSyncDate/{UNIT_ID}/1/2027", json=load_json("last_sync_date.json"))
    mocker.get(
        f"{API}/deviceStatuses/{UNIT_ID}/1/2027?fromDate=2026-11-14",
        json=load_json("device_statuses.json"),
    )

    unit = await client.async_get_residential_unit(UNIT_ID)
    assert unit.billing_unit_id == BILLING_UNIT_ID
    assert unit.export_active
    assert await client.async_get_delivery_types(UNIT_ID) == [1]
    periods = await client.async_get_billing_periods(UNIT_ID)
    assert periods[0].active
    assert periods[0].end == date(2027, 5, 31)
    assert await client.async_get_model(UNIT_ID, 1) == "F71"
    assert await client.async_get_last_sync_date(UNIT_ID, 1, 2027) == date(2026, 11, 13)
    statuses = await client.async_get_device_statuses(UNIT_ID, 1, 2027, date(2026, 11, 14))
    assert [(s.device_number, s.room) for s in statuses][:3] == [
        ("90000001", "W"),
        ("90000002", "KA"),
        ("90000003", "KA"),
    ]


async def test_day_values_use_local_midnight(
    client: MijntedClient, mocker: AiohttpClientMocker, token_endpoint: TokenEndpoint
) -> None:
    """The export for 13 November is requested as Amsterdam midnight in UTC."""
    exports = load_json("export_day.json")
    mock_sign_in(mocker, token_endpoint)
    mocker.post(
        f"{API}/runBillingUnitMeterValuesExport/{BILLING_UNIT_ID}/DAY"
        "?date=2026-11-12T23:00:00.000Z",
        json=exports["2026-11-12T23:00:00.000Z"],
    )
    values = await client.async_get_day_values(BILLING_UNIT_ID, UNIT_ID, date(2026, 11, 13))
    _, _, body, _ = calls_to(mocker, "runBillingUnitMeterValuesExport")[0]
    assert body == [UNIT_ID]
    assert values["90000001"] == 22.0
    assert values["90000005"] == 24.0
