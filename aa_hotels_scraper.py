# To save/capture updated curls, run:
# & c:/Users/galluzzi-tomas/scraper/.venv-1/Scripts/python.exe c:/Users/galluzzi-tomas/scraper/aa_hotels_scraper.py --capture-curls

# Single city search:
# c:/Users/galluzzi-tomas/scraper/.venv-1/Scripts/python.exe c:/Users/galluzzi-tomas/scraper/aa_hotels_scraper.py "Chicago" --curl-only

import argparse
from datetime import datetime, timedelta
import json
import os
import re
import shlex
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

MIN_PPD_DEFAULT  = 30             # minimum LP per dollar to include in results
START_DATE       = "2026/08/13"   # search window start
#END_DATE         = "2027/02/28"   # search window end
END_DATE         = "2026/12/31"   # search window end

#START_DATE       = "2026/12/01"   # search window start
#END_DATE         = "2026/12/01"   # search window end

MAX_PAGES_PER_SEARCH = 5          # max pages to fetch when pagination is enabled

# =============================================================
# PER-RUN SETTINGS â€” change these before each run
# =============================================================
CONFIG = """
{
    "cities": [
                "Las Vegas",
                "Chicago",
                "Orlando",
                "Tokyo",
                "Miami",
                "Fort Lauderdale",
                "New York",
                "Madrid",
                "Mexico City"
        ],
    "output_json": "aahoteloptimizer_results.json",

    "aa_hotels_curl": ""
}
"""

# =============================================================
# INFRASTRUCTURE SETTINGS â€” only change when adding cities or
# refreshing curl files; not needed per run
# =============================================================
INFRA_CONFIG = """
{
    "target_lp": 200000,
    "mileage_value": 1.5,
    "headless": false,
    "all_pages": true,
    "use_curl_only": false,
    "city_curl_files": {
        "Chicago":     "curls/curl_Chicago.txt",
        "Fort Lauderdale": "curls/curl_FortLauderdale.txt",
        "Las Vegas":   "curls/curl_LasVegas.txt",
        "Madrid":      "curls/curl_Madrid.txt",
        "Mexico City": "curls/curl_MexicoCity.txt",
        "Miami":       "curls/curl_Miami.txt",
        "New York":    "curls/curl_NewYork.txt",
        "Orlando":     "curls/curl_Orlando.txt",
        "Tokyo":       "curls/curl_Tokyo.txt"
    },
    "city_curls": {},
    "city_place_ids": {
        "Chicago":     "AGODA_CITY|13899",
        "Fort Lauderdale": "AGODA_CITY|11579",
        "Las Vegas":   "AGODA_CITY|17072",
        "Madrid":      "AGODA_CITY|5531",
        "Mexico City": "AGODA_CITY|2086",
        "Miami":       "AGODA_CITY|15289",
        "New York":    "AGODA_CITY|318",
        "Orlando":     "AGODA_CITY|10757",
        "Tokyo":       "AGODA_CITY|5085"
    },
    "city_search_queries": {
        "Chicago":     "Chicago (IL), United States",
        "Fort Lauderdale": "Fort Lauderdale (FL), United States",
        "Las Vegas":   "Las Vegas (NV), United States",
        "Madrid":      "Madrid, Spain",
        "Mexico City": "Mexico City, Mexico",
        "Miami":       "Miami (FL), United States",
        "New York":    "New York (NY), United States",
        "Orlando":     "Orlando (FL), United States",
        "Tokyo":       "Tokyo, Japan"
    }
}
"""

def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def read_text_file(path):
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()


def log(message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def build_driver(headless=True):
    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1600,1400")
    # Some local Chrome/ChromeDriver combos reject non-lowercase or enum-like
    # values; force a plain valid W3C strategy string.
    options.set_capability("pageLoadStrategy", "normal")
    if headless:
        options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)
    return driver


def set_input_value(driver, element, value):
    text_value = "" if value is None else str(value)
    try:
        element.click()
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        element.send_keys(text_value)
    except Exception:
        pass

    driver.execute_script(
        """
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', {bubbles: true}));
        arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
        arguments[0].dispatchEvent(new Event('blur', {bubbles: true}));
        """,
        element,
        text_value,
    )


def find_first_visible(driver, selectors):
    for by, query in selectors:
        elements = driver.find_elements(by, query)
        for el in elements:
            try:
                if el.is_displayed():
                    return el
            except Exception:
                continue
    return None


def current_value(element):
    try:
        return element.get_attribute("value") or ""
    except Exception:
        return ""


def parse_curl(curl_cmd):
    # Handle Windows CMD escaped cURL copy/paste form that uses caret escapes.
    curl_cmd = normalize_curl_command(curl_cmd)

    # Chat apps often wrap URLs as markdown links: [text](url). Keep the visible
    # text so pasted cURL remains executable and header values stay human-readable.
    curl_cmd = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", curl_cmd)

    parsed = {"url": None, "headers": {}, "cookies": "", "data": None, "method": "GET"}
    if not curl_cmd:
        return parsed

    parts = shlex.split(curl_cmd)
    for idx, token in enumerate(parts):
        token_clean = token.strip("[]")
        if token in {"--url"} and idx + 1 < len(parts):
            parsed["url"] = parts[idx + 1].strip("[]")
        if token in {"-H", "--header"} and idx + 1 < len(parts):
            header = parts[idx + 1]
            if ":" in header:
                key, value = header.split(":", 1)
                parsed["headers"][key.strip()] = value.strip().strip("[]")
        elif token in {"-b", "--cookie"} and idx + 1 < len(parts):
            parsed["cookies"] = parts[idx + 1]
        elif token in {"--data", "--data-raw"} and idx + 1 < len(parts):
            parsed["data"] = parts[idx + 1]
            parsed["method"] = "POST"
        elif token in {"-X", "--request"} and idx + 1 < len(parts):
            parsed["method"] = parts[idx + 1].upper()
        elif token_clean.startswith("http://") or token_clean.startswith("https://"):
            parsed["url"] = token_clean
    return parsed


