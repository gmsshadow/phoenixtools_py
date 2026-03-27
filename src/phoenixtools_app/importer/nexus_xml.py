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

    def fetch(self, sa: str, **extra_params: object) -> str:
        # Mirrors Ruby: "#{XML_BASE}?a=xml&uid=#{user_id}&code=#{xml_code}&sa=#{data_type}"
        params = {"a": "xml", "uid": str(self._cfg.user_id), "code": self._cfg.xml_code, "sa": sa}
        for k, v in extra_params.items():
            if v is None:
                continue
            params[str(k)] = str(v)
        resp = self._client.get(XML_BASE, params=params)
        resp.raise_for_status()
        text = resp.text
        # Nexus XML errors are commonly returned as:
        # <?xml ...?><data><error>...</error></data>
        if "<error>" in text.lower():
            start = text.lower().find("<error>")
            end = text.lower().find("</error>", start + 7)
            if start >= 0 and end > start:
                msg = text[start + 7 : end].strip()
                raise RuntimeError(f"Nexus XML error for sa={sa}: {msg}")
        return text

