"""Async client for the Réséda customer portal."""

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import (
    AUTH_BASE,
    CLIENT_ID,
    ENTREPRISE,
    GROUPES_GRANDEUR,
    OAUTH_SCOPE,
    REDIRECT_URI,
    REST_BASE,
)

_LOGGER = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "entreprise": ENTREPRISE,
    "Origin": "https://monagence.reseda.fr",
    "Referer": "https://monagence.reseda.fr/",
}

# Refresh a bit before the access token actually expires.
_TOKEN_EXPIRY_MARGIN = 60


class ResedaError(Exception):
    """Base error for the Réséda client."""


class ResedaConnectionError(ResedaError):
    """Network-level failure."""


class ResedaAuthError(ResedaError):
    """Authentication failed (bad credentials or rejected refresh)."""


class ResedaApiError(ResedaError):
    """The API returned an unexpected response."""


@dataclass(slots=True)
class PascSummary:
    """One contract — Point d'Accès aux Services Client."""

    id: str
    reference: str
    activity: str
    activation_date: datetime | None
    pds_reference: str
    pds_id: str
    delivery_space_id: str | None


@dataclass(slots=True)
class Address:
    """Address of a delivery point."""

    line: str
    commune: str
    code_postal: str


@dataclass(slots=True)
class DailyReading:
    """One day's reading for one tariff poste."""

    day: date
    poste_mnemo: str
    consumption_kwh: float
    index_kwh: float


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE (verifier, S256 challenge) pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class ResedaClient:
    """Talks to monagence.reseda.fr.

    The same ``ClientSession`` is reused across calls so the cookie set by the
    login endpoint authorises ``/auth/authorize-internet``.
    """

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        refresh_token: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._username = username
        self._password = password
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._access_expires_at: float = 0.0

    @property
    def refresh_token(self) -> str | None:
        """Latest refresh token (None until first successful login)."""
        return self._refresh_token

    async def async_ensure_token(self) -> None:
        """Ensure we hold a valid access token."""
        if self._access_token and time.time() < self._access_expires_at:
            return
        if self._refresh_token:
            try:
                await self._async_refresh()
            except ResedaAuthError:
                _LOGGER.info("Refresh token rejected, falling back to full login")
            else:
                return
        await self._async_login()

    async def _async_password_step(self) -> None:
        """Step 1 — credentials check, sets the session cookie."""
        body = {
            "username": self._username,
            "password": self._password,
            "client_id": CLIENT_ID,
        }
        try:
            async with (
                self._session.post(
                    f"{AUTH_BASE}/externe/authentification",  # codespell:ignore authentification
                    data=body,
                    headers=_DEFAULT_HEADERS,
                ) as resp
            ):
                if resp.status == 401:
                    raise ResedaAuthError("Invalid credentials")
                resp.raise_for_status()
                payload = await resp.json()
        except ClientResponseError as err:
            raise ResedaApiError(f"login HTTP {err.status}") from err
        except ClientError as err:
            raise ResedaConnectionError(str(err)) from err

        if payload.get("code") != "0":
            raise ResedaAuthError(
                f"Authentication rejected: {payload.get('libelle', 'unknown')}"
            )

    async def _async_authorize_step(self, code_challenge: str) -> str:
        """Step 2 — GET /auth/authorize-internet; return the authorization code."""
        params = {
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "client_id": CLIENT_ID,
        }
        try:
            async with self._session.get(
                f"{AUTH_BASE}/authorize-internet",
                params=params,
                headers=_DEFAULT_HEADERS,
                allow_redirects=False,
            ) as resp:
                if resp.status not in (302, 303):
                    raise ResedaAuthError(
                        f"authorize-internet returned {resp.status}, expected redirect"
                    )
                location = resp.headers.get("Location", "")
        except ClientError as err:
            raise ResedaConnectionError(str(err)) from err

        code = parse_qs(urlparse(location).query).get("code", [None])[0]
        if not code:
            raise ResedaAuthError(
                "No authorization code returned from authorize-internet"
            )
        return code

    async def _async_token_exchange(
        self, *, code: str | None = None, verifier: str | None = None
    ) -> None:
        """Step 3 / refresh — POST /auth/tokenUtilisateurInternet."""
        if code is not None and verifier is not None:
            body: dict[str, str] = {
                "client_id": CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            }
        elif self._refresh_token:
            body = {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "scope": OAUTH_SCOPE,
            }
        else:
            raise ResedaError("No code or refresh_token available for token exchange")

        try:
            async with self._session.post(
                f"{AUTH_BASE}/tokenUtilisateurInternet",
                data=body,
                headers=_DEFAULT_HEADERS,
            ) as resp:
                if resp.status in (400, 401):
                    raise ResedaAuthError(f"token exchange rejected ({resp.status})")
                resp.raise_for_status()
                payload = await resp.json()
        except ClientResponseError as err:
            raise ResedaApiError(f"token HTTP {err.status}") from err
        except ClientError as err:
            raise ResedaConnectionError(str(err)) from err

        try:
            self._access_token = payload["access_token"]
            self._refresh_token = payload["refresh_token"]
            self._access_expires_at = (
                time.time() + int(payload["expires_in"]) - _TOKEN_EXPIRY_MARGIN
            )
        except (KeyError, TypeError, ValueError) as err:
            raise ResedaApiError(f"Malformed token payload: {payload}") from err

    async def _async_rattacher_pascs(self) -> None:
        """Step 4 — POST /produits/acteurs/courant/rattacherPASCS (binds PASCs)."""
        try:
            async with self._session.post(
                f"{REST_BASE}/produits/acteurs/courant/rattacherPASCS",
                headers=self._auth_headers(),
                json={},
            ) as resp:
                if resp.status not in (200, 204):
                    raise ResedaApiError(f"rattacherPASCS returned {resp.status}")
        except ClientError as err:
            raise ResedaConnectionError(str(err)) from err

    async def _async_login(self) -> None:
        """Full username/password login (steps 1-4)."""
        await self._async_password_step()
        verifier, challenge = _pkce_pair()
        code = await self._async_authorize_step(challenge)
        await self._async_token_exchange(code=code, verifier=verifier)
        await self._async_rattacher_pascs()

    async def _async_refresh(self) -> None:
        """Refresh the access token using the stored refresh token."""
        await self._async_token_exchange()

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            raise ResedaError("Client has no access token")
        return {
            **_DEFAULT_HEADERS,
            "Authorization": f"Bearer {self._access_token}",
        }

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Authenticated request that retries once on 401."""
        await self.async_ensure_token()
        for attempt in range(2):
            try:
                async with self._session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._auth_headers(),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        self._access_token = None
                        await self.async_ensure_token()
                        continue
                    if resp.status >= 400:
                        text = await resp.text()
                        raise ResedaApiError(
                            f"{method} {url} → {resp.status}: {text[:200]}"
                        )
                    return await resp.json()
            except ClientError as err:
                raise ResedaConnectionError(str(err)) from err
        raise ResedaApiError(f"{method} {url} unreachable after retry")

    async def async_get_pascs(self) -> list[PascSummary]:
        """List the user's contracts."""
        payload = await self._request_json(
            "GET",
            f"{REST_BASE}/produits/pointsAccesServicesClient",
            params={"expand": "pointDeService(espaceDeLivraison)"},
        )
        return [_parse_pasc(item) for item in payload]

    async def async_get_address(self, delivery_space_id: str) -> Address:
        """Fetch the postal address for a delivery space."""
        url = f"{REST_BASE}/produits/espacesDeLivraison/{delivery_space_id}/adresse"  # codespell:ignore adresse
        payload = await self._request_json("GET", url)
        return Address(
            line=payload.get("adresseComplete") or "",
            commune=payload.get("commune") or "",
            code_postal=payload.get("codePostal") or "",
        )

    async def async_get_history(
        self, pasc_id: str, start: datetime, end: datetime
    ) -> list[DailyReading]:
        """Fetch daily consumption between two timestamps."""
        body = {
            "typeObjet": "DonneesHistoriqueMesureRepresentation",
            "dateDebut": _to_z(start),
            "dateFin": _to_z(end),
            "pointAccesServicesClient": {
                "typeObjet": "produit.PointAccesServicesClient",
                "id": pasc_id,
            },
            "groupesDeGrandeurs": [
                {
                    "typeObjet": "produit.GroupeGrandeur",
                    "codeGroupeGrandeur": {"code": code},
                }
                for code in GROUPES_GRANDEUR
            ],
        }
        payload = await self._request_json(
            "POST",
            f"{REST_BASE}/interfaces/aelgrd/historiqueDeMesure",
            json=body,
        )
        return _parse_history(payload)


