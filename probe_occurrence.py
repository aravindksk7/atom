"""Probe the SAP BO occurrence flow on-premises. Standalone: needs only `requests`.

Answers the question blocking the blank-export fix: **why the same PUT refreshes
for the browser and not for the ETL client**, and which resource then carries
the data.

Evidence so far. The 2026-08-05 browser trace (SAPBO_1.txt, document 124313)
and the 2026-08-05 11:53 ETL log send the same body to the same occurrence
path with the same query params and get back the same success message --
differing in one field:

    browser:  allDataprovidersRefreshed "true"    (occurrence then reports rowCount 18159)
    client:   allDataprovidersRefreshed "false"   (export: 5784 bytes, 17 layout rows)

So the export is not where this breaks; the refresh never runs. Two candidate
causes survive, and this probe separates them in one run:

  1. Base path. Every Raylight call in the trace goes through the portal's
     proxy -- /BOE/portal/<build>/biprwsproxy/biprws/raylight/v1 -- while the
     client calls /biprws/raylight/v1 directly. The proxy is the path that
     carries a WebI viewing session, i.e. a live document instance for the
     refresh to act on.
  2. Headers. The trace sends X-Client-Type: wise and friends on every call;
     the client sends only X-SAP-LogonToken.

The sequence below runs against the direct base first and the proxied base
second -- in that order, so the direct result cannot be contaminated by a
refresh the proxied run performed. Trace headers are sent on both, so a direct
run that comes back "true" acquits the proxy and convicts the headers.

Within each base it also settles which resource serves a refreshed document,
since the trace stops at the viewer's keep-alive and contains no export at all
-- no request in it carries an xlsx Accept header. Four candidates are pulled
and their row counts printed:

    occurrences/0?reportIds=N   the client's current path
    occurrences/0/reports/N     the occurrence's own report resource
    documents/{id}              SAP's whole-document export (saved copy)
    documents/{id}/reports/N    the path that produced layout-only workbooks

Between refresh and export it runs the trace's own intermediate steps --
snapshot, occurrence state, and the getReportPageOutput render where the trace
spends 6.4s -- so an export that only works *after* a render is
distinguishable from one that never carries data.

Note this writes to the document twice (once per base). That is deliberate:
one trip to a server this probe may only reach once is worth more than halving
its write traffic.

Usage:
  python probe_occurrence.py --url http://qbox111:8080 --user USER --password PW
  # optional: --auth secEnterprise --doc 124313 --report 1
  #           --date 2026-05-08T00:00:00.000Z --code ASX --insecure
  #           --portal-path /BOE/portal/2308301709/biprwsproxy   (from the trace)
  #           --direct-only | --proxy-only
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
# `X-Client-Type: wise` is the one with teeth -- it identifies the caller as a
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
    p.add_argument("--portal-path", default="/BOE/portal/2308301709/biprwsproxy",
                   help="portal proxy prefix from the trace; the build number "
                        "changes per patch level, so correct it from a fresh "
                        "browser URL if the proxied run 404s")
    p.add_argument("--direct-only", action="store_true")
    p.add_argument("--proxy-only", action="store_true")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--insecure", action="store_true", help="skip TLS verification")
    return p.parse_args()


def cache_buster() -> int:
    """The `c=` param every trace URL carries -- defeats a path-keyed gateway cache."""
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


def refreshed_flag(response: requests.Response) -> str | None:
    """`allDataprovidersRefreshed` out of a success body, or None.

    Read off the raw text rather than the parsed JSON: this is the one field
    the whole probe turns on, and a body that does not parse (an HTML error
    page from the proxy, say) should still surrender the flag if it carries it.
    """
    match = re.search(
        r'allDataprovidersRefreshed"\s*,\s*"\$"\s*:\s*"(\w+)"',
        response.text or "",
    )
    return match.group(1) if match else None


def show(label: str, response: requests.Response, *, limit: int = 900) -> int | None:
    """Print one exchange. Returns the xlsx row count when the body is a workbook.

    The row count is returned rather than only printed so the export candidates
    can be summarised at the end: with four of them per base in play, "which
    one had rows" is the probe's actual output and should not have to be
    reconstructed by eye from eight blocks of preview text.
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


