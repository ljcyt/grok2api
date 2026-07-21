from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests


class Grok2APIClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._token = ""
        self._token_at = 0.0
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def login(self, *, force: bool = False) -> str:
        if (
            not force
            and self._token
            and (time.time() - self._token_at) < 10 * 60
        ):
            return self._token
        resp = self.session.post(
            self._url("/api/admin/v1/auth/login"),
            json={"username": self.username, "password": self.password},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        tokens = (data or payload).get("tokens") if isinstance(data or payload, dict) else {}
        token = str((tokens or {}).get("accessToken") or "").strip()
        if not token:
            raise RuntimeError("admin login missing accessToken")
        self._token = token
        self._token_at = time.time()
        return token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.login()}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Any = None,
    ) -> Any:
        for attempt in range(2):
            resp = self.session.request(
                method,
                self._url(path),
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
            if resp.status_code == 401 and attempt == 0:
                self.login(force=True)
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
            if not resp.content:
                return {}
            return resp.json()
        return {}

    def health(self) -> bool:
        try:
            resp = self.session.get(self._url("/healthz"), timeout=min(5.0, self.timeout))
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def summary(self) -> Dict[str, Any]:
        payload = self._request("GET", "/api/admin/v1/accounts/summary")
        return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload

    def list_accounts(
        self,
        *,
        provider: str = "grok_build",
        status: str = "",
        page_size: int = 100,
        max_pages: int = 100,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            params: Dict[str, Any] = {
                "page": page,
                "pageSize": page_size,
                "provider": provider,
            }
            if status:
                params["status"] = status
            payload = self._request("GET", "/api/admin/v1/accounts", params=params)
            data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
            batch = []
            if isinstance(data, dict):
                batch = data.get("items") or data.get("accounts") or data.get("list") or []
                total = int(data.get("total") or 0)
            elif isinstance(data, list):
                batch = data
                total = 0
            else:
                break
            if not batch:
                break
            items.extend(batch)
            if total and len(items) >= total:
                break
            if len(batch) < page_size:
                break
            page += 1
        return items

    def probe_build(
        self,
        account_ids: List[str],
        *,
        model: str = "grok-4.5",
        timeout_seconds: int = 20,
        concurrency: int = 5,
    ) -> List[Dict[str, Any]]:
        if not account_ids:
            return []
        # HTTP client timeout must cover worst-case batch wall time.
        conc = max(1, int(concurrency or 1))
        batch_timeout = max(
            self.timeout,
            float(timeout_seconds) * ((len(account_ids) + conc - 1) // conc) + 30.0,
        )
        old_timeout = self.timeout
        self.timeout = batch_timeout
        try:
            payload = self._request(
                "POST",
                "/api/admin/v1/accounts/build/probe",
                json_body={
                    "account_ids": account_ids,
                    "mode": "chat",
                    "model": model,
                    "timeout_seconds": timeout_seconds,
                    "concurrency": concurrency,
                },
            )
        finally:
            self.timeout = old_timeout
        data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        if isinstance(data, dict):
            return list(data.get("results") or [])
        return []

    def set_enabled(self, account_id: str, enabled: bool, provider: str = "grok_build") -> Any:
        return self._request(
            "PATCH",
            f"/api/admin/v1/accounts/{account_id}",
            json_body={"enabled": enabled},
        )

    def set_priority(self, account_id: str, priority: int) -> Any:
        """Patch priority only (G2A UpdateInput uses optional pointer fields)."""
        return self._request(
            "PATCH",
            f"/api/admin/v1/accounts/{account_id}",
            json_body={"priority": int(priority)},
        )

    def batch_set_enabled(
        self, account_ids: List[str], enabled: bool, provider: str = "grok_build"
    ) -> Any:
        return self._request(
            "PATCH",
            "/api/admin/v1/accounts/batch",
            json_body={"ids": account_ids, "provider": provider, "enabled": enabled},
        )

    def delete_account(self, account_id: str) -> Any:
        return self._request("DELETE", f"/api/admin/v1/accounts/{account_id}")

    def batch_delete(self, account_ids: List[str], provider: str = "grok_build") -> Any:
        return self._request(
            "DELETE",
            "/api/admin/v1/accounts",
            json_body={"ids": account_ids, "provider": provider},
        )
