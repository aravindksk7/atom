from __future__ import annotations

import io
from urllib.parse import urlparse

import pandas as pd
import requests

from etl_framework.config.models import ApiEndpointEntry
from etl_framework.exceptions import APIRequestError


class APIEndpointClient:
    def __init__(self, entry: ApiEndpointEntry) -> None:
        self._entry = entry
        self._session = requests.Session()

    def fetch_dataframe(self, max_pages: int | None = None) -> pd.DataFrame:
        entry = self._entry
        page_cap = max_pages if max_pages is not None else entry.pagination_max_pages
        frames: list[pd.DataFrame] = []
        query_params = dict(entry.query_params)
        url = entry.base_url
        page_number = 1

        for _ in range(page_cap):
            if entry.pagination_type == "page":
                query_params[entry.pagination_page_param] = page_number
                query_params[entry.pagination_size_param] = entry.pagination_page_size

            response = self._request(url, query_params)
            frame = self._parse_response(response)
            frames.append(frame)

            if entry.pagination_type == "none":
                break
            if entry.pagination_type == "page":
                if len(frame) < entry.pagination_page_size:
                    break
                page_number += 1
                continue
            if entry.pagination_type == "cursor":
                cursor_value = self._extract_cursor(response)
                if not cursor_value:
                    break
                if urlparse(cursor_value).scheme:
                    url = cursor_value
                    query_params = {}
                else:
                    query_params[entry.pagination_cursor_param] = cursor_value

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

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
            body = response.text[:1000] if response.text else ""
            raise APIRequestError(url=url, http_status=response.status_code, message=body)
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

    def _extract_cursor(self, response: requests.Response) -> str | None:
        entry = self._entry
        if not entry.pagination_cursor_path or entry.response_format == "csv":
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        current = payload
        for part in entry.pagination_cursor_path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return str(current) if current else None

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
