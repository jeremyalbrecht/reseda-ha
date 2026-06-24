"""Constants for the Réséda integration."""

from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "reseda"

BASE_URL = "https://monagence.reseda.fr/application"
AUTH_BASE = f"{BASE_URL}/auth"
REST_BASE = f"{BASE_URL}/rest"

REDIRECT_URI = "https://monagence.reseda.fr/autorisation-callback.html"
CLIENT_ID = "yxXMT6m_bHQYsUW_eNsPIhW"
ENTREPRISE = "SASGRDUEM"
OAUTH_SCOPE = "externe:public"

SCAN_INTERVAL = timedelta(hours=6)
TZ_PARIS = ZoneInfo("Europe/Paris")

# Code values for the three "groupesDeGrandeurs" the portal queries.
GROUPES_GRANDEUR: tuple[str, ...] = ("2", "3", "4")

HISTORY_BACKFILL_DAYS = 730  # ~2 years on first import

CONF_PASC_ID = "pasc_id"
CONF_PASC_REF = "pasc_ref"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_PRICE_PER_KWH = "price_per_kwh"
