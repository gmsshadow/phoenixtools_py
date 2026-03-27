from __future__ import annotations

from dataclasses import dataclass

import httpx


NEXUS_DOMAIN = "phoenixbse.com"
INDEX_PATH = "/index.php"
MARKET_XML_URL = f"http://{NEXUS_DOMAIN}{INDEX_PATH}?a=game&sa=markets&type=xml"


@dataclass(frozen=True)
class MarketXmlClient:
    timeout_s: float = 60.0

    def fetch(self) -> str:
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(MARKET_XML_URL)
            resp.raise_for_status()
            return resp.text

