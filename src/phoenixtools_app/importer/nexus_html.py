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

    def list_external_turns(self):
        """Parse Turns → Find → External (`a=turns&sa=find`)."""
        if not self._logged_in:
            self.login()
        from phoenixtools_app.importer.parsers import parse_external_turns_find

        resp = self._client.get(INDEX_URL, params={"a": "turns", "sa": "find"})
        resp.raise_for_status()
        return parse_external_turns_find(resp.text)

    def resolve_turn_token(self, base_id: int):
        """
        Resolve a fetch token for a position via Turns Find (`la=find&id=`).
        Works for external affiliation turns not on the personal list.
        """
        if not self._logged_in:
            self.login()
        from phoenixtools_app.importer.parsers import parse_turn_list

        resp = self._client.get(
            INDEX_URL,
            params={"a": "turns", "sa": "list", "la": "find", "id": str(int(base_id))},
        )
        resp.raise_for_status()
        entry = next((e for e in parse_turn_list(resp.text) if e.pos_id == int(base_id)), None)
        if entry is None or not entry.token:
            raise RuntimeError(f"Could not resolve turn token for position {base_id}.")
        return entry

    def get_turn_report(self, token: str) -> str:
        """Fetch a turn report by its turns-list token (modern Nexus: `?a=tf&c=turn&t=<token>`)."""
        if not self._logged_in:
            self.login()
        resp = self._client.get(INDEX_URL, params={"a": "tf", "c": "turn", "t": str(token)})
        resp.raise_for_status()
        return resp.text

    def get_turn_html(self, base_id: int) -> str:
        """
        Fetch the turn report HTML for a position id.
        Checks the personal turns list first, then falls back to Turns Find (external).
        """
        entry = next((e for e in self.list_turns() if e.pos_id == int(base_id)), None)
        if entry is None or not entry.token:
            entry = self.resolve_turn_token(int(base_id))
        return self.get_turn_report(entry.token)

