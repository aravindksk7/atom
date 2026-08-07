"""Probe the SAP BO answer PUT on-premises. Standalone: needs only `requests`.

Isolates why the same PUT reports `allDataprovidersRefreshed:"true"` for a
throwaway script and `"false"` for the ETL client against the same server, the
same document and the same occurrence.

What the 2026-08-07 run already settled, and this no longer re-tests by
default:

  * The portal proxy is irrelevant. /biprws/raylight/v1 and
    /BOE/portal/<build>/biprwsproxy/biprws/raylight/v1 both refreshed.
  * The export resource is irrelevant. occurrences/0?reportIds=N,
    occurrences/0/reports/N, documents/{id} and documents/{id}/reports/N all
    returned the same 901280 bytes and 18175 rows. The client's docstrings
    claiming …/reports/{id} exports layout with no data rows are wrong.
  * Occurrence 0 is reachable from a stateless REST client: state "Modified",
    snapshot created, render served, rowCount 18159.

So the export is sound and the refresh is the whole bug. Four differences
remain between the script that refreshes and the client that does not:

  1. headers      -- the script sends the viewer's X-Client-Type: wise and
                     friends; the client sends only X-SAP-LogonToken
  2. answer value -- the script changed the stored date; the client may have
                     re-sent the value already stored, which BO can call
                     "successfully updated" with nothing to re-run
  3. document open-- the client GETs …/documents/{id} first (the document is
                     refreshOnOpen:true); the script does not
  4. cache buster -- the script puts c=<ms> on every URL; the client does not

The matrix below flips one at a time, alternating the answered date so every
step that is supposed to change the value really does, and reads the data
providers after each PUT. `updated` and `rowCount` there are the measurement
that matters: a refresh flag is a claim, a moved `updated` timestamp is the
providers having actually run.

A final step answers the document-level parameters resource instead of the
occurrence, settling the client's other undertested docstring claim.

Usage:
  python probe_occurrence.py --url http://qbox111:8080 --user USER --password PW
  # optional: --auth secEnterprise --doc 124313 --report 1 --insecure
  #           --date 2026-05-08T00:00:00.000Z --date-b 2026-05-07T00:00:00.000Z
  #           --full-sequence   re-run the settled export comparison as well
  #           --portal-path /BOE/portal/2308301709/biprwsproxy
"""
from __future__ import annotations

import argparse
import io
import re
import time
import zipfile

import requests

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JSON_HEADERS = {"Accept": "application/json"}
PUT_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
PUT_PARAMS = {"dataproviderScope": "accessible", "lovInfo": "false",
              "prepare": "false"}

