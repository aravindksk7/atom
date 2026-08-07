"""Probe the SAP BO occurrence flow on-premises. Standalone: needs only `requests`.

Answers the question blocking the blank-export fix: **which resource carries a
refreshed document's data**, and what a stateless REST client has to do to get
there.

The 2026-08-05 browser trace (SAPBO_1.txt, document 124313) proves the refresh
half — `PUT …/occurrences/0/parameters` returns
`allDataprovidersRefreshed:"true"`, and the occurrence then reports
`rowCount:18159`. It does **not** contain the export: the capture stops at the
viewer's keep-alive, so no request in it carries an xlsx `Accept` header. Every
claim about the export URL is therefore unverified, and this probe settles it by
pulling all four candidates in one session and printing the row count of each:

    occurrences/0?reportIds=N   the client's current path
    occurrences/0/reports/N     the occurrence's own report resource
    documents/{id}              SAP's whole-document export (saved copy)
    documents/{id}/reports/N    the path that produced layout-only workbooks

Between refresh and export it runs the trace's own intermediate steps —
snapshot, occurrence state, and the `getReportPageOutput` render that is where
the trace spends 6.4 s — so an export that only works *after* the report has
been rendered is distinguishable from one that never works.

Requests carry the headers the trace sends on every Raylight call
(`X-Client-Type: wise` and friends) and the `c=` cache buster it puts on every
URL; this deployment sits behind a gateway already caught re-serving cached
GETs, and a probe that skipped either would be testing a different client than
the one whose behaviour we are trying to reproduce.

Usage:
  python probe_occurrence.py --url http://qbox111:8080 --user USER --password PW
  # optional: --auth secEnterprise --doc 124313 --report 1
  #           --date 2026-05-08T00:00:00.000Z --code ASX --insecure
"""
from __future__ import annotations

import argparse
import io
import re
import time
import zipfile

import requests

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Sent by the Fiori BI viewer on every Raylight call in the 2026-08-05 trace.
# `X-Client-Type: wise` is the one with teeth — it identifies the caller as a
# WebI interactive-viewing client, which is the session kind that owns the
# modifiable occurrence 0 this whole flow depends on.
TRACE_HEADERS = {
    "X-Client-Type": "wise",
    "X-SAP-PVL": "en_US",
    "Accept-Language": "en_US",
    "X-Requested-With": "XMLHttpRequest",
    "Cache-Control": "no-cache",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="e.g. http://qbox111:8080")
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--auth", default="secEnterprise")
    p.add_argument("--doc", default="124313")
    p.add_argument("--report", default="1")
    p.add_argument("--date", default="2026-05-08T00:00:00.000Z",
                   help="DateTime prompt answer, exactly as the browser sent it")
    p.add_argument("--code", default="ASX", help="String prompt answer")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--insecure", action="store_true", help="skip TLS verification")
    return p.parse_args()


def cache_buster() -> int:
    """The `c=` param every trace URL carries — defeats a path-keyed gateway cache."""
    return int(time.time() * 1000)