def run_sequence(session: requests.Session, api: str, tag: str,
                 args: argparse.Namespace, kw: dict) -> dict:
    """The trace's flow against one API base. Returns what the summary needs."""
    print(f"\n{'=' * 72}\n== BASE [{tag}] {api}\n{'=' * 72}\n")

    doc = f"{api}/documents/{args.doc}"
    json_headers = {"Accept": "application/json"}
    put_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    put_params = {"dataproviderScope": "accessible", "lovInfo": "false",
                  "prepare": "false"}
    answer_body = {"parameters": {"parameter": [
        {"id": 0, "answer": {"values": {"value": [
            {"$": args.date, "@type": "DateTime"}]}}},
        {"id": 1, "answer": {"values": {"value": [
            {"@type": "String", "$": args.code}]}}},
    ]}}
    result: dict = {"tag": tag, "api": api, "refreshed": None, "exports": []}

    # 1. How the server lists the prompts, and what it currently holds as the
    #    answer. "The value did not change, so nothing needed refreshing" is a
    #    live explanation for refreshed=false, and only this listing rules it
    #    in or out.
    show(f"{tag} params-listing (pre-answer)", session.get(
        f"{doc}/parameters", params={"c": cache_buster()},
        headers=json_headers, **kw))

    # 2. The trace's refresh. `allDataprovidersRefreshed` here is the one piece
    #    of positive evidence in the whole flow, and the field this probe
    #    exists to compare across bases.
    #
    #    Both spellings are tried because BO has shipped the misspelled
    #    "occurences" in some releases and the double-r "occurrences" in the
    #    captured trace. Settling that here costs one request and saves a second
    #    trip to a server this probe may only reach once.
    #
    #    If occurrence 0 only exists once the document is opened, opening it and
    #    retrying separates "needs an open" from "portal-only".
    spelling = None
    for candidate in ("occurrences", "occurences"):
        occ_put = session.put(f"{doc}/{candidate}/0/parameters",
                              params={**put_params, "c": cache_buster()},
                              json=answer_body, headers=put_headers, **kw)
        show(f"{tag} PUT {candidate}/0/parameters", occ_put)
        if occ_put.status_code == 404:
            show(f"{tag} GET document (open)", session.get(
                doc, params={"c": cache_buster()}, headers=json_headers, **kw))
            occ_put = session.put(f"{doc}/{candidate}/0/parameters",
                                  params={**put_params, "c": cache_buster()},
                                  json=answer_body, headers=put_headers, **kw)
            show(f"{tag} PUT {candidate}/0/parameters (after open)", occ_put)
        if occ_put.status_code < 400:
            spelling = candidate
            result["refreshed"] = refreshed_flag(occ_put)
            break
    if spelling is None:
        print(f"!! [{tag}] Neither spelling accepted the refresh PUT. The "
              "remaining steps still run against 'occurrences' so their errors "
              "are on record, but treat their output as diagnostic only.\n")
        spelling = "occurrences"
    else:
        print(f"== [{tag}] occurrence spelling={spelling} "
              f"allDataprovidersRefreshed={result['refreshed']!r}\n")
    result["spelling"] = spelling

    occ = f"{doc}/{spelling}/0"

    # 3. The snapshot step the ETL client skips entirely.
    show(f"{tag} POST {spelling}/0/snapshots", session.post(
        occ + "/snapshots", params={"c": cache_buster()},
        headers=json_headers, **kw))

    # 4. Occurrence state. The trace reports "Modified" here -- an in-session
    #    working copy. A stateless client seeing anything else is the difference
    #    that matters.
    show(f"{tag} GET {spelling}/0?allInfo=true", session.get(
        occ, params={"allInfo": "true", "c": cache_buster()},
        headers=json_headers, **kw))

    # 5. The render. The refresh PUT fills the microcube; this is where the
    #    trace spends 6.4s actually computing the report blocks. Run before the
    #    exports so a "only exports after a render" server is distinguishable
    #    from one that never exports data at all -- the exports below can then
    #    be re-run without this step to confirm.
    show(f"{tag} POST {spelling}/0/reports/{args.report}/pages/1 (render)",
         session.post(
             f"{occ}/reports/{args.report}/pages/1",
             params={"getReportPageOutput": "", "c": cache_buster()},
             json={"export": {
                 "mode": "normal", "show": "normal", "chartOutputFormat": "vbo",
                 "incremental": True, "stylePrefix": "V1R1P1P0",
                 "baseUrl": api,
             }},
             headers={"Accept": "multipart/mixed",
                      "Content-Type": "application/json"},
             **kw), limit=1500)

    # 6. Did the data providers actually run? Distinguishes "not refreshed" from
    #    "refreshed, genuinely zero rows for that date".
    #
    #    Occurrence-scoped **and** document-scoped, because they are different
    #    resources and the client currently probes only the document one: the
    #    trace's rowCount:18159 came from the occurrence, so a document-scoped
    #    probe cannot answer "did rows land on the thing we are about to
    #    export?".
    show(f"{tag} dataproviders ({spelling}/0 -- where the trace's rowCount lives)",
         session.get(f"{occ}/dataproviders",
                     params={"c": cache_buster()}, headers=json_headers, **kw))
    show(f"{tag} dataproviders (document-scoped -- what the client probes today)",
         session.get(f"{doc}/dataproviders",
                     params={"c": cache_buster()}, headers=json_headers, **kw))

    # 7. Every export candidate, same session, same refreshed state. The trace
    #    does not contain the export, so none of these is privileged: the row
    #    counts decide.
    for name, url, params in [
        (f"{spelling}/0?reportIds={args.report} (client's current path)",
         occ, {"dpi": 96, "optimized": "true", "reportIds": args.report}),
        (f"{spelling}/0/reports/{args.report}",
         f"{occ}/reports/{args.report}", {"dpi": 96, "optimized": "true"}),
        ("documents/{id} (SAP whole-document export)",
         doc, {"dpi": 96, "optimized": "true"}),
        (f"documents/{{id}}/reports/{args.report} (pre-2026-08-04 path)",
         f"{doc}/reports/{args.report}", {}),
    ]:
        rows = show(f"{tag} GET {name}", session.get(
            url, params={**params, "c": cache_buster()},
            headers={"Accept": XLSX}, **kw))
        result["exports"].append((name, rows))
    return result


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

    # Direct first, proxied second. The reverse order would let a refresh the
    # proxied run performed show up in the direct run's export and acquit the
    # very path under suspicion.
    bases = []
    if not args.proxy_only:
        bases.append(("direct", f"{base}/biprws/raylight/v1"))
    if not args.direct_only:
        portal = args.portal_path.strip("/")
        bases.append(("proxy", f"{base}/{portal}/biprws/raylight/v1"))

    results = []
    try:
        for tag, api in bases:
            results.append(run_sequence(session, api, tag, args, kw))
    finally:
        session.post(f"{base}/biprws/logoff",
                     headers={"Accept": "application/json"}, **kw)

    print(f"\n{'=' * 72}\n== SUMMARY\n{'=' * 72}")
    for r in results:
        print(f"\n[{r['tag']}] {r['api']}")
        print(f"    allDataprovidersRefreshed = {r['refreshed']!r}"
              f"   (occurrence spelling: {r.get('spelling')})")
        print("    export candidates by row count (a WebI sheet carries "
              "title/filter/header rows even when empty,")
        print("    so compare across bases, not against zero):")
        for name, rows in r["exports"]:
            print(f"        {'unreadable' if rows is None else rows:>10}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
