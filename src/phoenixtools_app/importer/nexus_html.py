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

        # Be forgiving: Rails looks under `td.turns_tab_off` and matches "(id)" in link text,
        # but Nexus markup varies (sometimes ID appears without parentheses, or only in onclick/href).
        anchors = doc.xpath("//a")
        target = None
        needle = str(base_id)
        for a in anchors:
            text = (a.text_content() or "").strip()
            onclick = (a.get("onclick") or "").strip()
            href = (a.get("href") or "").strip()
            if (
                f"({needle})" in text
                or needle in text
                or needle in onclick
                or needle in href
            ):
                target = a
                break

        if target is None:
            raise RuntimeError(
                f"Turn report for base {base_id} not found. "
                "The turns list page did not contain a link matching that ID. "
                "This can happen if the base isn't visible to the logged-in user yet."
            )

        onclick = (target.get("onclick") or "").strip()
        href = (target.get("href") or "").strip()

        url_path = ""
        if "/index.php" in onclick:
            # Example: window.open('/index.php?a=...','_blank',...)
            start = onclick.find("/index.php")
            # end at next quote/parens/comma
            end_candidates = [onclick.find('"', start), onclick.find("'", start), onclick.find(")", start), onclick.find(",", start)]
            end_candidates = [e for e in end_candidates if e != -1]
            end = min(end_candidates) if end_candidates else len(onclick)
            url_path = onclick[start:end]
        elif href.startswith("/index.php"):
            url_path = href
        elif href.startswith("index.php"):
            url_path = "/" + href

        if not url_path:
            raise RuntimeError(
                f"Turn report link for base {base_id} was found, but could not extract the popup URL "
                "(missing onclick/href)."
            )

        if not url_path.startswith("/"):
            url_path = "/" + url_path

        report = self._client.get(f"http://{NEXUS_DOMAIN}{url_path}")
        report.raise_for_status()
        return report.text