def xlsx_rows_and_preview(data: bytes) -> tuple[int | None, str]:
    """Row count and first cell strings of an xlsx, without openpyxl."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8", "replace")
            rows = len(re.findall(r"<row[ >]", sheet))
            try:
                shared = zf.read("xl/sharedStrings.xml").decode("utf-8", "replace")
                cells = re.findall(r"<t[^>]*>(.*?)</t>", shared, re.S)
            except KeyError:
                cells = re.findall(r"<t[^>]*>(.*?)</t>", sheet, re.S)
            return rows, " | ".join(c.strip() for c in cells[:40])
    except Exception as exc:  # noqa: BLE001
        return None, f"(unreadable as xlsx: {exc})"


def show(label: str, response: requests.Response, *, limit: int = 900) -> int | None:
    """Print one exchange. Returns the xlsx row count when the body is a workbook.

    The row count is returned rather than only printed so the export candidates
    can be summarised at the end: with four of them in play, "which one had
    rows" is the probe's actual output and should not have to be reconstructed
    by eye from four blocks of preview text.
    """
    body = response.content or b""
    ctype = (response.headers or {}).get("Content-Type", "")
    print(f"[{label}] HTTP {response.status_code} ct={ctype} bytes={len(body)}")
    if body[:2] == b"PK":
        rows, preview = xlsx_rows_and_preview(body)
        print(f"    rows={rows}")
        print(f"    preview: {preview[:700]}")
        print()
        return rows
    print(f"    body: {body[:limit].decode('utf-8', 'replace')}")
    print()
    return None


def main() -> int:
    args = parse_args()
    base = args.url.rstrip("/")
    verify = not args.insecure
    session = requests.Session()
    session.headers.update(TRACE_HEADERS)
    kw = {"timeout": args.timeout, "verify": verify}

    logon = session.post(
        f"{base}/biprws/logon/long",
        json={"password": args.password, "clientType": "",
              "auth": args.auth, "userName": args.user},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        **kw,
    )
    token = logon.headers.get("X-SAP-LogonToken")
    print(f"logon HTTP {logon.status_code} token={'yes' if token else 'NO'}")
    if not token:
        print(logon.text[:900])
        return 1
    session.headers.update({"X-SAP-LogonToken": token})

    doc = f"{base}/biprws/raylight/v1/documents/{args.doc}"
    json_headers = {"Accept": "application/json"}
    answer_body = {"parameters": {"parameter": [
        {"id": 0, "answer": {"values": {"value": [
            {"$": args.date, "@type": "DateTime"}]}}},
        {"id": 1, "answer": {"values": {"value": [
            {"@type": "String", "$": args.code}]}}},
    ]}}
    put_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    put_params = {"dataproviderScope": "accessible", "lovInfo": "false",
                  "prepare": "false"}

    try:
        # 1. How the server lists the prompts (ids and answer types).
        show("params-listing", session.get(
            f"{doc}/parameters", params={"c": cache_buster()},
            headers=json_headers, **kw))

        # 2. The trace's refresh. `allDataprovidersRefreshed` here is the one
        #    piece of positive evidence in the whole flow.
        #
        #    Both spellings are tried because BO has shipped the misspelled
        #    "occurences" in some releases and the double-r "occurrences" in the
        #    captured trace. Settling that here costs one request and saves a
        #    second trip to a server this probe may only reach once.
        #
        #    If occurrence 0 only exists once the document is opened, opening it
        #    and retrying separates "needs an open" from "portal-only".
        spelling = None
        for candidate in ("occurrences", "occurences"):
            occ_put = session.put(f"{doc}/{candidate}/0/parameters",
                                  params={**put_params, "c": cache_buster()},
                                  json=answer_body, headers=put_headers, **kw)
            show(f"PUT {candidate}/0/parameters", occ_put)
            if occ_put.status_code == 404:
                show("GET document (open)", session.get(
                    doc, params={"c": cache_buster()}, headers=json_headers, **kw))
                occ_put = session.put(f"{doc}/{candidate}/0/parameters",
                                      params={**put_params, "c": cache_buster()},
                                      json=answer_body, headers=put_headers, **kw)
                show(f"PUT {candidate}/0/parameters (after open)", occ_put)
            if occ_put.status_code < 400:
                spelling = candidate
                break
        if spelling is None:
            print("!! Neither spelling accepted the refresh PUT. The remaining "
                  "steps still run against 'occurrences' so their errors are "
                  "on record, but treat their output as diagnostic only.\n")
            spelling = "occurrences"
        else:
            print(f"== occurrence path spelling in use: {spelling}\n")

        occ = f"{doc}/{spelling}/0"

        # 3. The snapshot step the ETL client skips entirely.
        show(f"POST {spelling}/0/snapshots", session.post(
            occ + "/snapshots",
            params={"c": cache_buster()},
            headers=json_headers, **kw))

        # 4. Occurrence state. The trace reports "Modified" here — an in-session
        #    working copy. A stateless client seeing anything else is the
        #    difference that matters.
        show(f"GET {spelling}/0?allInfo=true", session.get(
            occ, params={"allInfo": "true", "c": cache_buster()},
            headers=json_headers, **kw))

        # 5. The render. The refresh PUT fills the microcube; this is where the
        #    trace spends 6.4s actually computing the report blocks. Run before
        #    the exports so a "only exports after a render" server is
        #    distinguishable from one that never exports data at all — the
        #    exports below can then be re-run without this step to confirm.
        show(f"POST {spelling}/0/reports/{args.report}/pages/1 (render)", session.post(
            f"{occ}/reports/{args.report}/pages/1",
            params={"getReportPageOutput": "", "c": cache_buster()},
            json={"export": {
                "mode": "normal", "show": "normal", "chartOutputFormat": "vbo",
                "incremental": True, "stylePrefix": "V1R1P1P0",
                "baseUrl": f"{base}/biprws/raylight/v1",
            }},
            headers={"Accept": "multipart/mixed", "Content-Type": "application/json"},
            **kw), limit=1500)

        # 6. Did the data providers actually run? Distinguishes "not refreshed"
        #    from "refreshed, genuinely zero rows for that date".
        #
        #    Occurrence-scoped **and** document-scoped, because they are
        #    different resources and the client currently probes only the
        #    document one: the trace's rowCount:18159 came from the occurrence,
        #    so a document-scoped probe cannot answer "did rows land on the
        #    thing we are about to export?".
        show(f"dataproviders ({spelling}/0 — where the trace's rowCount lives)",
             session.get(f"{occ}/dataproviders",
                         params={"c": cache_buster()}, headers=json_headers, **kw))
        show("dataproviders (document-scoped — what the client probes today)",
             session.get(f"{doc}/dataproviders",
                         params={"c": cache_buster()}, headers=json_headers, **kw))

        # 7. Every export candidate, same session, same refreshed state. The
        #    trace does not contain the export, so none of these is privileged:
        #    the row counts decide.
        candidates = [
            (f"GET {spelling}/0?reportIds={args.report} (client's current path)",
             occ, {"dpi": 96, "optimized": "true", "reportIds": args.report}),
            (f"GET {spelling}/0/reports/{args.report}",
             f"{occ}/reports/{args.report}", {"dpi": 96, "optimized": "true"}),
            ("GET documents/{id} (SAP whole-document export)",
             doc, {"dpi": 96, "optimized": "true"}),
            (f"GET documents/{{id}}/reports/{args.report} (pre-2026-08-04 path)",
             f"{doc}/reports/{args.report}", {}),
        ]
        results: list[tuple[str, int | None]] = []
        for label, url, params in candidates:
            rows = show(label, session.get(
                url, params={**params, "c": cache_buster()},
                headers={"Accept": XLSX}, **kw))
            results.append((label, rows))

        print("== export candidates by row count "
              "(a WebI sheet carries title/filter/header rows even when empty, "
              "so compare against each other, not against zero)")
        for label, rows in results:
            print(f"    {'unreadable' if rows is None else rows:>10}  {label}")
    finally:
        session.post(f"{base}/biprws/logoff", headers=json_headers, **kw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
