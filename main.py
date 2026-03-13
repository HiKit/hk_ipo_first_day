import argparse
import math
import random
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional

from bs4 import BeautifulSoup


IPO_LIST_URL = "https://www.futunn.com/quote/hk/ipo?code=LCNB"
AAS_LIST_URL_TEMPLATE = (
    "https://www.aastocks.com/sc/stocks/analysis/stock-aafn/{code}/0/hk-stock-news/1"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36"
)

BLOCK_TEXTS = ("访问频繁", "请稍后重试", "访问频繁，请稍后重试")
NAV_TIMEOUT_MS = 20000

def safe_print(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="ignore"))
    sys.stdout.flush()


FUTU_TAG = "富途交易平台"  # 富途交易平台
GREY_TEXT = "暗盘"  # 暗盘
GREY_LINK_TEXT = "暗盘收"  # 暗盘收
DAY1_LINK_TEXT = "全日收"  # 全日收


@dataclass
class PriceBlock:
    open: Optional[float]
    close: Optional[float]
    high: Optional[float]
    low: Optional[float]


@dataclass
class IPORecord:
    code: str
    name: str
    offer_price: Optional[float]
    listing_date: str
    grey_market: Optional[PriceBlock] = None
    first_day: Optional[PriceBlock] = None


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def normalize_date(text: str) -> str:
    digits = _digits_only(text)
    if len(digits) == 8:
        return digits
    return ""


def parse_price(text: str) -> Optional[float]:
    if text is None:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def parse_ipo_table(html: str, target_date: str) -> List[IPORecord]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    headers = [_norm_text(th.get_text()) for th in table.find_all("th")]
    if not headers:
        return []

    def find_idx(candidates: Iterable[str]) -> Optional[int]:
        for idx, header in enumerate(headers):
            for cand in candidates:
                if cand in header:
                    return idx
        return None

    idx_date = find_idx(["上市日期", "上市日"])
    idx_code = find_idx(["股票代码", "代码"])
    idx_name = find_idx(["股票名称", "名称"])
    idx_offer = find_idx(["招股价", "发售价", "发行价"])

    if idx_date is None or idx_code is None:
        return []

    rows = []
    tbody = table.find("tbody")
    tr_list = tbody.find_all("tr") if tbody else table.find_all("tr")
    for tr in tr_list:
        cells = tr.find_all(["td", "th"])
        if not cells or len(cells) <= idx_date:
            continue
        listing_date = normalize_date(cells[idx_date].get_text())
        if listing_date != target_date:
            continue
        code = _norm_text(cells[idx_code].get_text()) if idx_code is not None else ""
        name = _norm_text(cells[idx_name].get_text()) if idx_name is not None else ""
        offer = parse_price(cells[idx_offer].get_text()) if idx_offer is not None else None
        rows.append(
            IPORecord(
                code=code,
                name=name,
                offer_price=offer,
                listing_date=listing_date,
            )
        )

    return rows


def parse_ipo_text(text: str, target_date: str) -> List[IPORecord]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return []

    try:
        start_idx = lines.index("代码")
    except ValueError:
        return []

    try:
        end_idx = lines.index("上市日期", start_idx)
    except ValueError:
        return []

    headers = lines[start_idx : end_idx + 1]
    header_count = len(headers)
    if header_count < 4:
        return []

    data_lines = lines[end_idx + 1 :]
    rows: List[List[str]] = []
    for i in range(0, len(data_lines), header_count):
        chunk = data_lines[i : i + header_count]
        if len(chunk) < header_count:
            break
        rows.append(chunk)

    idx_date = headers.index("上市日期")
    idx_code = headers.index("代码")
    idx_name = headers.index("股票名称") if "股票名称" in headers else None
    idx_offer = None
    for label in ("发行价", "发售价", "招股价"):
        if label in headers:
            idx_offer = headers.index(label)
            break

    records: List[IPORecord] = []
    for row in rows:
        listing_date = normalize_date(row[idx_date])
        if listing_date != target_date:
            continue
        code = _norm_text(row[idx_code])
        if code and not code.endswith("-HK"):
            if re.fullmatch(r"\d{4,5}", code):
                code = f"{code}-HK"
        name = _norm_text(row[idx_name]) if idx_name is not None else ""
        offer = parse_price(row[idx_offer]) if idx_offer is not None else None
        records.append(
            IPORecord(
                code=code,
                name=name,
                offer_price=offer,
                listing_date=listing_date,
            )
        )

    return records


def _extract_futu_segment(text: str) -> str:
    if FUTU_TAG in text:
        start = text.find(FUTU_TAG)
        end = text.find("根据", start + len(FUTU_TAG))  # 根据
        if end == -1:
            end = start + 800
        return text[start:end]
    return text


