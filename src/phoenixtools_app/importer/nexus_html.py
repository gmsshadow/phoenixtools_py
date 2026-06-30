from __future__ import annotations

from dataclasses import dataclass

import httpx


NEXUS_DOMAIN = "phoenixbse.com"
# Must be https: the site 301-redirects http, and clients drop POST bodies on
# redirect, which silently breaks the login form submission.
INDEX_URL = f"https://{NEXUS_DOMAIN}/index.php"


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

    def list_turns(self):
        """
        Parse the logged-in turns list (`a=turns&sa=list`) into TurnListEntry rows.
        This includes both your own turns and turns shared with you by other players.
        """
        if not self._logged_in:
            self.login()
        from phoenixtools_app.importer.parsers import parse_turn_list

        resp = self._client.get(INDEX_URL, params={"a": "turns", "sa": "list"})
        resp.raise_for_status()
        return parse_turn_list(resp.text)

    def get_turn_report(self, token: str) -> str:
        """Fetch a turn report by its turns-list token (modern Nexus: `?a=tf&c=turn&t=<token>`)."""
        if not self._logged_in:
            self.login()
        resp = self._client.get(INDEX_URL, params={"a": "tf", "c": "turn", "t": str(token)})
        resp.raise_for_status()
        return resp.text

    def get_turn_html(self, base_id: int) -> str:
        """
        Fetch the turn report HTML for a position id by looking it up in the turns list
        (works for both owned and shared turns).
        """
        entry = next((e for e in self.list_turns() if e.pos_id == int(base_id)), None)
        if entry is None:
            raise RuntimeError(
                f"Turn for position {base_id} not found in the Nexus turns list. "
                "Add it to your turns (or have it shared with you) on the Nexus first."
            )
        return self.get_turn_report(entry.token)