def normalize_curl_command(curl_cmd):
    if not curl_cmd:
        return curl_cmd

    text = curl_cmd.replace("\r\n", "\n").replace("\r", "\n")
    # Remove line-continuation carets at line ends first.
    text = re.sub(r"\^\s*\n", " ", text)
    # Unescape CMD carets (e.g. ^", ^&, ^%).
    text = re.sub(r"\^(.)", r"\1", text)
    # Normalize excessive whitespace/newlines to single spaces for parsing.
    text = re.sub(r"\s+", " ", text).strip()
    return text


def request_with_curl(curl_cmd):
    parsed = parse_curl(curl_cmd)
    if not parsed["url"]:
        raise ValueError("No URL found in curl command")

    log(f"Sending {parsed['method']} request to {parsed['url']}")

    headers = dict(parsed["headers"])
    if parsed["cookies"]:
        headers["Cookie"] = parsed["cookies"]
    headers.setdefault("User-Agent", "Mozilla/30.0")
    headers.setdefault("Accept", "application/json, text/plain, */*")

    payload = parsed["data"].encode("utf-8") if parsed["data"] else None
    request = urllib.request.Request(parsed["url"], data=payload, headers=headers, method=parsed["method"])
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=60) as response:
        body = response.read().decode("utf-8", errors="replace")
        log(f"Received response ({len(body)} chars)")
        return body


def money_amount(value):
    if isinstance(value, dict):
        return normalize_number(value.get("amount"))
    return normalize_number(value)


def resolve_search_results_url(base_url, bootstrap_payload):
    if not isinstance(bootstrap_payload, dict):
        return None
    href = ((bootstrap_payload.get("_links") or {}).get("search") or {}).get("href")
    if not href:
        return None
    return urllib.parse.urljoin(base_url, href)


def fetch_search_results_payload(curl_cmd, fetch_all_pages=True):
    parsed = parse_curl(curl_cmd)
    if not parsed["url"]:
        raise ValueError("No URL found in curl command")

    headers = dict(parsed["headers"])
    if parsed["cookies"]:
        headers["Cookie"] = parsed["cookies"]
    headers.setdefault("User-Agent", "Mozilla/30.0")
    headers.setdefault("Accept", "application/json, text/plain, */*")

    payload = parsed["data"].encode("utf-8") if parsed["data"] else None

    bootstrap_req = urllib.request.Request(parsed["url"], data=payload, headers=headers, method=parsed["method"])
    context = ssl.create_default_context()
    with urllib.request.urlopen(bootstrap_req, context=context, timeout=60) as response:
        bootstrap_text = response.read().decode("utf-8", errors="replace")
    # log(f"Received bootstrap response ({len(bootstrap_text)} chars)")
    bootstrap_payload = json.loads(bootstrap_text)

    search_url = resolve_search_results_url(parsed["url"], bootstrap_payload)
    if not search_url:
        return bootstrap_payload

    if not fetch_all_pages:
        first_page_url = f"{search_url}?pageNumber=1"
        search_req = urllib.request.Request(first_page_url, headers=headers, method="GET")
        with urllib.request.urlopen(search_req, context=context, timeout=60) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    all_results = []
    page_payload = {}
    for page in range(1, MAX_PAGES_PER_SEARCH + 1):
        paged_url = f"{search_url}?pageNumber={page}"
        search_req = urllib.request.Request(paged_url, headers=headers, method="GET")
        with urllib.request.urlopen(search_req, context=context, timeout=60) as response:
            page_payload = json.loads(response.read().decode("utf-8", errors="replace"))
        page_results = page_payload.get("results") or []
        if not page_results:
            break
        all_results.extend(page_results)
        total = page_payload.get("totalFilteredHotels") or 0
        if len(all_results) >= total:
            break

    page_payload["results"] = all_results
    return page_payload


def parse_city_hint_from_curl(curl_cmd):
    parsed = parse_curl(curl_cmd)
    if not parsed.get("url"):
        return None
    url_parts = urllib.parse.urlparse(parsed["url"])
    params = urllib.parse.parse_qs(url_parts.query)
    query = (params.get("query") or [None])[0]
    if query:
        return query
    return None


def city_name_matches_curl(curl_cmd, city):
    hint = parse_city_hint_from_curl(curl_cmd)
    if not hint or not city:
        return True
    return city.lower() in hint.lower()


def parse_city_curls(value):
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if v}
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if v}
        except Exception:
            pass
    return {}


def parse_string_map(value):
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if v}
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if v}
        except Exception:
            pass
    return {}