def _parse_pasc(item: dict[str, Any]) -> PascSummary:
    pds = item.get("pointDeService") or {}
    delivery_space = pds.get("espaceDeLivraison") or {}
    activity_field = "activite"  # codespell:ignore activite
    activity = (pds.get(activity_field) or {}).get("libelle") or ""
    return PascSummary(
        id=item["id"],
        reference=item.get("reference") or "",
        activity=activity,
        activation_date=_parse_dt(item.get("dateDebut")),
        pds_reference=pds.get("reference") or pds.get("referenceExterne") or "",
        pds_id=pds.get("id") or "",
        delivery_space_id=delivery_space.get("id"),
    )


def _parse_history(payload: dict[str, Any]) -> list[DailyReading]:
    readings: list[DailyReading] = []
    for period in (
        payload.get("periodesActivite") or []
    ):  # codespell:ignore periodesactivite
        block = period.get("blocGRD") or {}
        for poste in block.get("postesHorosaisonnier") or []:
            mnemo = poste.get("mnemo") or ""
            for entry in poste.get("consommationsJournalieres") or []:
                day = _parse_day(entry.get("date"))
                if day is None:
                    continue
                readings.append(
                    DailyReading(
                        day=day,
                        poste_mnemo=mnemo,
                        consumption_kwh=float(entry.get("consommation") or 0.0),
                        index_kwh=float(entry.get("index") or 0.0),
                    )
                )
    readings.sort(key=lambda r: (r.day, r.poste_mnemo))
    return readings


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


def _to_z(value: datetime) -> str:
    """Render a datetime as ``YYYY-MM-DDTHH:MM:SSZ`` in UTC (HAR format)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
