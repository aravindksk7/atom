"""Build SAP BO prompt answers, converting date-only DateTime prompts from a
local calendar date to the UTC instant BO expects.

The real BO web UI answers a report's date prompt with local-midnight of the
picked day expressed in UTC (e.g. picking 2026-06-02 on a UTC+1 server sends
"2026-06-01T23:00:00.000Z"). This mirrors that using ZoneInfo, so the result
follows whatever the configured app timezone resolves to.

DST note: ZoneInfo is DST-aware. A summer date under a DST zone (Europe/Paris
-> +2) yields "...22:00Z", while the observed server used a fixed +1 (no DST)
giving "...23:00Z". To match a fixed-offset server, set the app timezone to a
fixed zone such as "Etc/GMT-1"; the builder stays faithful to ZoneInfo.
"""
from __future__ import annotations

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC = ZoneInfo("UTC")


def build_parameter_answers(answers: list[dict], tz: str) -> list[dict]:
    """Return prompt answers with each `value` finalized for the BO PUT body.

    `answers`: list of {"id": int, "type": str, "value": str}. For a DateTime
    prompt whose value is a bare ISO date (YYYY-MM-DD), convert local midnight
    in `tz` to a UTC "...000Z" string. Everything else passes through verbatim.
    """
    zone = ZoneInfo(tz)
    built: list[dict] = []
    for answer in answers:
        value = answer["value"]
        if answer.get("type") == "DateTime" and _DATE_ONLY.match(str(value)):
            local_midnight = datetime.combine(
                datetime.strptime(value, "%Y-%m-%d").date(),
                time(0, 0),
                tzinfo=zone,
            )
            value = local_midnight.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        built.append({"id": answer["id"], "type": answer["type"], "value": value})
    return built