def parse_grey_market_from_text(text: str) -> Optional[PriceBlock]:
    if not text:
        return None
    segment = _extract_futu_segment(text)
    open_price = None
    close_price = None
    high_price = None
    low_price = None

    open_scope = segment
    close_idx = open_scope.find("收报")  # 收报
    if close_idx != -1:
        open_scope = open_scope[:close_idx]

    open_match = re.search(
        rf"{GREY_TEXT}[^\d]{{0,20}}(?:高开|低开|平开|开盘|开市|开报)?[^\d]{{0,12}}"
        r"(?:[0-9]+(?:\.[0-9]+)?%[^\d]{0,6})?"
        r"(?:报|報)?\s*([0-9]+(?:\.[0-9]+)?)元",
        open_scope,
    )
    if open_match:
        open_price = parse_price(open_match.group(1))
    else:
        match = re.search(rf"{GREY_TEXT}[^\d]{{0,20}}(?:报|報)\s*([0-9]+(?:\.[0-9]+)?)元", open_scope)
        if match:
            open_price = parse_price(match.group(1))

    match = re.search(r"(?:收报|收市|收盘|收于)\s*([0-9]+(?:\.[0-9]+)?)元?", segment)
    if match:
        close_price = parse_price(match.group(1))

    match = re.search(
        r"最高/低分别见\s*([0-9]+(?:\.[0-9]+)?)[/／]([0-9]+(?:\.[0-9]+)?)元",
        segment,
    )
    if match:
        high_price = parse_price(match.group(1))
        low_price = parse_price(match.group(2))
    else:
        match = re.search(r"最高[^\d]{0,10}([0-9]+(?:\.[0-9]+)?)元[^\d]{0,10}最低[^\d]{0,10}([0-9]+(?:\.[0-9]+)?)元", segment)
        if match:
            high_price = parse_price(match.group(1))
            low_price = parse_price(match.group(2))

    match = re.search(r"最高[^\d]{0,6}([0-9]+(?:\.[0-9]+)?)元", segment)
    if match and high_price is None:
        high_price = parse_price(match.group(1))

    match = re.search(r"最低[^\d]{0,6}([0-9]+(?:\.[0-9]+)?)元", segment)
    if match and low_price is None:
        low_price = parse_price(match.group(1))

    if open_price is None and close_price is not None:
        open_price = close_price

    if open_price is None and close_price is None and high_price is None and low_price is None:
        return None
    return PriceBlock(open=open_price, close=close_price, high=high_price, low=low_price)


def parse_first_day_from_text(text: str) -> Optional[PriceBlock]:
    if not text:
        return None
    segment = text
    open_price = None
    close_price = None
    high_price = None
    low_price = None

    match = re.search(
        r"(?:首日|全日)?[^\d]{0,10}(?:高开|低开|平开|开盘|开市|开报)"
        r"[^\d]{0,12}(?:[0-9]+(?:\.[0-9]+)?%[^\d]{0,6})?"
        r"(?:报|報)?\s*([0-9]+(?:\.[0-9]+)?)元",
        segment,
    )
    if match:
        open_price = parse_price(match.group(1))
    else:
        match = re.search(r"(?:首日|挂牌)?[^\d]{0,10}(?:开报|开市|开盘)\s*([0-9]+(?:\.[0-9]+)?)元", segment)
        if match:
            open_price = parse_price(match.group(1))

    match = re.search(
        r"(?:全日|首日)?[^\d]{0,6}"
        r"(?:收报|收報|收市|收盘|收于|收於)\s*"
        r"([0-9]+(?:\.[0-9]+)?)元?",
        segment,
    )
    if not match:
        match = re.search(r"(?:全日|首日)[^\d]{0,6}收\s*([0-9]+(?:\.[0-9]+)?)元", segment)
    if match:
        close_price = parse_price(match.group(1))

    match = re.search(
        r"最高/低分别见\s*([0-9]+(?:\.[0-9]+)?)[/／]([0-9]+(?:\.[0-9]+)?)元",
        segment,
    )
    if match:
        high_price = parse_price(match.group(1))
        low_price = parse_price(match.group(2))
    else:
        match = re.search(r"最高[^\d]{0,10}([0-9]+(?:\.[0-9]+)?)元[^\d]{0,10}最低[^\d]{0,10}([0-9]+(?:\.[0-9]+)?)元", segment)
        if match:
            high_price = parse_price(match.group(1))
            low_price = parse_price(match.group(2))

    match = re.search(r"最高[^\d]{0,6}([0-9]+(?:\.[0-9]+)?)元", segment)
    if match and high_price is None:
        high_price = parse_price(match.group(1))

    match = re.search(r"最低[^\d]{0,6}([0-9]+(?:\.[0-9]+)?)元", segment)
    if match and low_price is None:
        low_price = parse_price(match.group(1))

    if open_price is None and close_price is None and high_price is None and low_price is None:
        return None
    return PriceBlock(open=open_price, close=close_price, high=high_price, low=low_price)