def load_city_curls_from_files(value):
    path_map = parse_string_map(value)
    loaded = {}
    for city, path in path_map.items():
        try:
            loaded[city] = read_text_file(path)
        except Exception as exc:
            log(f"Could not read city cURL file for {city} at {path}: {exc}")
    return loaded


def validate_city_curl_coverage(cities, base_curl, city_curls, city_place_ids):
    issues = []
    for city in cities:
        city_curl = city_curls.get(city)
        place_id = city_place_ids.get(city)

        if city_curl:
            continue

        if not base_curl:
            issues.append(f"{city}: missing cURL (add city_curls/city_curl_files entry or aa_hotels_curl)")
            continue

        if place_id:
            continue

        if not city_name_matches_curl(base_curl, city):
            hint = parse_city_hint_from_curl(base_curl) or "unknown city"
            issues.append(f"{city}: base cURL appears pinned to '{hint}' and no city_place_ids entry exists")

    return issues


def parse_cookie_header(curl_cmd):
    parsed = parse_curl(curl_cmd)
    cookies = parsed.get("cookies") or parsed.get("headers", {}).get("Cookie") or parsed.get("headers", {}).get("cookie")
    if not cookies:
        return {}
    out = {}
    for part in cookies.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def check_session_health(curl_cmd):
    warnings = []
    parsed = parse_curl(curl_cmd)
    headers = parsed.get("headers", {})
    xsrf = headers.get("x-xsrf-token") or headers.get("X-XSRF-TOKEN") or headers.get("x-xsrf-token")
    if not xsrf:
        warnings.append("No x-xsrf-token header found in cURL.")

    cookies = parse_cookie_header(curl_cmd)
    if "JSESSIONID" not in cookies:
        warnings.append("No JSESSIONID cookie found in cURL.")

    cf_clearance = cookies.get("cf_clearance")
    if cf_clearance:
        match = re.search(r"-(\d{10})-", cf_clearance)
        if match:
            expires_epoch = int(match.group(1))
            now_epoch = int(time.time())
            if expires_epoch <= now_epoch:
                warnings.append("cf_clearance appears expired; refresh cURL from a logged-in browser session.")
    return warnings


def to_mmddyyyy(date_text):
    text = (date_text or "").strip()
    if not text:
        return text
    if re.match(r"^\d{2}/\d{2}/\d{4}$", text):
        return text
    match = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})$", text)
    if match:
        year, month, day = match.groups()
        return f"{month}/{day}/{year}"
    return text


