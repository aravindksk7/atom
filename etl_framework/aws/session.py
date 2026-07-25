from __future__ import annotations

import boto3

from etl_framework.aws.config import AWSConfig


class AWSSession:
    """Thin wrapper over boto3.Session with endpoint_url + client caching.

    Pass ``_session`` to inject a session in tests (mirrors DBEngine(_engine=...)).
    """

    def __init__(self, cfg: AWSConfig, _session: "boto3.Session | None" = None) -> None:
        self._cfg = cfg
        if _session is not None:
            self.session = _session
        else:
            kwargs: dict = {}
            if cfg.profile:
                kwargs["profile_name"] = cfg.profile
            if cfg.region:
                kwargs["region_name"] = cfg.region
            if cfg.access_key_id:
                kwargs["aws_access_key_id"] = cfg.access_key_id
                kwargs["aws_secret_access_key"] = cfg.secret_access_key
                if cfg.session_token:
                    kwargs["aws_session_token"] = cfg.session_token
            self.session = boto3.Session(**kwargs)
        self._clients: dict[str, object] = {}

    def client(self, service: str):
        if service not in self._clients:
            kwargs: dict = {"verify": self._cfg.verify_ssl}
            if self._cfg.endpoint_url:
                kwargs["endpoint_url"] = self._cfg.endpoint_url
            self._clients[service] = self.session.client(service, **kwargs)
        return self._clients[service]