def pct_change(value: Optional[float], base: Optional[float]) -> Optional[float]:
    if value is None or base is None or base == 0:
        return None
    return (value - base) / base * 100.0


def format_price_with_pct(value: Optional[float], pct: Optional[float]) -> str:
    if value is None:
        return "--"
    if pct is None or math.isnan(pct):
        return f"{value:.2f}(--)"
    return f"{value:.2f}({pct:+.2f}%)"


def format_output(records: List[IPORecord]) -> str:
    blocks: List[str] = []
    for record in records:
        offer = record.offer_price
        grey = record.grey_market or PriceBlock(None, None, None, None)
        day1 = record.first_day or PriceBlock(None, None, None, None)

        grey_open = format_price_with_pct(grey.open, pct_change(grey.open, offer))
        grey_close = format_price_with_pct(grey.close, pct_change(grey.close, offer))
        grey_high = format_price_with_pct(grey.high, pct_change(grey.high, offer))
        grey_low = format_price_with_pct(grey.low, pct_change(grey.low, offer))

        day_open = format_price_with_pct(day1.open, pct_change(day1.open, offer))
        day_close = format_price_with_pct(day1.close, pct_change(day1.close, offer))
        day_high = format_price_with_pct(day1.high, pct_change(day1.high, offer))
        day_low = format_price_with_pct(day1.low, pct_change(day1.low, offer))

        header = f"{record.name} {record.code}".strip()
        offer_line = f"招股价：{offer:.2f}" if offer is not None else "招股价：--"
        grey_line = f"暗盘：开{grey_open}，收{grey_close}，最高{grey_high}，最低{grey_low}"
        day_line = f"首日：开{day_open}，收{day_close}，最高{day_high}，最低{day_low}"

        blocks.append("\n".join([header, offer_line, grey_line, day_line]))

    return "\n\n".join(blocks)


def _ensure_date(date_str: str) -> str:
    try:
        import datetime as dt

        dt.datetime.strptime(date_str, "%Y%m%d")
        return date_str
    except ValueError as exc:
        raise ValueError("date must be YYYYMMDD, e.g. 20260309") from exc


def _create_context(playwright, headless: bool, user_data_dir: str, slow_mo: int):
    if getattr(sys, "_force_headed", False):
        headless = False

    context = playwright.chromium.launch_persistent_context(
        user_data_dir,
        headless=headless,
        slow_mo=slow_mo,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 720},
        locale="zh-CN",
        user_agent=USER_AGENT,
        extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});"
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});"
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
    )
    context.add_init_script("window.chrome = { runtime: {} };")
    context.add_init_script(
        """
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
          parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
        );
        """
    )
    return context


def _create_page(context):
    page = context.new_page()
    page.set_default_timeout(30000)
    return page


def _human_pause(base_ms: int = 150, jitter_ms: int = 150) -> int:
    return base_ms + random.randint(0, jitter_ms)


