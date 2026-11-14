"""Async client for the mijnTED portal (Techem heat cost allocators).

The portal is a single-page app on Azure AD B2C. Signing in works without a browser: fetch the
authorize page (PKCE), submit the credentials to SelfAsserted, confirm, and redeem the code. The
API token comes from a refresh-token grant with the API.Call scope.

Never log credentials, tokens, cookies or response bodies of the sign-in requests.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import hashlib
import json
import logging
import re
import secrets
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

TENANT = "mytedprod"
POLICY = "B2C_1_user"
CLIENT_ID = "60e8b8d3-1644-4013-837f-5dc29eb93c70"
REDIRECT_URI = "https://mijnted.nl/"
ORIGIN = "https://mijnted.nl"
B2C_HOST = f"https://{TENANT}.b2clogin.com"
B2C_BASE = f"{B2C_HOST}/{TENANT}.onmicrosoft.com/{POLICY}"
SIGN_IN_SCOPE = "openid profile offline_access"
API_SCOPE = (
    f"openid offline_access https://MyTedprod.onmicrosoft.com/{CLIENT_ID}/API.Call profile"
)
API_BASE = "https://ted-prod-function-app.azurewebsites.net/api"
EXPECTED_SIGN_IN_API = "CombinedSigninAndSignup"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Renew tokens this long before they expire.
TOKEN_MARGIN = timedelta(minutes=2)
# B2C caps refresh tokens at 24 hours for this client; used when the response omits the lifetime.
DEFAULT_REFRESH_LIFETIME = timedelta(hours=24)
EXPORT_CONCURRENCY = 4


class MijntedError(Exception):
    """Base error for the mijnTED client."""


class MijntedAuthError(MijntedError):
    """The portal rejected the credentials."""


class MijntedUnexpectedPageError(MijntedError):
    """The sign-in flow showed a page this client does not handle (MFA, captcha, new layout)."""

    def __init__(self, step: str) -> None:
        super().__init__(f"Unexpected sign-in page at step '{step}'")
        self.step = step


class MijntedConnectionError(MijntedError):
    """A network error, timeout or server error; worth retrying later."""


@dataclass(slots=True)
class Tokens:
    """Tokens for one signed-in account. Never log or print these."""

    access_token: str
    access_expires: datetime
    refresh_token: str
    refresh_expires: datetime
    account_id: str


@dataclass(frozen=True, slots=True)
class BillingPeriod:
    """A heating season (billing period) of a residential unit."""

    start: date
    end: date
    active: bool


@dataclass(frozen=True, slots=True)
class ResidentialUnit:
    """A residential unit (apartment) and the billing unit (complex) it belongs to."""

    id: int
    billing_unit_id: int
    apartment_no: str | None
    export_active: bool


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    """A meter as listed by the portal's device status call."""

    device_number: str
    room: str | None
    reading: float | None
    unit_of_measure: str | None
    installation_date: str | None
    deactivation_date: str | None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying it. Only used to read the account id and expiry."""
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (IndexError, ValueError, binascii.Error):
        return {}
    return claims if isinstance(claims, dict) else {}


def _page_setting(page: str, key: str) -> str | None:
    """Pick a string field out of the sign-in page's SETTINGS literal (not strict JSON)."""
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', page)
    return match.group(1) if match else None