def parse_date_input(date_text):
    text = (date_text or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {date_text}")


def format_yyyy_mm_dd(date_obj):
    return date_obj.strftime("%Y/%m/%d")


def build_date_windows(start_date, end_date):
    start = parse_date_input(start_date)
    end = parse_date_input(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")

    windows = []
    current = start
    while current <= end:
        checkout = current + timedelta(days=1)
        windows.append((format_yyyy_mm_dd(current), format_yyyy_mm_dd(checkout)))
        current += timedelta(days=1)
    return windows


def rewrite_curl_for_city(base_curl, city, start_date, end_date, place_id=None, rewrite_query=True):
    parsed = parse_curl(base_curl)
    if not parsed.get("url"):
        return base_curl

    url_parts = urllib.parse.urlparse(parsed["url"])
    query = urllib.parse.parse_qs(url_parts.query, keep_blank_values=True)

    if rewrite_query:
        query["query"] = [city]
    query["checkIn"] = [to_mmddyyyy(start_date)]
    query["checkOut"] = [to_mmddyyyy(end_date)]
    query["sort"] = ["milesHighest"]
    query["searchId"] = [str(uuid.uuid4())]
    if place_id:
        query["placeId"] = [place_id]

    new_query = urllib.parse.urlencode(query, doseq=True)
    new_url = urllib.parse.urlunparse((url_parts.scheme, url_parts.netloc, url_parts.path, url_parts.params, new_query, url_parts.fragment))

    # Replace only the parsed URL instance.
    return base_curl.replace(parsed["url"], new_url, 1)


def first_present(obj, keys):
    if isinstance(obj, dict):
        for key in keys:
            for candidate_key in obj.keys():
                if candidate_key.lower() == key.lower():
                    return obj[candidate_key]
    return None


def normalize_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.-]", "", value)
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def should_exclude_hotel(city, hotel_name):
    if not city or not hotel_name:
        return False
    if city.strip().lower() != "chicago":
        return False
    return "the chicago hotel collection" in hotel_name.strip().lower()


def summarize_response(payload):
    rows = []

    def walk(node):
        if isinstance(node, dict):
            if any(key.lower() in {"hotel", "hotelname", "name", "propertyname", "property_name"} for key in node.keys()):
                row = {}
                row["Hotel Name"] = first_present(node, ["hotelName", "hotel_name", "name", "propertyName", "property_name", "title"])
                row["Total Price ($)"] = normalize_number(first_present(node, ["total_price", "totalPrice", "grandTotalPublishedPriceInclusiveWithFees", "price", "price_usd"]))
                row["Stay LP PPD"] = normalize_number(first_present(node, ["stayLpPpd", "stay_lp_ppd", "ppd", "pointsPerDollar", "points_per_dollar"]))
                row["Total Stay LP"] = normalize_number(first_present(node, ["totalStayLP", "total_stay_lp", "totalLp", "points", "apiPointsEarned"]))
                if row["Hotel Name"] or row["Total Price ($)"] is not None or row["Stay LP PPD"] is not None:
                    rows.append(row)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return rows


def search_city_with_curl(city, start_date, end_date, target_lp, mileage_value, aa_hotels_curl, fetch_all_pages=True):
    if not aa_hotels_curl:
        return []

    try:
        log(f"  {start_date}: {city}")
        payload = fetch_search_results_payload(aa_hotels_curl, fetch_all_pages=fetch_all_pages)
    except urllib.error.HTTPError as exc:
        log(f"cURL request failed: HTTP {exc.code} for {city}. This usually means expired session/cookies or invalid city/place/date pairing.")
        return []
    except Exception as exc:
        log(f"cURL request failed: {exc}")
        return []

    check_in = payload.get("checkInDate") if isinstance(payload, dict) else None
    check_out = payload.get("checkOutDate") if isinstance(payload, dict) else None
    results = payload.get("results") if isinstance(payload, dict) else None

    rows = []
    if isinstance(results, list):
        for item in results:
            hotel = item.get("hotel") if isinstance(item, dict) else None
            if not isinstance(hotel, dict):
                continue

            hotel_name = hotel.get("name")
            if not hotel_name:
                continue
            if should_exclude_hotel(city, hotel_name):
                continue

            address = hotel.get("address") if isinstance(hotel.get("address"), dict) else {}
            hotel_city = address.get("city")

            total_price = money_amount(item.get("grandTotalPublishedPriceInclusiveWithFees"))
            if total_price is None:
                total_price = money_amount(item.get("totalPrice"))

            teaser = item.get("roomTypeResultTeaser") if isinstance(item.get("roomTypeResultTeaser"), dict) else {}

            # The teaser rewards track what users usually see as the effective
            # per-stay earnings in the search UI for the selected context.
            effective_rewards = normalize_number(teaser.get("rewards"))
            if effective_rewards is None:
                effective_rewards = normalize_number(item.get("rewards"))
            if effective_rewards is None:
                effective_rewards = normalize_number(item.get("rewardsBeforeBoost"))

            base_rewards = normalize_number(teaser.get("originalRewards"))
            if base_rewards is None:
                base_rewards = normalize_number(item.get("rewardsBeforeBoost"))
            if base_rewards is None:
                base_rewards = normalize_number(item.get("originalRewards"))

            promo_rewards = normalize_number(teaser.get("promotionRewards"))
            if promo_rewards is None:
                promo_rewards = normalize_number(item.get("promotionRewards"))

            bonus_rewards = normalize_number(item.get("boostedRewards"))
            if bonus_rewards is None and effective_rewards is not None and base_rewards is not None:
                promo_val = promo_rewards or 0.0
                bonus_rewards = max(0.0, effective_rewards - base_rewards - promo_val)

            total_stay_lp = effective_rewards

            stay_lp_ppd = None
            if total_price and total_stay_lp is not None:
                stay_lp_ppd = round(total_stay_lp / total_price, 4)

            row = {
                "City": hotel_city,
                "Hotel Name": hotel_name,
                "Check In": check_in,
                "Check Out": check_out,
                "Total Price ($)": total_price,
                "Total Stay LP": total_stay_lp,
                "Stay LP PPD": stay_lp_ppd,
                "Base Rewards": base_rewards,
            }
            rows.append(row)

    if rows:
        return sort_rows_by_ppd_desc(rows)

    return []


def parse_numeric_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return None
    return float(match.group(0))


def normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_results_table(driver):
    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.XPATH, "//*[contains(normalize-space(.), 'All Hotel Options Found')]")))
    tables = driver.find_elements(By.TAG_NAME, "table")
    for table in tables:
        text = table.text.replace("\n", " ")
        if "Hotel Name" not in text and "Stay LP PPD" not in text and "Base+Card LP PPD" not in text:
            continue

        rows = table.find_elements(By.TAG_NAME, "tr")
        if not rows:
            continue

        header_cells = rows[0].find_elements(By.TAG_NAME, "th") or rows[0].find_elements(By.TAG_NAME, "td")
        if not header_cells:
            continue

        headers = []
        for cell in header_cells:
            label = re.sub(r"\s+", " ", cell.text).strip()
            headers.append(label)

        output = []
        for row in rows[1:]:
            cells = row.find_elements(By.TAG_NAME, "td")
            if not cells:
                continue
            record = {}
            for idx, cell in enumerate(cells):
                label = headers[idx] if idx < len(headers) else f"col_{idx}"
                value = re.sub(r"\s+", " ", cell.text).strip()
                record[label] = value

            if any(key.lower().find("hotel") >= 0 for key in record.keys()):
                record["Hotel Name"] = record.get("Hotel Name") or record.get("Property") or record.get("Property Name") or ""
                if should_exclude_hotel("Chicago", record["Hotel Name"]):
                    continue
                record["Base+Card LP PPD"] = parse_numeric_value(record.get("Base+Card LP PPD") or record.get("Base Card LP PPD") or record.get("Stay LP PPD"))
                record["Stay LP PPD"] = parse_numeric_value(record.get("Stay LP PPD") or record.get("Base+Card LP PPD"))
                record["Total Stay LP"] = parse_numeric_value(record.get("Total Stay LP") or record.get("Stay LP"))
                record["Total Price ($)"] = parse_numeric_value(record.get("Total Price") or record.get("Price"))
                output.append(record)

        if output:
            return output
    return []


