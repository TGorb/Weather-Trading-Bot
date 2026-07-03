"""
Authenticated REST client for the Kalshi trading API, with retry/backoff.

Routes to the demo API when PAPER_TRADING=true and to production otherwise.
Never logs credential values.
"""

import os
import time

import requests

from kalshi.auth import build_auth_headers, load_private_key

PROD_URL = "https://trading-api.kalshi.com/trade-api/v2"
DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"


class KalshiClient:
    def __init__(self):
        key_id = os.getenv("KALSHI_KEY_ID")
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
        if not key_id or not key_path:
            raise EnvironmentError(
                "KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH must be set in .env"
            )

        self.key_id = key_id
        self.private_key = load_private_key(key_path)
        self.base_url = (
            DEMO_URL if os.getenv("PAPER_TRADING", "true").lower() == "true" else PROD_URL
        )
        self.session = requests.Session()

    def _headers(self, method: str, path: str) -> dict:
        return build_auth_headers(method, path, self.key_id, self.private_key)

    def get(self, path: str, params: dict = None, retries: int = 3) -> dict:
        full_path = f"/trade-api/v2{path}"
        last_exc = None
        for attempt in range(retries):
            try:
                r = self.session.get(
                    f"{self.base_url}{path}",
                    headers=self._headers("GET", full_path),
                    params=params,
                    timeout=10,
                )
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_exc = e
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)
        if last_exc:
            raise last_exc
        return {}

    def post(self, path: str, data: dict, retries: int = 3) -> dict:
        full_path = f"/trade-api/v2{path}"
        last_exc = None
        for attempt in range(retries):
            try:
                r = self.session.post(
                    f"{self.base_url}{path}",
                    headers=self._headers("POST", full_path),
                    json=data,
                    timeout=10,
                )
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_exc = e
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)
        if last_exc:
            raise last_exc
        return {}