# Sent by the Fiori BI viewer on every Raylight call in the 2026-08-05 trace,
# and by the script that refreshed successfully on 2026-08-07.
# `X-Client-Type: wise` is the one with teeth -- it identifies the caller as a
# WebI interactive-viewing client.
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
    p.add_argument("--date-b", default="2026-05-07T00:00:00.000Z",
                   help="a second date, alternated with --date so each step "
                        "that is meant to change the answer really changes it")
    p.add_argument("--code", default="ASX", help="String prompt answer")
    p.add_argument("--full-sequence", action="store_true",
                   help="also re-run the settled snapshot/render/export "
                        "comparison across both API bases")
    p.add_argument("--portal-path", default="/BOE/portal/2308301709/biprwsproxy",
                   help="portal proxy prefix, used only by --full-sequence; the "
                        "build number changes per patch level")
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
    page from a gateway, say) should still surrender the flag if it carries it.
    """
    match = re.search(
        r'allDataprovidersRefreshed"\s*,\s*"\$"\s*:\s*"(\w+)"',
        response.text or "",
    )
    return match.group(1) if match else None


def stored_answers(response: requests.Response) -> dict:
    """{prompt id: currently stored answer} off a params listing.

    Which value is already stored decides whether a given PUT is a change at
    all, and "the answer did not change, so nothing needed re-running" is one
    of the explanations under test. Reading it costs nothing -- the listing is
    already being fetched.

    Parsed rather than pattern-matched because `answer` carries three
    candidates and only one is the current answer: `answer.info.values` is the
    saved default (2003-12-05 on document 124313), `answer.info.previous` is
    the prior response, and `answer.values` -- read here -- is what the
    document holds now. A regex for the first "values" would report the
    default and quietly invert every conclusion about whether a PUT changed
    anything.
    """
    try:
        params = response.json()["parameters"]["parameter"]
    except Exception:  # noqa: BLE001 - a listing we cannot read is not fatal
        return {}
    if isinstance(params, dict):        # BO collapses a 1-item collection
        params = [params]
    stored = {}
    for param in params:
        values = ((param.get("answer") or {}).get("values") or {}).get("value") or []
        if isinstance(values, dict):
            values = [values]
        stored[param.get("id")] = values[0].get("$") if values else None
    return stored


def show(label: str, response: requests.Response, *, limit: int = 900) -> int | None:
    """Print one exchange. Returns the xlsx row count when the body is a workbook."""
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


def dataprovider_state(session: requests.Session, occ: str,
                       kw: dict) -> tuple[str, object]:
    """(`updated`, `rowCount`) of the occurrence's first data provider.

    The refresh flag is the server's claim; this is the measurement. A PUT that
    reports "true" without moving `updated` did not run the providers, and one
    that reports "false" while moving it did.
    """
    try:
        response = session.get(f"{occ}/dataproviders",
                               params={"c": cache_buster()},
                               headers=JSON_HEADERS, **kw)
        providers = response.json()["dataproviders"]["dataprovider"]
        first = providers[0] if isinstance(providers, list) else providers
        return str(first.get("updated", "")), first.get("rowCount")
    except Exception as exc:  # noqa: BLE001 - measurement must not end the run
        return f"(unreadable: {exc})", None


def answer_put(session: requests.Session, doc: str, occ: str, args: argparse.Namespace,
               kw: dict, *, label: str, date: str, headers_on: bool,
               cache_bust: bool, open_first: bool, document_level: bool) -> dict:
    """One answer PUT with a single variable flipped. Returns a summary row."""
    if open_first:
        # The client's own pre-answer open. The document is refreshOnOpen:true,
        # so this is not obviously inert.
        show(f"{label} GET document (open)", session.get(
            doc, params={"c": cache_buster()}, headers=JSON_HEADERS, **kw))

    body = {"parameters": {"parameter": [
        {"id": 0, "answer": {"values": {"value": [
            {"$": date, "@type": "DateTime"}]}}},
        {"id": 1, "answer": {"values": {"value": [
            {"@type": "String", "$": args.code}]}}},
    ]}}
    # requests deletes a session header when a per-request value is None, which
    # is how a step drops the trace headers without disturbing the others.
    headers = dict(PUT_HEADERS)
    if not headers_on:
        headers.update({k: None for k in TRACE_HEADERS})
    params = dict(PUT_PARAMS)
    if cache_bust:
        params["c"] = cache_buster()

    url = f"{doc}/parameters" if document_level else f"{occ}/parameters"
    started = time.time()
    response = session.put(url, params=params, json=body, headers=headers, **kw)
    elapsed = time.time() - started
    show(f"{label} PUT {'documents/{id}' if document_level else 'occurrences/0'}"
         f"/parameters (date={date} headers={headers_on} c={cache_bust} "
         f"open={open_first})", response)

    updated, row_count = dataprovider_state(session, occ, kw)
    print(f"    -> refreshed={refreshed_flag(response)!r} "
          f"dp.updated={updated} rowCount={row_count} took={elapsed:.1f}s\n")
    return {
        "label": label, "date": date, "headers": headers_on,
        "cache_bust": cache_bust, "open_first": open_first,
        "document_level": document_level, "status": response.status_code,
        "refreshed": refreshed_flag(response), "updated": updated,
        "rows": row_count, "secs": round(elapsed, 1),
    }


def run_matrix(session: requests.Session, api: str, args: argparse.Namespace,
               kw: dict) -> list[dict]:
    """Flip one variable per PUT and report what each did to the providers."""
    doc = f"{api}/documents/{args.doc}"
    occ = f"{doc}/occurrences/0"
    a, b = args.date, args.date_b

    listing = session.get(f"{doc}/parameters", params={"c": cache_buster()},
                          headers=JSON_HEADERS, **kw)
    show("params-listing (pre-answer)", listing)
    print(f"== stored answers before any PUT: {stored_answers(listing)}\n")

    # Steps are ordered so that every one meant to change the answer does. The
    # prime exists only to guarantee that of step 1; its own result is not
    # evidence about anything, since what it changed from is unknown.
    plan = [
        # label, date, headers, c, open, document-level
        ("prime (setup, not evidence)",        b, True,  True,  False, False),
        ("1 baseline",                         a, True,  True,  False, False),
        ("2 SAME value again",                 a, True,  True,  False, False),
        ("3 no trace headers",                 b, False, True,  False, False),
        ("4 no c= cache buster",               a, True,  False, False, False),
        ("5 document opened first",            b, True,  True,  True,  False),
        ("6 client-exact",                     a, False, False, True,  False),
        ("7 document-level resource",          b, True,  True,  False, True),
    ]
    return [
        answer_put(session, doc, occ, args, kw, label=label, date=date,
                   headers_on=headers, cache_bust=bust, open_first=opened,
                   document_level=doc_level)
        for label, date, headers, bust, opened, doc_level in plan
    ]


def run_render_check(session: requests.Session, api: str, args: argparse.Namespace,
                     kw: dict) -> list[tuple[str, int | None]]:
    """Does the export need the report rendered first?

    Nothing on record answers this. The browser renders before it exports
    (SAPBO_10_bold.har spends 6.4s in getReportPageOutput, then downloads), the
    client never renders, and every probe run so far happened to render before
    exporting too. So "refresh ran but the export is still blank" is a failure
    mode that could survive fixing the refresh, and it would look identical.

    Answer it directly: refresh, export before any render, render, export
    again. Equal row counts mean the render is the viewer's business and the
    client can keep skipping it.
    """
    print(f"\n{'=' * 72}\n== RENDER CHECK\n{'=' * 72}\n")
    doc = f"{api}/documents/{args.doc}"
    occ = f"{doc}/occurrences/0"
    export_params = {"dpi": 96, "optimized": "true", "reportIds": args.report}

    # A known-good refresh first: the matrix leaves the document in whatever
    # state its last step produced, and this must start from a refreshed one.
    answer_put(session, doc, occ, args, kw, label="render-check refresh",
               date=args.date, headers_on=True, cache_bust=True,
               open_first=False, document_level=False)

    before = show("export BEFORE any render", session.get(
        occ, params={**export_params, "c": cache_buster()},
        headers={"Accept": XLSX}, **kw))
    show("render (getReportPageOutput)", session.post(
        f"{occ}/reports/{args.report}/pages/1",
        params={"getReportPageOutput": "", "c": cache_buster()},
        json={"export": {"mode": "normal", "show": "normal",
                         "chartOutputFormat": "vbo", "incremental": True,
                         "stylePrefix": "V1R1P1P0", "baseUrl": api}},
        headers={"Accept": "multipart/mixed", "Content-Type": "application/json"},
        **kw), limit=400)
    after = show("export AFTER the render", session.get(
        occ, params={**export_params, "c": cache_buster()},
        headers={"Accept": XLSX}, **kw))
    return [("export before render", before), ("export after render", after)]


def run_full_sequence(session: requests.Session, api: str, tag: str,
                      args: argparse.Namespace, kw: dict) -> None:
    """The settled snapshot/render/export comparison, kept for regression."""
    print(f"\n{'=' * 72}\n== FULL SEQUENCE [{tag}] {api}\n{'=' * 72}\n")
    doc = f"{api}/documents/{args.doc}"
    occ = f"{doc}/occurrences/0"

    show(f"{tag} POST occurrences/0/snapshots", session.post(
        occ + "/snapshots", params={"c": cache_buster()}, headers=JSON_HEADERS, **kw))
    show(f"{tag} GET occurrences/0?allInfo=true", session.get(
        occ, params={"allInfo": "true", "c": cache_buster()},
        headers=JSON_HEADERS, **kw))
    show(f"{tag} POST occurrences/0/reports/{args.report}/pages/1 (render)",
         session.post(
             f"{occ}/reports/{args.report}/pages/1",
             params={"getReportPageOutput": "", "c": cache_buster()},
             json={"export": {
                 "mode": "normal", "show": "normal", "chartOutputFormat": "vbo",
                 "incremental": True, "stylePrefix": "V1R1P1P0", "baseUrl": api,
             }},
             headers={"Accept": "multipart/mixed",
                      "Content-Type": "application/json"}, **kw), limit=1500)
    for name, url, params in [
        (f"occurrences/0?reportIds={args.report}", occ,
         {"dpi": 96, "optimized": "true", "reportIds": args.report}),
        (f"occurrences/0/reports/{args.report}", f"{occ}/reports/{args.report}",
         {"dpi": 96, "optimized": "true"}),
        ("documents/{id}", doc, {"dpi": 96, "optimized": "true"}),
        (f"documents/{{id}}/reports/{args.report}",
         f"{doc}/reports/{args.report}", {}),
    ]:
        show(f"{tag} GET {name}", session.get(
            url, params={**params, "c": cache_buster()},
            headers={"Accept": XLSX}, **kw))


def main() -> int:
    args = parse_args()
    base = args.url.rstrip("/")
    session = requests.Session()
    session.headers.update(TRACE_HEADERS)
    kw = {"timeout": args.timeout, "verify": not args.insecure}

    logon = session.post(
        f"{base}/biprws/logon/long",
        json={"password": args.password, "clientType": "",
              "auth": args.auth, "userName": args.user},
        headers=PUT_HEADERS, **kw,
    )
    token = logon.headers.get("X-SAP-LogonToken")
    print(f"logon HTTP {logon.status_code} token={'yes' if token else 'NO'}")
    if not token:
        print(logon.text[:900])
        return 1
    session.headers.update({"X-SAP-LogonToken": token})

    api = f"{base}/biprws/raylight/v1"
    try:
        rows = run_matrix(session, api, args, kw)
        render = run_render_check(session, api, args, kw)
        if args.full_sequence:
            portal = args.portal_path.strip("/")
            run_full_sequence(session, api, "direct", args, kw)
            run_full_sequence(session, f"{base}/{portal}/biprws/raylight/v1",
                              "proxy", args, kw)
    finally:
        session.post(f"{base}/biprws/logoff", headers=JSON_HEADERS, **kw)

    print(f"\n{'=' * 78}\n== ANSWER-PUT MATRIX\n{'=' * 78}")
    print(f"{'step':<28} {'hdrs':<5} {'c':<5} {'open':<5} {'refreshed':<10} "
          f"{'rows':<7} {'secs':<5} dp.updated")
    for r in rows:
        print(f"{r['label']:<28} {str(r['headers']):<5} {str(r['cache_bust']):<5} "
              f"{str(r['open_first']):<5} {str(r['refreshed']):<10} "
              f"{str(r['rows']):<7} {r['secs']:<5} {r['updated']}")
    print("\nRead it as: a step whose dp.updated did NOT move past the previous "
          "row's did not run the data providers, whatever its refresh flag "
          "says. Step 2 falsifies 'an unchanged answer still refreshes'; steps "
          "3-6 each convict or acquit one client-vs-script difference.")

    print(f"\n{'=' * 78}\n== RENDER CHECK\n{'=' * 78}")
    for label, count in render:
        print(f"    {'unreadable' if count is None else count:>10}  {label}")
    print("Equal row counts mean the export does not need the render, and the "
          "client can keep skipping it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