def print_best_matches(rows, city, min_ppd=MIN_PPD_DEFAULT):
    ranked = []
    for row in rows:
        ppd = row.get("Base+Card LP PPD") or row.get("Stay LP PPD")
        if ppd is None:
            continue
        if ppd >= min_ppd:
            ranked.append((ppd, row))

    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        log(f"No hotels for {city} at or above {min_ppd}x Base+Card LP PPD.")
        return []

    log(f"Top matches for {city} (>= {min_ppd}x Base+Card LP PPD):")
    for ppd, row in ranked:
        log(
            f"- {row.get('Hotel Name', 'Unknown')} | Base+Card LP PPD: {ppd:.2f}x | "
            f"Stay LP PPD: {row.get('Stay LP PPD', 'n/a')} | Total Stay LP: {row.get('Total Stay LP', 'n/a')} | Price: {row.get('Total Price ($)', 'n/a')}"
        )
    return [row for _, row in ranked]


def search_city(driver, city, start_date, end_date, target_lp, mileage_value, aa_hotels_curl, min_ppd=MIN_PPD_DEFAULT):
    log(f"Opening optimizer UI for {city}")
    driver.get("https://aahoteloptimizer.streamlit.app/~/+/")
    wait = WebDriverWait(driver, 45)

    city_input = find_first_visible(
        driver,
        [
            (By.CSS_SELECTOR, "input[aria-label='City to Search:']"),
            (By.CSS_SELECTOR, "input[aria-label*='City']"),
            (By.CSS_SELECTOR, "input[placeholder*='City']"),
            (By.CSS_SELECTOR, "input[placeholder*='city']"),
        ],
    )
    if not city_input:
        city_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input")))
    set_input_value(driver, city_input, city)
    log(f"City field now: '{current_value(city_input)}'")

    date_inputs = driver.find_elements(By.CSS_SELECTOR, "input[aria-label='Select a date.']")
    if len(date_inputs) < 2:
        date_inputs = [
            el
            for el in driver.find_elements(By.CSS_SELECTOR, "input")
            if (el.get_attribute("type") or "").lower() in {"text", "date"}
            and ("/" in (el.get_attribute("value") or "") or "date" in ((el.get_attribute("aria-label") or "").lower()))
        ]
    if len(date_inputs) >= 2:
        set_input_value(driver, date_inputs[0], start_date)
        set_input_value(driver, date_inputs[1], end_date)
        log(f"Date fields now: start='{current_value(date_inputs[0])}' end='{current_value(date_inputs[1])}'")
    else:
        log("Date fields not found with expected selectors; continuing with page defaults.")

    target_elem = None
    try:
        target_elem = driver.find_element(By.CSS_SELECTOR, "input[aria-label='Target Loyalty Points']")
    except Exception:
        pass
    if target_elem:
        set_input_value(driver, target_elem, str(target_lp))
        log(f"Target LP field now: '{current_value(target_elem)}'")

    mileage_elem = None
    try:
        mileage_elem = driver.find_element(By.CSS_SELECTOR, "input[aria-label='Mileage Value (cents per mile)']")
    except Exception:
        pass
    if mileage_elem:
        set_input_value(driver, mileage_elem, str(mileage_value))
        log(f"Mileage value field now: '{current_value(mileage_elem)}'")

    if aa_hotels_curl:
        curl_input = find_first_visible(
            driver,
            [
                (By.CSS_SELECTOR, "textarea[aria-label='Paste cURL Command']"),
                (By.CSS_SELECTOR, "textarea[aria-label*='cURL']"),
                (By.CSS_SELECTOR, "textarea[placeholder*='cURL']"),
                (By.XPATH, "//textarea[contains(@aria-label, 'curl') or contains(@aria-label, 'cURL') or contains(@placeholder, 'curl') or contains(@placeholder, 'cURL')]"),
            ],
        )
        if curl_input:
            set_input_value(driver, curl_input, aa_hotels_curl)
            log(f"cURL textarea loaded with {len(current_value(curl_input))} chars")
        else:
            log("Could not find cURL textarea in UI; continuing without UI-pasted cURL.")

    search_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//div[contains(., 'Search for Hotel Deals')]]")))
    log(f"Submitting UI search for {city}")
    search_button.click()

    log("Waiting for results table to load...")
    try:
        wait.until(lambda d: "All Hotel Options Found" in d.page_source or "Stay LP PPD" in d.page_source)
    except TimeoutException:
        log("Timed out waiting for UI results. Session may be blocked/challenged or the query returned no table.")
        return []
    time.sleep(8)
    rows = parse_results_table(driver)
    log(f"Parsed {len(rows)} rows from UI table")
    print_best_matches(rows, city, min_ppd=min_ppd)
    return rows


def build_capture_driver():
    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1400,900")
    options.set_capability("pageLoadStrategy", "normal")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=options)


