"""Minimal ThingsBoard CE REST client (JWT login, paging helpers)."""
from __future__ import annotations

import time
from typing import Iterator, Optional

import requests


class TBError(RuntimeError):
    def __init__(self, resp: requests.Response, what: str = ""):
        self.status = resp.status_code
        try:
            msg = resp.json().get("message", resp.text)
        except ValueError:
            msg = resp.text
        super().__init__(f"{what} -> HTTP {resp.status_code}: {msg[:400]}")


class TB:
    def __init__(self, url: str, user: str, password: str, verify=True, timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.verify = verify
        self.timeout = timeout
        self.user, self.password = user, password
        self.login()

    def login(self):
        r = self.s.post(self.url + "/api/auth/login", json={"username": self.user, "password": self.password},
                        timeout=self.timeout)
        if r.status_code != 200:
            raise TBError(r, "login")
        self.token = r.json()["token"]
        self.s.headers["X-Authorization"] = "Bearer " + self.token
        self._login_t = time.time()

    def _req(self, method: str, path: str, ok=(200,), **kw):
        if time.time() - self._login_t > 1800:       # JWT default lifetime is 2.5 h; refresh early
            self.login()
        r = self.s.request(method, self.url + path, timeout=self.timeout, **kw)
        if r.status_code == 401:
            self.login()
            r = self.s.request(method, self.url + path, timeout=self.timeout, **kw)
        if r.status_code not in ok:
            raise TBError(r, f"{method} {path}")
        return r

    def get(self, path, **kw):
        r = self._req("GET", path, **kw)
        return r.json() if r.content else None

    def post(self, path, body=None, ok=(200,), **kw):
        r = self._req("POST", path, ok=ok, json=body, **kw)
        return r.json() if r.content else None

    def delete(self, path, ok=(200,), **kw):
        return self._req("DELETE", path, ok=ok, **kw)

    def pages(self, path: str, params: Optional[dict] = None, page_size: int = 100) -> Iterator[dict]:
        page = 0
        while True:
            p = dict(params or {}, pageSize=page_size, page=page)
            d = self.get(path, params=p)
            for it in d.get("data", []):
                yield it
            if not d.get("hasNext"):
                return
            page += 1

    # -- lookups ---------------------------------------------------------------
    def find_by_name(self, path: str, name: str, field: str = "name") -> Optional[dict]:
        for it in self.pages(path, {"textSearch": name}):
            if it.get(field) == name:
                return it
        return None

    def server_time_offset_ms(self) -> Optional[float]:
        """Rough server-minus-local clock offset from the HTTP Date header (1 s resolution)."""
        from email.utils import parsedate_to_datetime
        t0 = time.time()
        r = self.s.get(self.url + "/api/noauth/oauth2Clients", timeout=self.timeout)
        t1 = time.time()
        d = r.headers.get("Date")
        if not d:
            return None
        return (parsedate_to_datetime(d).timestamp() - (t0 + t1) / 2) * 1000.0