def _parse_date(value: Any) -> date | None:
    """Parse the portal's date formats: '13/11/2026', '2026-06-01T00:00:00', '31-05-2023'."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().split("T")[0].split(" ")[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007 - dates only
        except ValueError:
            continue
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


class MijntedClient:
    """Client for one mijnTED account.

    The session must have its own cookie jar: the B2C sign-in relies on cookies, and they must not
    leak into a shared jar. The jar is cleared before every full sign-in.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        time_zone: tzinfo,
    ) -> None:
        """Initialise the client. Credentials stay in memory only."""
        self._session = session
        self._email = email
        self._password = password
        self._tz = time_zone
        self._tokens: Tokens | None = None
        self._token_lock = asyncio.Lock()

    @property
    def account_id(self) -> str | None:
        """B2C object id of the signed-in account (the token's sub claim)."""
        return self._tokens.account_id if self._tokens else None

    def update_credentials(self, email: str, password: str) -> None:
        """Replace the credentials and drop cached tokens."""
        self._email = email
        self._password = password
        self._tokens = None

    # ----------------------------------------------------------------------------------------
    # Authentication
    # ----------------------------------------------------------------------------------------

    async def async_sign_in(self) -> str:
        """Do a full sign-in and fetch an API token. Returns the account id."""
        async with self._token_lock:
            refresh = await self._sign_in()
            self._tokens = await self._redeem_refresh(refresh)
            return self._tokens.account_id

    async def _async_access_token(self) -> str:
        """Return a valid API token: cached, else refreshed, else from a full sign-in."""
        async with self._token_lock:
            now = datetime.now(UTC)
            tokens = self._tokens
            if tokens and tokens.access_expires - TOKEN_MARGIN > now:
                return tokens.access_token
            if tokens and tokens.refresh_expires - TOKEN_MARGIN > now:
                try:
                    self._tokens = await self._redeem_refresh(tokens.refresh_token)
                except MijntedAuthError:
                    # Refresh token revoked or expired early: fall back to a full sign-in.
                    _LOGGER.debug("Refresh token refused, signing in again")
                    self._tokens = None
                else:
                    return self._tokens.access_token
            refresh = await self._sign_in()
            self._tokens = await self._redeem_refresh(refresh)
            return self._tokens.access_token

    async def _sign_in(self) -> str:
        """Run the B2C sign-in and return a refresh token (sign-in scope)."""
        self._session.cookie_jar.clear()
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())

        _LOGGER.debug("Sign-in: fetching authorize page")
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "response_mode": "fragment",
            "scope": SIGN_IN_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": secrets.token_hex(8),
            "nonce": secrets.token_hex(8),
        }
        async with self._request(
            "GET", f"{B2C_BASE}/oauth2/v2.0/authorize", params=params
        ) as resp:
            page = await resp.text()
            authorize_url = str(resp.url)
            if resp.status != 200:
                raise MijntedConnectionError(f"authorize returned HTTP {resp.status}")

        csrf = _page_setting(page, "csrf")
        trans_id = _page_setting(page, "transId")
        api = _page_setting(page, "api")
        tenant_path = _page_setting(page, "tenant") or f"/{TENANT}.onmicrosoft.com/{POLICY}"
        policy = _page_setting(page, "policy") or POLICY
        if not csrf or not trans_id or api != EXPECTED_SIGN_IN_API:
            raise MijntedUnexpectedPageError("authorize")
        if not tenant_path.startswith("/"):
            raise MijntedUnexpectedPageError("authorize")

        _LOGGER.debug("Sign-in: submitting credentials")
        async with self._request(
            "POST",
            f"{B2C_HOST}{tenant_path}/SelfAsserted",
            params={"tx": trans_id, "p": policy},
            data={"request_type": "RESPONSE", "email": self._email, "password": self._password},
            headers={
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Origin": B2C_HOST,
                "Referer": authorize_url,
            },
        ) as resp:
            if resp.status != 200:
                raise MijntedConnectionError(f"SelfAsserted returned HTTP {resp.status}")
            try:
                reply = json.loads(await resp.text())
            except ValueError as err:
                raise MijntedUnexpectedPageError("credentials") from err
        status = str(reply.get("status")) if isinstance(reply, dict) else None
        if status == "400":
            # Wrong e-mail or password, or a locked account. B2C's message may echo input, so it
            # is not logged.
            raise MijntedAuthError("Credentials rejected")
        if status != "200":
            raise MijntedUnexpectedPageError("credentials")

        _LOGGER.debug("Sign-in: confirming")
        async with self._request(
            "GET",
            f"{B2C_HOST}{tenant_path}/api/{api}/confirmed",
            params={"rememberMe": "false", "csrf_token": csrf, "tx": trans_id, "p": policy},
            allow_redirects=False,
        ) as resp:
            location = resp.headers.get("Location", "")
            if resp.status not in (301, 302, 303) or not location:
                # B2C shows another page here for MFA, terms of use or a password reset.
                raise MijntedUnexpectedPageError("confirm")
        parts = urlsplit(location)
        query = parse_qs(parts.fragment) or parse_qs(parts.query)
        if "code" not in query:
            if "error" in query:
                raise MijntedUnexpectedPageError("confirm")
            raise MijntedUnexpectedPageError("redirect")

        _LOGGER.debug("Sign-in: redeeming authorization code")
        body = await self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": query["code"][0],
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
                "scope": SIGN_IN_SCOPE,
            }
        )
        refresh = body.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise MijntedUnexpectedPageError("token")
        return refresh

    async def _redeem_refresh(self, refresh_token: str) -> Tokens:
        """Exchange a refresh token for an API token."""
        _LOGGER.debug("Requesting API token")
        body = await self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
                "scope": API_SCOPE,
            }
        )
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise MijntedUnexpectedPageError("api_token")
        now = datetime.now(UTC)
        claims = jwt_claims(access)
        account_id = claims.get("sub") or claims.get("oid")
        if not isinstance(account_id, str) or not account_id:
            raise MijntedUnexpectedPageError("api_token")
        expires_in = body.get("expires_in")
        access_life = timedelta(seconds=int(expires_in)) if expires_in else timedelta(minutes=30)
        refresh_in = body.get("refresh_token_expires_in")
        refresh_life = (
            timedelta(seconds=int(refresh_in)) if refresh_in else DEFAULT_REFRESH_LIFETIME
        )
        return Tokens(
            access_token=access,
            access_expires=now + access_life,
            refresh_token=str(body.get("refresh_token") or refresh_token),
            refresh_expires=now + refresh_life,
            account_id=account_id,
        )

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        """POST to the token endpoint. SPA clients must send an Origin header."""
        async with self._request(
            "POST", f"{B2C_BASE}/oauth2/v2.0/token", data=data, headers={"Origin": ORIGIN}
        ) as resp:
            text = await resp.text()
            if resp.status >= 500:
                raise MijntedConnectionError(f"token endpoint returned HTTP {resp.status}")
        try:
            body = json.loads(text)
        except ValueError as err:
            raise MijntedUnexpectedPageError("token") from err
        if not isinstance(body, dict):
            raise MijntedUnexpectedPageError("token")
        if resp.status != 200:
            if body.get("error") in ("invalid_grant", "invalid_request"):
                raise MijntedAuthError("Token request refused")
            raise MijntedConnectionError(f"token endpoint returned HTTP {resp.status}")
        return body

    def _request(self, method: str, url: str, **kwargs: Any) -> _ErrorMapping:
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return _ErrorMapping(self._session.request(method, url, **kwargs))

    # ----------------------------------------------------------------------------------------
    # Portal API
    # ----------------------------------------------------------------------------------------

    async def _api(self, method: str, path: str, **kwargs: Any) -> Any:
        token = await self._async_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": ORIGIN,
            "Accept": "application/json",
        }
        async with self._request(method, f"{API_BASE}{path}", headers=headers, **kwargs) as resp:
            if resp.status == 401:
                # Token rejected: drop it, so the next update signs in again.
                self._tokens = None
                raise MijntedConnectionError(f"{path.split('/')[1]}: token rejected")
            if resp.status != 200:
                raise MijntedConnectionError(f"{path.split('/')[1]}: HTTP {resp.status}")
            text = await resp.text()
        try:
            return json.loads(text) if text else None
        except ValueError as err:
            raise MijntedConnectionError(f"{path.split('/')[1]}: invalid JSON") from err

    async def async_get_residential_unit_ids(self) -> list[int]:
        """Residential units this account may see."""
        data = await self._api("GET", "/profiles/me/access")
        units = data.get("residentialUnits") if isinstance(data, dict) else None
        if not isinstance(units, list):
            return []
        return [int(unit) for unit in units if isinstance(unit, int)]

    async def async_get_residential_unit(self, unit_id: int) -> ResidentialUnit:
        """Details of one residential unit, including its billing unit."""
        data = await self._api("GET", f"/residentialUnitDetailItem/{unit_id}")
        if not isinstance(data, dict) or not isinstance(data.get("billingUnitId"), int):
            raise MijntedConnectionError("residentialUnitDetailItem: no billing unit")
        apartment = data.get("appartmentNo")
        return ResidentialUnit(
            id=unit_id,
            billing_unit_id=data["billingUnitId"],
            apartment_no=str(apartment) if apartment else None,
            export_active=bool(data.get("isMeterValuesExportActive", True)),
        )

    async def async_get_delivery_types(self, unit_id: int) -> list[int]:
        """Delivery types of a unit (1 is heating)."""
        data = await self._api("GET", f"/address/deliveryTypes/{unit_id}")
        if not isinstance(data, list):
            return []
        return [int(item) for item in data if isinstance(item, int)]

    async def async_get_billing_periods(self, unit_id: int) -> list[BillingPeriod]:
        """Heating seasons of a unit."""
        data = await self._api("GET", f"/residentialUnit/{unit_id}/billingPeriods")
        periods: list[BillingPeriod] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            start = _parse_date(item.get("startDate"))
            end = _parse_date(item.get("endDate"))
            if start and end:
                periods.append(BillingPeriod(start, end, bool(item.get("isActive"))))
        return periods

    async def async_get_model(self, unit_id: int, delivery_type: int) -> str | None:
        """Model of the meters of a unit, for example 'F71'."""
        data = await self._api("GET", f"/activeModel/{unit_id}/{delivery_type}")
        return data if isinstance(data, str) and data else None

    async def async_get_last_sync_date(
        self, unit_id: int, delivery_type: int, year: int
    ) -> date | None:
        """Date of the last readout Techem received."""
        data = await self._api("GET", f"/getLastSyncDate/{unit_id}/{delivery_type}/{year}")
        return _parse_date(data)

    async def async_get_device_statuses(
        self, unit_id: int, delivery_type: int, year: int, from_date: date
    ) -> list[DeviceStatus]:
        """Meters of a unit with their room code."""
        data = await self._api(
            "GET",
            f"/deviceStatuses/{unit_id}/{delivery_type}/{year}",
            params={"fromDate": from_date.isoformat()},
        )
        devices: list[DeviceStatus] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not item.get("deviceNumber"):
                continue
            devices.append(
                DeviceStatus(
                    device_number=str(item["deviceNumber"]),
                    room=str(item["room"]) if item.get("room") else None,
                    reading=_as_float(item.get("currentReadingValue")),
                    unit_of_measure=item.get("unitOfMeasure") or None,
                    installation_date=item.get("installationDate") or None,
                    deactivation_date=item.get("deactivationDate") or None,
                )
            )
        return devices

    async def async_get_day_values(
        self, billing_unit_id: int, unit_id: int, day: date
    ) -> dict[str, float]:
        """Cumulative season value per meter on one day."""
        midnight = datetime.combine(day, time(0, 0), tzinfo=self._tz).astimezone(UTC)
        data = await self._api(
            "POST",
            f"/runBillingUnitMeterValuesExport/{billing_unit_id}/DAY",
            params={"date": midnight.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
            json=[unit_id],
        )
        rows = data.get("data") if isinstance(data, dict) else None
        values: dict[str, float] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("deviceNumber"):
                continue
            value = _as_float(row.get("dayValue"))
            if value is not None:
                values[str(row["deviceNumber"])] = value
        return values

    async def async_get_days(
        self, billing_unit_id: int, unit_id: int, days: list[date]
    ) -> dict[date, dict[str, float]]:
        """Fetch several days with limited concurrency."""
        semaphore = asyncio.Semaphore(EXPORT_CONCURRENCY)

        async def fetch(day: date) -> tuple[date, dict[str, float]]:
            async with semaphore:
                return day, await self.async_get_day_values(billing_unit_id, unit_id, day)

        results = await asyncio.gather(*(fetch(day) for day in days))
        return dict(results)


class _ErrorMapping:
    """Wrap a request context manager and map aiohttp errors to MijntedConnectionError."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def __aenter__(self) -> aiohttp.ClientResponse:
        try:
            return await self._ctx.__aenter__()  # type: ignore[no-any-return]
        except (aiohttp.ClientError, TimeoutError) as err:
            # Only the exception type: messages can contain URLs with query parameters.
            raise MijntedConnectionError(type(err).__name__) from None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._ctx.__aexit__(exc_type, exc, tb)
        if isinstance(exc, aiohttp.ClientError | TimeoutError):
            raise MijntedConnectionError(type(exc).__name__) from None