def _safe_goto(page, url: str, min_text_len: int = 200, debug: bool = False) -> str:
    if debug:
        safe_print(f"[goto] {url}")
    for attempt in range(1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception:
            if debug:
                safe_print(f"[goto] failed attempt {attempt + 1}")
            continue
        page.wait_for_timeout(_human_pause(400, 200))
        body_text = page.evaluate("() => document.body.innerText")
        if debug:
            safe_print(f"[goto] text_len={len(body_text)}")
        if any(block in body_text for block in BLOCK_TEXTS) or len(body_text) < min_text_len:
            if debug:
                safe_print("[goto] blocked or too short, retrying")
            page.wait_for_timeout(800 * (attempt + 1))
            continue
        return body_text
    return page.evaluate("() => document.body.innerText")


def _collect_link_hrefs(page, text: str) -> List[str]:
    try:
        return (
            page.evaluate(
                "(needle) => Array.from(document.querySelectorAll('a'))"
                ".filter(a => a.textContent && a.textContent.includes(needle))"
                ".map(a => a.href)",
                text,
            )
            or []
        )
    except Exception:
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize HK IPO data for a date.")
    parser.add_argument("date", help="YYYYMMDD")
    parser.add_argument("--slow-ms", type=int, default=0, help="slowmo for playwright actions")
    parser.add_argument("--user-data-dir", default="E:\\hk_ipo_first_day\\.pw_user", help="user data dir")
    parser.add_argument("--manual-wait", type=int, default=0, help="extra wait seconds when blocked")
    parser.add_argument("--debug", action="store_true", help="print debug logs")
    args = parser.parse_args()

    date_str = _ensure_date(args.date)


    from playwright.sync_api import sync_playwright

    def fetch_ipo_list(page, date_str: str) -> List[IPORecord]:
        body_text = _safe_goto(page, IPO_LIST_URL, min_text_len=500, debug=args.debug)
        records = parse_ipo_text(body_text, date_str)
        if records:
            return records
        html = page.content()
        return parse_ipo_table(html, date_str)

    def fetch_first_day(page, record: IPORecord) -> Optional[PriceBlock]:
        list_url = AAS_LIST_URL_TEMPLATE.format(code=record.code.replace("-HK", ""))
        try:
            body_text = _safe_goto(page, list_url, min_text_len=500, debug=args.debug)
            if any(block in body_text for block in BLOCK_TEXTS) or len(body_text) < 200:
                if args.manual_wait > 0:
                    page.wait_for_timeout(args.manual_wait * 1000)
                    body_text = page.evaluate("() => document.body.innerText")
                if any(block in body_text for block in BLOCK_TEXTS) or len(body_text) < 200:
                    return None
        except Exception:
            return None

        hrefs = _collect_link_hrefs(page, DAY1_LINK_TEXT)
        if not hrefs:
            page.wait_for_timeout(1500)
            hrefs = _collect_link_hrefs(page, DAY1_LINK_TEXT)
        if args.debug:
            safe_print(f"[first_day] links={len(hrefs)}")
            if hrefs:
                safe_print(f"[first_day] href0={hrefs[0]}")
        if not hrefs:
            return None

        for href in hrefs[:30]:
            if not href:
                continue

            try:
                page.goto(href, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(1500)
            except Exception:
                continue

            article_text = page.evaluate("() => document.body.innerText") or ""
            if record.code.replace("-HK", "") not in article_text and record.name not in article_text:
                continue
            block = parse_first_day_from_text(article_text)
            if args.debug:
                preview = article_text[:300].replace("\n", " ").replace(" ", " ")
                safe_print(f"[first_day] preview: {preview}")
                safe_print(f"[first_day] parsed: {block}")
            if block:
                return block

        return None

    def fetch_grey_market(page, record: IPORecord) -> Optional[PriceBlock]:
        list_url = AAS_LIST_URL_TEMPLATE.format(code=record.code.replace("-HK", ""))
        try:
            body_text = _safe_goto(page, list_url, min_text_len=500, debug=args.debug)
            if any(block in body_text for block in BLOCK_TEXTS) or len(body_text) < 200:
                if args.manual_wait > 0:
                    page.wait_for_timeout(args.manual_wait * 1000)
                    body_text = page.evaluate("() => document.body.innerText")
                if any(block in body_text for block in BLOCK_TEXTS) or len(body_text) < 200:
                    return None
        except Exception:
            return None

        hrefs = _collect_link_hrefs(page, GREY_LINK_TEXT)
        if not hrefs:
            page.wait_for_timeout(1500)
            hrefs = _collect_link_hrefs(page, GREY_LINK_TEXT)
        if args.debug:
            safe_print(f"[grey] links={len(hrefs)}")
            if hrefs:
                safe_print(f"[grey] href0={hrefs[0]}")
        if not hrefs:
            return None

        for href in hrefs[:30]:
            if not href:
                continue

            try:
                page.goto(href, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(1500)
            except Exception:
                continue

            article_text = page.evaluate("() => document.body.innerText") or ""
            if FUTU_TAG not in article_text:
                continue
            if record.code.replace("-HK", "") not in article_text and record.name not in article_text:
                continue

            block = parse_grey_market_from_text(article_text)
            if block:
                return block

        return None

    with sync_playwright() as p:
        context = _create_context(p, not args.debug, args.user_data_dir, args.slow_ms)
        page = _create_page(context)
        if args.debug:
            page.on("console", lambda msg: safe_print(f"[console] {msg.type}: {msg.text}"))
            page.on("pageerror", lambda err: safe_print(f"[pageerror] {err}"))
            page.on("requestfailed", lambda req: safe_print(f"[requestfailed] {req.url}"))
            page.on("response", lambda resp: safe_print(f"[response] {resp.status} {resp.url}"))

        records = fetch_ipo_list(page, date_str)
        if args.debug:
            safe_print(f"found {len(records)} records for {date_str}")

        for record in records:
            if record.code:
                if args.debug:
                    safe_print(f"fetching {record.code} {record.name}")
                record.first_day = fetch_first_day(page, record)
                record.grey_market = fetch_grey_market(page, record)
                page.wait_for_timeout(_human_pause(300, 300))

        context.close()

    print(format_output(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


