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

UNRESOLVED (2026-07-30): a second captured trace from the same deployment
contradicts the first. Picking 2026-05-08 there sent
"2026-05-08T00:00:00.000Z" — plain UTC midnight, i.e. *no* offset applied —
whereas the first trace (2026-06-02 -> "2026-06-01T23:00:00.000Z") implies +1.
Both dates fall inside the same DST period, so DST does not explain the gap;
the likeliest cause is that the two captures were taken from browsers in
different timezones. Until that is settled, the app timezone setting decides:
"UTC" reproduces the second trace exactly, "Etc/GMT-1" reproduces the first.
Behaviour is deliberately left driven by configuration rather than re-guessed
from one sample.
"""
from __future__ import annotations

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC = ZoneInfo("UTC")

# BO reports a prompt's data type in the parameters *listing* using a different
# vocabulary — and a different case — than it accepts in the answer PUT's
# `@type`: the listing calls a string prompt "Text" on one deployment and
# lowercase "string" on another, while every captured 200-OK answer PUT uses
# "String". Keyed lowercase so case alone can never leak an unmapped type
# through. Only trace-proven targets go here; anything unknown passes through
# unchanged (verbatim, not case-folded).
_ANSWER_TYPE_ALIASES = {"text": "String", "string": "String", "datetime": "DateTime"}


def _answer_type(listing_type) -> str | None:
    """Map a listing's prompt type onto the vocabulary the answer PUT accepts."""
    if not isinstance(listing_type, str):
        return listing_type
    return _ANSWER_TYPE_ALIASES.get(listing_type.lower(), listing_type)


def build_parameter_answers(answers: list[dict], tz: str) -> list[dict]:
    """Return prompt answers with each `value` finalized for the BO PUT body.

    `answers`: list of {"id": int, "type": str, "value": str}. For a DateTime
    prompt whose value is a bare ISO date (YYYY-MM-DD), format as UTC midnight
    ("YYYY-MM-DDT00:00:00.000Z") so the selected calendar date is preserved
    regardless of regional timezone settings. Everything else passes through verbatim.
    `type` is also mapped from the listing's vocabulary to the one the answer
    PUT accepts (see _ANSWER_TYPE_ALIASES).
    """
    built: list[dict] = []
    for answer in answers:
        value = answer["value"]
        # Normalise first: the date conversion below keys off the type, so a
        # listing that says lowercase 'datetime' would otherwise ship a raw
        # YYYY-MM-DD to BO.
        ptype = _answer_type(answer.get("type"))
        if ptype == "DateTime" and _DATE_ONLY.match(str(value)):
            value = f"{value}T00:00:00.000Z"
        built.append({
            "id": answer["id"],
            "type": ptype,
            "value": value,
        })
    return built
