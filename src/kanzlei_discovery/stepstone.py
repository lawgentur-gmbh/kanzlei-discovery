from __future__ import annotations

import csv
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests


BASE_URL = "https://www.stepstone.de"
STATE_MARKER = 'window.__PRELOADED_STATE__["app-unifiedResultlist"]'
EXPORT_COLUMNS = [
    "Job_Titel",
    "Titel_url",
    "Name_des_Unternehmens",
    "Standort",
    "Home_möglich",
    "Job_Beschreibung",
    "Erscheinen",
    "Image_url",
    "Stepstone_ID",
    "Harmonised_ID",
    "Search_Query",
    "Scrape_Date",
]
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class SearchSpec:
    name: str
    url: str
    required_filters: tuple[tuple[str, str], ...] = ()


@dataclass
class SearchResult:
    rows: list[dict[str, str]]
    raw_rows: int
    pages: int
    duplicates: int
    inventory_complete: bool = False
    coverage_warnings: list[str] = field(default_factory=list)


def pagination_metadata(results: dict[str, Any]) -> tuple[int, int, int | None, dict]:
    """Read both published layouts; missing metadata is never a one-page list."""
    legacy = results.get("pagination") or {}
    unified = results.get("unifiedPagination") or {}
    complete = [p for p in (legacy, unified) if isinstance(p, dict)
                and p.get("page") is not None and p.get("pageCount") is not None]
    if not complete:
        raise RuntimeError("Stepstone pagination metadata missing; completeness unknown")
    pairs = {(int(p["page"]), int(p["pageCount"])) for p in complete}
    if len(pairs) != 1:
        raise RuntimeError("Stepstone pagination fields conflict")
    page, count = pairs.pop()
    total = legacy.get("totalCount", (results.get("meta") or {}).get("total"))
    total = int(total) if total is not None else None
    if page == 1 and count == 0 and total == 0:
        count = 1
    if page < 1 or count < page or count > 10000 or total is not None and total < 0:
        raise RuntimeError("Stepstone pagination range invalid")
    return page, count, total, unified or legacy


class StepstoneClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        delay_seconds: float = 0.5,
        timeout_seconds: int = 90,
        retries: int = 4,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.delay_seconds = max(0.0, delay_seconds)
        self.timeout_seconds = timeout_seconds
        self.retries = max(1, retries)

    def fetch_search(
        self,
        search: SearchSpec,
        scrape_date: str,
        *,
        max_pages: int | None = None,
    ) -> SearchResult:
        rows_by_id: OrderedDict[str, dict[str, str]] = OrderedDict()
        raw_rows = 0
        page_number = 1
        next_url = search.url
        expected_pages: int | None = None
        seen_page_urls: set[str] = set()
        observed_counts: set[int] = set()
        observed_totals: set[int] = set()
        page_id_sets: set[tuple[str, ...]] = set()
        warnings: list[str] = []
        visited_pages = 0
        invalid_items = 0
        maximum_pages = 0

        while next_url:
            if next_url in seen_page_urls:
                raise RuntimeError(f"{search.name}: Stepstone repeated page URL {next_url}")
            seen_page_urls.add(next_url)
            state = self._get_state(next_url)
            results = state.get("searchResults") or {}
            active_filters = (results.get("activeFilters") or {}).get("items") or []
            for filter_type, filter_id in search.required_filters:
                if not any(f.get("type") == filter_type and str(f.get("id")) == filter_id for f in active_filters if isinstance(f, dict)):
                    raise RuntimeError(f"{search.name}: required filter {filter_type}={filter_id} not active on page {page_number}")
            items = results.get("items") or []
            if not isinstance(items, list):
                raise RuntimeError(f"{search.name}: Stepstone returned an invalid item list")

            current_page, expected_pages, total_count, pagination = pagination_metadata(results)
            if current_page != page_number:
                raise RuntimeError(f"{search.name}: requested page {page_number}, received {current_page}")
            observed_counts.add(expected_pages)
            maximum_pages = max(maximum_pages, expected_pages)
            if total_count is not None:
                observed_totals.add(total_count)
            if not items and (total_count != 0 or expected_pages > 1):
                raise RuntimeError(
                    f"{search.name}: Stepstone returned no items on page {current_page}"
                )
            visited_pages += 1
            raw_rows += len(items)
            page_ids = []
            for item in items:
                if not isinstance(item, dict):
                    invalid_items += 1
                    continue
                row = item_to_export_row(item, search.name, scrape_date)
                if not row:
                    invalid_items += 1
                    continue
                identity = item_identity(item, row)
                page_ids.append(identity)
                existing = rows_by_id.get(identity)
                if existing:
                    add_search_name(existing, search.name)
                else:
                    rows_by_id[identity] = row
            signature = tuple(sorted(page_ids))
            if signature and signature in page_id_sets:
                raise RuntimeError(f"{search.name}: repeated result page under a different URL")
            page_id_sets.add(signature)

            print(
                f"STEPSTONE_PAGE query={safe_console(search.name)} "
                f"page={current_page}/{expected_pages} items={len(items)}"
            )
            if max_pages and current_page >= max_pages:
                if current_page < maximum_pages:
                    warnings.append("explicit_page_limit")
                break
            if current_page >= maximum_pages:
                break

            links = pagination.get("links") or {}
            next_link = str(links.get("next") or "").strip()
            # Search totals can shrink while pages are being read. Follow the
            # highest observed range using the site's own published template.
            if not next_link and len(observed_counts) > 1 and current_page < maximum_pages:
                template = str(pagination.get("template") or "")
                if "{page}" in template:
                    next_link = template.replace("{page}", str(current_page + 1))
            if not next_link:
                raise RuntimeError(
                    f"{search.name}: missing next-page link at {current_page}/{maximum_pages}"
                )
            next_url = urljoin(BASE_URL, next_link)
            parsed = urlsplit(next_url)
            if parsed.scheme != "https" or parsed.hostname != "www.stepstone.de" or not parsed.path.startswith("/jobs/") or parsed.username or parsed.password:
                raise RuntimeError(f"{search.name}: invalid next-page origin/path")
            page_number = current_page + 1
            if self.delay_seconds:
                time.sleep(self.delay_seconds)

        rows = list(rows_by_id.values())
        if len(observed_counts) > 1 or len(observed_totals) > 1:
            warnings.append("inventory_changed_during_pagination")
        if invalid_items:
            warnings.append("invalid_result_items")
        if len(observed_totals) == 1 and len(rows) != next(iter(observed_totals)):
            warnings.append("unique_inventory_differs_from_declared_total")
        return SearchResult(
            rows=rows,
            raw_rows=raw_rows,
            pages=visited_pages,
            duplicates=max(raw_rows - len(rows), 0),
            inventory_complete=not warnings,
            coverage_warnings=warnings,
        )

    def _get_state(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout_seconds)
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        f"temporary HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()
                if "text/html" not in response.headers.get("content-type", "").casefold():
                    raise RuntimeError(
                        f"unexpected Stepstone content type: {response.headers.get('content-type', '')}"
                    )
                return parse_result_state(response.text)
            except (requests.RequestException, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(f"Stepstone request failed after {self.retries} attempts: {url}") from last_error


def load_searches(path: Path) -> list[SearchSpec]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected a JSON list")
    searches: list[SearchSpec] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: search entry {index} is not an object")
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url.startswith("https://www.stepstone.de/jobs/"):
            raise ValueError(f"{path}: invalid Stepstone search entry {index}")
        filters = item.get("required_filters") or {}
        if not isinstance(filters, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in filters.items()):
            raise ValueError(f"{path}: invalid required filters at entry {index}")
        searches.append(SearchSpec(name=name, url=url, required_filters=tuple(sorted(filters.items()))))
    if not searches:
        raise ValueError(f"{path}: no Stepstone searches configured")
    return searches


def parse_result_state(html: str) -> dict[str, Any]:
    marker_index = html.find(STATE_MARKER)
    if marker_index < 0:
        raise RuntimeError("Stepstone result state was not found in the response")
    assignment_index = html.find("=", marker_index + len(STATE_MARKER))
    script_end = html.find("</script>", assignment_index)
    if assignment_index < 0 or script_end < 0:
        raise RuntimeError("Stepstone result state is incomplete")
    payload = html[assignment_index + 1 : script_end].strip().removesuffix(";").strip()
    try:
        state = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Stepstone result state is not valid JSON") from exc
    if not isinstance(state, dict) or not isinstance(state.get("searchResults"), dict):
        raise RuntimeError("Stepstone result state has an unexpected structure")
    return state


def item_to_export_row(
    item: dict[str, Any], search_name: str, scrape_date: str
) -> dict[str, str] | None:
    title = clean(item.get("title"))
    company = clean(item.get("companyName"))
    link = canonical_job_url(clean(item.get("url")))
    # A missing employer remains a research candidate (title + city search),
    # never a reason to silently lose a real source posting or invent a name.
    if not title or not link:
        return None
    return {
        "Job_Titel": title,
        "Titel_url": link,
        "Name_des_Unternehmens": company,
        "Standort": clean(item.get("location")),
        "Home_möglich": clean(item.get("workFromHome")),
        "Job_Beschreibung": clean(item.get("textSnippet")),
        "Erscheinen": clean(
            item.get("datePosted")
            or item.get("publishFromDate")
            or item.get("periodPostedDate")
        ),
        "Image_url": clean(item.get("companyLogoUrl")),
        "Stepstone_ID": clean(item.get("id")),
        "Harmonised_ID": clean(item.get("harmonisedId")),
        "Search_Query": search_name,
        "Scrape_Date": scrape_date,
    }


def canonical_job_url(value: str) -> str:
    if not value:
        return ""
    absolute = urljoin(BASE_URL, value)
    parts = urlsplit(absolute)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def item_identity(item: dict[str, Any], row: dict[str, str]) -> str:
    return clean(item.get("id") or item.get("harmonisedId")) or row["Titel_url"].casefold()


def add_search_name(row: dict[str, str], search_name: str) -> None:
    names = [part.strip() for part in row.get("Search_Query", "").split("|") if part.strip()]
    if search_name not in names:
        names.append(search_name)
        row["Search_Query"] = " | ".join(names)


def search_fingerprint(searches: Iterable[SearchSpec]) -> str:
    payload = [{"name": item.name, "url": item.url, "required_filters": item.required_filters} for item in searches]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_resumable_checkpoint(
    path: Path, scrape_date: str, fingerprint: str
) -> tuple[int, OrderedDict[str, dict[str, str]], dict[str, int]]:
    if not path.exists():
        return 0, OrderedDict(), empty_stats()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, OrderedDict(), empty_stats()
    if (
        payload.get("date") != scrape_date
        or payload.get("pagination_version") != 2
        or payload.get("search_fingerprint") != fingerprint
        or payload.get("completed")
    ):
        return 0, OrderedDict(), empty_stats()
    rows = OrderedDict()
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        key = row.get("Stepstone_ID") or row.get("Harmonised_ID") or row.get("Titel_url")
        if key:
            rows[str(key)] = {column: clean(row.get(column)) for column in EXPORT_COLUMNS}
    stats = empty_stats()
    for key in stats:
        stats[key] = int((payload.get("stats") or {}).get(key) or 0)
    return int(payload.get("next_query_index") or 0), rows, stats


def save_checkpoint(
    path: Path,
    *,
    scrape_date: str,
    fingerprint: str,
    next_query_index: int,
    rows: Iterable[dict[str, str]],
    stats: dict[str, int],
    completed: bool,
) -> None:
    payload: dict[str, Any] = {
        "pagination_version": 2,
        "date": scrape_date,
        "search_fingerprint": fingerprint,
        "next_query_index": next_query_index,
        "completed": completed,
        "stats": stats,
    }
    if not completed:
        payload["rows"] = list(rows)
    write_json_atomic(path, payload)


def merge_search_rows(
    target: OrderedDict[str, dict[str, str]], incoming: Iterable[dict[str, str]]
) -> int:
    duplicates = 0
    for row in incoming:
        key = row.get("Stepstone_ID") or row.get("Harmonised_ID") or row["Titel_url"]
        existing = target.get(key)
        if existing:
            duplicates += 1
            for name in row.get("Search_Query", "").split("|"):
                if name.strip():
                    add_search_name(existing, name.strip())
        else:
            target[key] = row
    return duplicates


def write_export_atomic(path: Path, rows: Iterable[dict[str, str]]) -> None:
    rows = list(rows)
    if not rows:
        raise RuntimeError("Refusing to write an empty Stepstone export")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def empty_stats() -> dict[str, int]:
    return {"queries": 0, "pages": 0, "raw_rows": 0, "duplicates": 0, "partial_queries": 0}


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def safe_console(value: str) -> str:
    return value.encode("ascii", errors="backslashreplace").decode("ascii")
