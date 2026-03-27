from __future__ import annotations

from dataclasses import dataclass

import httpx


NEXUS_DOMAIN = "phoenixbse.com"
INDEX_URL = f"http://{NEXUS_DOMAIN}/index.php"


@dataclass(frozen=True)
class NexusHtmlConfig:
    nexus_user: str
    nexus_password: str


class NexusHtmlClient:
    def __init__(self, cfg: NexusHtmlConfig, *, timeout_s: float = 45.0) -> None:
        self._cfg = cfg
        self._client = httpx.Client(timeout=timeout_s, follow_redirects=True)
        self._logged_in = False

    def close(self) -> None:
        self._client.close()

    def login(self) -> None:
        # Mirrors Rails: POST /index.php?a=home&sa=first with login form
        params = {"a": "home", "sa": "first"}
        data = {
            "UserName": self._cfg.nexus_user,
            "PassWord": self._cfg.nexus_password,
            "forever": "on",
            "Action": "Login",
        }
        self._client.cookies.set("USE_COOKIES", "1")
        resp = self._client.post(INDEX_URL, params=params, data=data)
        resp.raise_for_status()
        # A successful login yields a PHPSESSID cookie.
        if "PHPSESSID" not in self._client.cookies:
            raise RuntimeError("Login failed (no session cookie received). Check username/password.")
        self._logged_in = True

    def get(self, a: str, sa: str | None = None, *, id: int | None = None, sys: int | None = None) -> str:
        if not self._logged_in:
            self.login()
        params: dict[str, str] = {"a": a}
        if sa is not None:
            params["sa"] = sa
        if id is not None:
            params["id"] = str(id)
        if sys is not None:
            params["sys"] = str(sys)
        resp = self._client.get(INDEX_URL, params=params)
        resp.raise_for_status()
        return resp.text

    def get_turn_html(self, base_id: int) -> str:
        """
        Mirrors Rails `get_turn(id)`:
        - Load turns list page.
        - Find the specific base anchor containing '(id)'.
        - Follow onclick popup URL to the report page.
        """
        if not self._logged_in:
            self.login()

        list_html = self._client.get(
            INDEX_URL,
            params={"a": "turns", "sa": "list", "la": "find", "id": str(base_id)},
        )
        list_html.raise_for_status()

        from lxml import html as lxml_html

        doc = lxml_html.fromstring(list_html.text)
        for td in doc.xpath('//td[contains(@class,"turns_tab_off")]'):
            a = td.xpath(".//a")
            if not a:
                continue
            anchor = a[0]
            text = (anchor.text_content() or "").strip()
            if f"({base_id})" not in text:
                continue
            onclick = (anchor.get("onclick") or "").strip()
            # Example: window.open('/index.php?a=...','_blank',...)
            start = onclick.find("/index.php")
            if start < 0:
                continue
            end = onclick.find('"', start)
            if end < 0:
                end = onclick.find("'", start)
            if end < 0:
                end = len(onclick)
            url_path = onclick[start:end]
            if not url_path.startswith("/"):
                url_path = "/" + url_path
            report = self._client.get(f"http://{NEXUS_DOMAIN}{url_path}")
            report.raise_for_status()
            return report.text

        raise RuntimeError(f"Turn report for base {base_id} not found (turns list did not contain it).")

