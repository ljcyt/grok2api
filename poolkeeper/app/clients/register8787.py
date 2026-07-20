from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests


class Register8787Client:
    """Orchestrate build_register web console (local 8787 or cftun public)."""

    def __init__(self, base_url: str, web_token: str, timeout: float = 30.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.web_token = web_token
        self.timeout = timeout
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _headers(self) -> Dict[str, str]:
        return {"X-Web-Token": self.web_token}

    def health(self) -> bool:
        try:
            resp = self.session.get(
                self._url("/api/health"),
                headers=self._headers(),
                timeout=min(5.0, self.timeout),
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def status(self) -> Dict[str, Any]:
        resp = self.session.get(
            self._url("/api/status"),
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def start_register(
        self,
        count: int,
        *,
        threads: int = 3,
        email: str = "rotate",
        no_oauth: bool = False,
    ) -> Dict[str, Any]:
        body = {
            "count": int(count),
            "threads": int(threads),
            "email": email,
            "no_oauth": bool(no_oauth),
        }
        resp = self.session.post(
            self._url("/api/jobs/register"),
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"register failed {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def start_auth_probe(self, *, concurrency: int = 4) -> Dict[str, Any]:
        resp = self.session.post(
            self._url("/api/jobs/auth-probe"),
            headers=self._headers(),
            json={"concurrency": concurrency},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"auth-probe failed {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def start_g2a_push(
        self,
        *,
        concurrency: int = 4,
        probe_first: bool = True,
        use_last_probe: bool = True,
    ) -> Dict[str, Any]:
        body = {
            "concurrency": concurrency,
            "probe_first": probe_first,
            "use_last_probe": use_last_probe,
        }
        resp = self.session.post(
            self._url("/api/jobs/auth-g2a-push"),
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"auth-g2a-push failed {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def job(self, job_id: str) -> Dict[str, Any]:
        resp = self.session.get(
            self._url(f"/api/jobs/{job_id}"),
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def stop_job(self, job_id: str) -> Dict[str, Any]:
        resp = self.session.post(
            self._url(f"/api/jobs/{job_id}/stop"),
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"stop job failed {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def active_register(self) -> Optional[Dict[str, Any]]:
        st = self.status()
        reg = st.get("register") or {}
        if reg.get("active"):
            return reg
        return None
