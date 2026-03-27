from __future__ import annotations

from dataclasses import dataclass

import httpx


NEXUS_DOMAIN = "phoenixbse.com"
INDEX_PATH = "/index.php"
XML_BASE = f"http://{NEXUS_DOMAIN}{INDEX_PATH}"


@dataclass(frozen=True)
class NexusXmlConfig:
    user_id: int
    xml_code: str


class NexusXmlClient:
    def __init__(self, cfg: NexusXmlConfig, *, timeout_s: float = 30.0) -> None:
        self._cfg = cfg
        self._client = httpx.Client(timeout=timeout_s, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def fetch(self, sa: str) -> str:
        # Mirrors Ruby: "#{XML_BASE}?a=xml&uid=#{user_id}&code=#{xml_code}&sa=#{data_type}"
        params = {"a": "xml", "uid": str(self._cfg.user_id), "code": self._cfg.xml_code, "sa": sa}
        resp = self._client.get(XML_BASE, params=params)
        resp.raise_for_status()
        return resp.text

