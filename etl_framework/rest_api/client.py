from __future__ import annotations

import io
import logging

import pandas as pd
import requests

from etl_framework.config.models import ApiEndpointEntry
from etl_framework.exceptions import APIRequestError

logger = logging.getLogger("etl_framework.rest_api.client")


class APIEndpointClient:
    def __init__(self, entry: ApiEndpointEntry) -> None:
        self._entry = entry
        self._session = requests.Session()

    def fetch_dataframe(self, max_pages: int | None = None) -> pd.DataFrame:
        entry = self._entry
        response = self._request(entry.base_url, dict(entry.query_params))
        return self._parse_response(response)

    def _auth_kwargs(self) -> dict:
        entry = self._entry
        headers = dict(entry.headers)
        auth = None
        if entry.auth_type == "api_key":
            headers[entry.api_key_header] = entry.api_key
        elif entry.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {entry.bearer_token}"
        elif entry.auth_type == "basic":
            auth = (entry.basic_username, entry.basic_password)
        return {"headers": headers, "auth": auth}

    def _request(self, url: str, query_params: dict) -> requests.Response:
        entry = self._entry
        kwargs = self._auth_kwargs()
        try:
            response = self._session.request(
                entry.method,
                url,
                params=query_params,
                json=entry.body if entry.method == "POST" else None,
                timeout=entry.timeout,
                verify=entry.verify_ssl,
                **kwargs,
            )
        except requests.exceptions.RequestException as exc:
            raise APIRequestError(url=url, http_status=None, message=str(exc)) from exc
        if response.status_code >= 400:
            raise APIRequestError(url=url, http_status=response.status_code, message=response.text)
        return response

    def _parse_response(self, response: requests.Response) -> pd.DataFrame:
        entry = self._entry
        if entry.response_format == "csv":
            try:
                return pd.read_csv(io.StringIO(response.text))
            except Exception as exc:
                raise APIRequestError(
                    url=response.url, http_status=response.status_code,
                    message=f"Cannot parse API response as csv: {exc}",
                ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIRequestError(
                url=response.url, http_status=response.status_code,
                message="Cannot parse API response as json",
            ) from exc
        records = self._walk_json_path(payload, entry.json_root_path, response.url)
        if not isinstance(records, list):
            raise APIRequestError(
                url=response.url, http_status=response.status_code,
                message=f"json_root_path '{entry.json_root_path}' did not resolve to a list of records",
            )
        return pd.json_normalize(records) if records else pd.DataFrame()

    @staticmethod
    def _walk_json_path(payload, path: str, url: str):
        if not path:
            return payload
        current = payload
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise APIRequestError(
                    url=url, http_status=None,
                    message=f"json_root_path '{path}' did not resolve to a list of records",
                )
        return current