def extract_search_request_from_logs(driver):
    """Pull the most recent searchRequest network call from Chrome performance logs."""
    try:
        logs = driver.get_log("performance")
    except Exception:
        return None

    for entry in reversed(logs):
        try:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") != "Network.requestWillBeSent":
                continue
            req = msg["params"]["request"]
            if "searchRequest" not in req.get("url", ""):
                continue

            url = req["url"]
            headers = req.get("headers", {})
            method = req.get("method", "GET")

            cookie_str = headers.pop("cookie", headers.pop("Cookie", ""))
            header_flags = " ".join(f'-H "{k}: {v}"' for k, v in headers.items() if k.lower() not in ("cookie",))
            cookie_flag = f' -b "{cookie_str}"' if cookie_str else ""
            curl_cmd = f"curl '{url}' {header_flags}{cookie_flag}"
            return curl_cmd
        except Exception:
            continue
    return None


def capture_city_curls(cities, city_place_ids, city_curl_file_map, city_search_queries=None):
    """Open browser, wait for user login, then capture searchRequest per city."""
    driver = build_capture_driver()
    log("Browser opened. Please log in to aadvantagehotels.com now.")
    driver.get("https://www.aadvantagehotels.com")

    input("\n[ACTION REQUIRED] Log in to aadvantagehotels.com in the browser, then press Enter here to continue...\n")
    log("Starting city capture...")

    today_checkin = datetime.today().strftime("%m/%d/%Y")
    tomorrow_checkout = (datetime.today() + timedelta(days=1)).strftime("%m/%d/%Y")

    for city in cities:
        place_id = city_place_ids.get(city, "")
        # Use explicit query hint to avoid ambiguous city names (e.g. Las Vegas NV vs Argentina).
        query_label = (city_search_queries or {}).get(city) or f"{city}, United States"
        query = urllib.parse.quote(query_label)
        search_url = (
            f"https://www.aadvantagehotels.com/search?"
            f"adults=1&checkIn={urllib.parse.quote(today_checkin)}"
            f"&checkOut={urllib.parse.quote(tomorrow_checkout)}"
            f"&children=0&currency=USD&language=en&locationType=CITY"
            f"&mode=earn&numberOfChildren=0&placeId={urllib.parse.quote(place_id)}"
            f"&program=aadvantage&promotion=&query={query}&rooms=1"
            f"&searchId={uuid.uuid4()}&sort=milesHighest&source=AGODA"
        )
        log(f"Navigating to {city} search page...")
        driver.get(search_url)

        curl_cmd = None
        for _ in range(30):
            time.sleep(1)
            curl_cmd = extract_search_request_from_logs(driver)
            if curl_cmd:
                break

        if not curl_cmd:
            log(f"WARNING: could not capture searchRequest for {city}. Try again or capture manually.")
            continue

        out_path = city_curl_file_map.get(city)
        if not out_path:
            out_path = f"curls/curl_{city.replace(' ', '')}.txt"
        if not os.path.isabs(out_path):
            out_path = os.path.join(BASE_DIR, out_path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(curl_cmd)
        log(f"Saved {city} cURL to {out_path}")

    driver.quit()
    log("All cities captured. You can now run the scraper normally.")


def acquire_file_lock(lock_path, timeout_seconds=30, poll_seconds=0.1):
    deadline = time.time() + timeout_seconds
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            return
        except FileExistsError:
            if time.time() >= deadline:
                raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
            time.sleep(poll_seconds)


def release_file_lock(lock_path):
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


def row_identity(row):
    return (
        str(row.get("Hotel Name", "")).strip().lower(),
        str(row.get("City", row.get("city", ""))).strip().lower(),
        str(row.get("Check In", "")).strip(),
        str(row.get("Check Out", "")).strip(),
        str(row.get("Total Price ($)", "")).strip(),
        str(row.get("Total Stay LP", "")).strip(),
    )


def merge_unique_rows(existing_rows, incoming_rows):
    merged = []
    seen = set()
    for row in list(existing_rows) + list(incoming_rows):
        if not isinstance(row, dict):
            continue
        identity = row_identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(row)
    return merged


def rows_for_output(rows):
    output_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out_row = dict(row)
        # Keep Base Rewards available in code for future use, but omit it from file output.
        out_row.pop("Base Rewards", None)
        out_row.pop("Miles Earned", None)
        output_rows.append(out_row)
    return output_rows


def write_json(rows, output_path):
    lock_path = f"{output_path}.lock"
    acquire_file_lock(lock_path)
    try:
        existing_rows = []
        if os.path.exists(output_path):
            try:
                with open(output_path, "r", encoding="utf-8") as fh:
                    existing_payload = json.load(fh)
                if isinstance(existing_payload, dict) and isinstance(existing_payload.get("results"), list):
                    existing_rows = existing_payload.get("results") or []
            except Exception:
                existing_rows = []

        merged_rows = merge_unique_rows(existing_rows, rows)
        merged_rows = rows_for_output(merged_rows)
        merged_rows = sort_rows_by_ppd_desc(merged_rows)

        payload = {
            "row_count": len(merged_rows),
            "results": merged_rows,
        }

        tmp_path = f"{output_path}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, output_path)
    finally:
        release_file_lock(lock_path)


def filter_rows_by_min_ppd(rows, min_ppd):
    filtered = []
    for row in rows:
        ppd = row.get("Base+Card LP PPD") or row.get("Stay LP PPD")
        if isinstance(ppd, (int, float)) and ppd >= min_ppd:
            filtered.append(row)
    return filtered


def sort_rows_by_ppd_desc(rows):
    return sorted(
        rows,
        key=lambda row: (row.get("Stay LP PPD") or row.get("Base+Card LP PPD") or -1),
        reverse=True,
    )


def write_progress_json(all_rows, window_rows, output_json, min_ppd, fetch_all_pages=True):
    filtered_rows = filter_rows_by_min_ppd(all_rows, min_ppd)
    sorted_rows = sort_rows_by_ppd_desc(filtered_rows)
    write_json(sorted_rows, output_json)
    window_qualifying = filter_rows_by_min_ppd(window_rows, min_ppd)
    fetch_label = f"top {MAX_PAGES_PER_SEARCH} pages" if fetch_all_pages else "first page"
    log(f"    fetched ({fetch_label}): {len(window_rows)}")
    log(f"    filtered (>= {min_ppd} PPD): {len(window_qualifying)}")
    log(f"    cumulative count: {len(filtered_rows)} (across all cities)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="AA Hotels scraper with optional cURL-only mode")
    parser.add_argument("city", nargs="?", help="Single city override")
    parser.add_argument("--curl-file", help="Path to a file containing the full cURL command")
    parser.add_argument("--curl-only", action="store_true", help="Use only cURL/API parsing and skip Selenium")
    parser.add_argument("--headless", action="store_true", help="Force headless browser mode")
    parser.add_argument("--capture-curls", action="store_true", help="Open browser, log in, then auto-capture cURL per city")
    parser.add_argument("--all-pages", action="store_true", help="Force fetching all pages")
    parser.add_argument("--first-page-only", action="store_true", help="Fetch only page 1 (faster, may miss deals)")
    parser.add_argument("--start-date", help="Override start_date (format: YYYY/MM/DD)")
    parser.add_argument("--end-date", help="Override end_date (format: YYYY/MM/DD)")
    parser.add_argument("--min-ppd", type=float, help="Override min LP PPD threshold")
    args = parser.parse_args()

    config = {**json.loads(INFRA_CONFIG), **json.loads(CONFIG)}
    config.update({k: v for k, v in os.environ.items() if k.startswith("AA_") or k in {"CITIES", "START_DATE", "END_DATE", "TARGET_LP", "MILEAGE_VALUE", "OUTPUT_JSON", "USE_CURL_ONLY", "AA_HOTELS_CURL_FILE", "AA_CITY_CURLS", "AA_CITY_CURL_FILES", "AA_CITY_PLACE_IDS"}})

    city_arg = None
    if args.city:
        city_arg = args.city
    elif os.environ.get("AA_CITY"):
        city_arg = os.environ.get("AA_CITY")

    if city_arg:
        cities = [city_arg]
    else:
        cities = config.get("cities") or []

    start_date = config.get("start_date", START_DATE)
    if args.start_date:
        start_date = args.start_date
    end_date = config.get("end_date", END_DATE)
    
    if args.end_date:
        end_date = args.end_date
    target_lp = int(config.get("target_lp", 200000))
    mileage_value = float(config.get("mileage_value", 1.5))
    headless = parse_bool(config.get("headless", False), default=False)
    if args.headless:
        headless = True
    output_json = config.get("output_json", "aahotel_results.json")
    aa_hotels_curl = config.get("aa_hotels_curl", "")
    if os.environ.get("AA_HOTELS_CURL"):
        aa_hotels_curl = os.environ.get("AA_HOTELS_CURL", "")
    if args.curl_file:
        aa_hotels_curl = read_text_file(args.curl_file)
    elif os.environ.get("AA_HOTELS_CURL_FILE"):
        aa_hotels_curl = read_text_file(os.environ.get("AA_HOTELS_CURL_FILE", ""))

    city_curls = parse_city_curls(config.get("city_curls"))
    city_curls.update(parse_city_curls(os.environ.get("AA_CITY_CURLS")))
    city_curls.update(load_city_curls_from_files(config.get("city_curl_files")))
    city_curls.update(load_city_curls_from_files(os.environ.get("AA_CITY_CURL_FILES")))
    city_place_ids = parse_string_map(config.get("city_place_ids"))
    city_place_ids.update(parse_string_map(os.environ.get("AA_CITY_PLACE_IDS")))

    if args.capture_curls:
        city_curl_file_map = parse_string_map(config.get("city_curl_files"))
        city_search_queries = parse_string_map(config.get("city_search_queries"))
        capture_city_curls(cities, city_place_ids, city_curl_file_map, city_search_queries=city_search_queries)
        sys.exit(0)

    coverage_issues = validate_city_curl_coverage(cities, aa_hotels_curl, city_curls, city_place_ids)
    if coverage_issues:
        log("City cURL coverage issues detected:")
        for issue in coverage_issues:
            log(f"- {issue}")

    sample_curl_for_health = ""
    if len(cities) == 1 and cities[0] in city_curls:
        sample_curl_for_health = city_curls[cities[0]]
    else:
        sample_curl_for_health = aa_hotels_curl or next(iter(city_curls.values()), "")
    if sample_curl_for_health:
        session_warnings = check_session_health(sample_curl_for_health)
        if session_warnings:
            log("Session/token warnings:")
            for warning in session_warnings:
                log(f"- {warning}")

    use_curl_only = args.curl_only or parse_bool(config.get("use_curl_only"), default=False) or parse_bool(os.environ.get("USE_CURL_ONLY"), default=False)
    fetch_all_pages = parse_bool(config.get("all_pages"), default=True)
    if args.all_pages:
        fetch_all_pages = True
    if args.first_page_only:
        fetch_all_pages = False

    try:
        start_date_obj = parse_date_input(start_date)
    except ValueError as exc:
        log(f"Invalid start_date configuration: {exc}")
        sys.exit(1)

    today_obj = datetime.today().date()
    if start_date_obj < today_obj:
        adjusted_start_date = format_yyyy_mm_dd(today_obj)
        log(f"start_date {start_date} is in the past; using {adjusted_start_date} instead.")
        start_date = adjusted_start_date

    try:
        date_windows = build_date_windows(start_date, end_date)
    except ValueError as exc:
        log(f"Invalid date configuration: {exc}")
        sys.exit(1)

    city_count = len(cities)
    city_list = ", ".join(cities) if cities else "(none)"
    log(f"Starting run for {city_count} {'city' if city_count == 1 else 'cities'}: {city_list}")
    log(f"Date windows: {len(date_windows)} (daily sweep, 1-night stays)")

    min_ppd = float(config.get("min_ppd", MIN_PPD_DEFAULT))
    if args.min_ppd is not None:
        min_ppd = args.min_ppd

    all_rows = []
    if use_curl_only:
        if not aa_hotels_curl and not city_curls:
            log("cURL-only mode requested but no cURL command provided. Use --curl-file, AA_HOTELS_CURL, or city_curls.")
            sys.exit(1)

        for city in cities:
            log(f"Searching {city} with cURL...")
            place_id = city_place_ids.get(city)
            shared_or_city_curl = city_curls.get(city) or aa_hotels_curl

            if not shared_or_city_curl:
                log(f"No cURL available for {city}; skipping.")
                continue

            if city not in city_curls and not place_id and not city_name_matches_curl(shared_or_city_curl, city):
                hint = parse_city_hint_from_curl(shared_or_city_curl)
                log(f"Skipping {city}: cURL appears pinned to '{hint}'. Provide city_place_ids or city_curls for this city.")
                continue

            for window_start, window_end in date_windows:
                if city in city_curls:
                    city_curl = rewrite_curl_for_city(shared_or_city_curl, city, window_start, window_end)
                elif place_id:
                    city_curl = rewrite_curl_for_city(shared_or_city_curl, city, window_start, window_end, place_id=place_id)
                else:
                    city_curl = rewrite_curl_for_city(shared_or_city_curl, city, window_start, window_end, rewrite_query=False)

                rows = search_city_with_curl(city, window_start, window_end, target_lp, mileage_value, city_curl, fetch_all_pages=fetch_all_pages)
                if rows:
                    all_rows.extend(rows)
                    write_progress_json(all_rows, rows, output_json, min_ppd, fetch_all_pages=fetch_all_pages)

        if all_rows:
            final_rows = sort_rows_by_ppd_desc(filter_rows_by_min_ppd(all_rows, min_ppd))
            write_json(final_rows, output_json)
            log(f"Saved {len(final_rows)} filtered row(s) (>= {min_ppd} PPD) to {output_json}")
        else:
            log("No results parsed from cURL response.")
        sys.exit(0)

    for city in cities:
        log(f"--- {city} ---")
        place_id = city_place_ids.get(city)
        shared_or_city_curl = city_curls.get(city) or aa_hotels_curl
        city_had_error = False

        for window_start, window_end in date_windows:
            rows = []
            city_curl = ""

            if shared_or_city_curl:
                if city in city_curls:
                    city_curl = rewrite_curl_for_city(shared_or_city_curl, city, window_start, window_end)
                elif place_id:
                    city_curl = rewrite_curl_for_city(shared_or_city_curl, city, window_start, window_end, place_id=place_id)
                elif city_name_matches_curl(shared_or_city_curl, city):
                    city_curl = rewrite_curl_for_city(shared_or_city_curl, city, window_start, window_end, rewrite_query=False)
                else:
                    hint = parse_city_hint_from_curl(shared_or_city_curl)
                    log(f"cURL pinned to '{hint}', cannot search {city}. Add a city-specific cURL file.")
                    break

            if city_curl:
                rows = search_city_with_curl(city, window_start, window_end, target_lp, mileage_value, city_curl, fetch_all_pages=fetch_all_pages)

            if not rows:
                if not city_had_error:
                    log(f"cURL failed or returned no results for {city} â€” check curl file / session. Skipping city.")
                    city_had_error = True
                break

            for row in rows:
                if not row.get("city") and not row.get("City"):
                    row["city"] = city
            all_rows.extend(rows)
            write_progress_json(all_rows, rows, output_json, min_ppd, fetch_all_pages=fetch_all_pages)
            time.sleep(1)

        if all_rows:
            final_rows = sort_rows_by_ppd_desc(filter_rows_by_min_ppd(all_rows, min_ppd))
            write_json(final_rows, output_json)
            log(f"Saved {len(final_rows)} filtered row(s) (>= {min_ppd} PPD) to {output_json}")
        else:
            log("No results parsed.")
    

