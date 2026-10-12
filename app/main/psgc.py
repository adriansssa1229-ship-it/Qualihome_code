import json
import time
from urllib.parse import quote
from urllib.request import urlopen


_BASE_URL = "https://psgc.gitlab.io/api"
_TTL_SECONDS = 60 * 60 * 12
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _normalize_items(payload) -> list[dict[str, str]]:
    items = payload if isinstance(payload, list) else []
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue
        out.append({"code": code, "name": name})
    return out


def _fetch_list(path: str) -> list[dict[str, str]]:
    key = path.strip()
    now = time.time()
    cached = _cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    url = f"{_BASE_URL}{key}"
    with urlopen(url, timeout=12) as response:
        body = response.read().decode("utf-8", errors="ignore")
        payload = json.loads(body)

    items = _normalize_items(payload)
    _cache[key] = (now + _TTL_SECONDS, items)
    return items


def list_regions() -> list[dict[str, str]]:
    return _fetch_list("/regions/")


def list_provinces(region_code: str) -> list[dict[str, str]]:
    code = quote(str(region_code).strip())
    if not code:
        return []
    # NCR has no provinces in Philippine administrative geography.
    # Return Metro Manila as the single province option so cascading UI works seamlessly.
    if code == "130000000" or code.startswith("13"):
        return [{"code": "130000000", "name": "Metro Manila"}]
    try:
        items = _fetch_list(f"/regions/{code}/provinces/")
    except Exception:
        items = []
    if not items and (code == "130000000" or code.startswith("13")):
        return [{"code": "130000000", "name": "Metro Manila"}]
    return items


def list_cities(province_code: str | None = None, region_code: str | None = None) -> list[dict[str, str]]:
    if province_code:
        code = quote(str(province_code).strip())
        if code:
            # If province_code is NCR / Metro Manila, fetch cities directly from region
            if code == "130000000" or code.startswith("13"):
                return _fetch_list(f"/regions/{code}/cities-municipalities/")
            return _fetch_list(f"/provinces/{code}/cities-municipalities/")
    if region_code:
        code = quote(str(region_code).strip())
        if code:
            return _fetch_list(f"/regions/{code}/cities-municipalities/")
    return []


def list_barangays(city_mun_code: str) -> list[dict[str, str]]:
    code = quote(str(city_mun_code).strip())
    if not code:
        return []
    return _fetch_list(f"/cities-municipalities/{code}/barangays/")
