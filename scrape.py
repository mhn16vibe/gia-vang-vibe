"""Lấy giá vàng SJC, DOJI, Bảo Tín Minh Châu và lưu vào data/prices.csv.

Chạy tự động trên GitHub Actions lúc 9h và 15h30 (giờ Việt Nam).
Nếu một trang lỗi, script ghi lý do vào data/status.json và lưu nội dung bảng
đã đọc được vào data/debug_<hãng>.txt để dễ sửa lại.
"""
import csv
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DATA = Path(__file__).parent / "data"
CSV_PATH = DATA / "prices.csv"
STATUS_PATH = DATA / "status.json"
FIELDS = ["timestamp", "date", "brand", "product", "buy", "sell"]

# Mỗi sản phẩm: (tên hiển thị, [các mẫu để tìm dòng trong bảng]).
# Mẫu đã bỏ dấu, bỏ khoảng trắng, viết thường. Thử lần lượt từng mẫu.
# Nếu sau lần chạy đầu tiên chọn sai dòng, chỉ cần sửa các mẫu này.
SOURCES = [
    # Trang sjc.com.vn chặn truy cập tự động (Cloudflare) nên tạm tắt.
    # Giá vàng miếng SJC vẫn có trong bảng của DOJI và Bảo Tín Minh Châu.
    {
        "brand": "DOJI",
        "url": "https://banggia.doji.vn/gold-price",
        "products": [
            ("Vàng miếng SJC", ["vangmiengsjc"]),
            ("Nhẫn tròn 9999 Hưng Thịnh Vượng", ["nhantron9999hungthinhvuong", "hungthinhvuong"]),
        ],
    },
    {
        "brand": "Bảo Tín Mạnh Hải",
        "url": "https://baotinmanhhai.vn/bang-gia-vang",
        "products": [
            ("Vàng miếng SJC", ["vangmiengsjc"]),
            ("Kim Gia Bảo 24K", ["kimgiabao24k"]),
        ],
    },
    {
        "brand": "PNJ",
        "url": "https://www.pnj.com.vn/site/gia-vang",
        "products": [
            ("Vàng miếng SJC 999.9", ["vangmiengsjc"]),
            ("Nhẫn trơn PNJ 999.9", ["nhantronpnj"]),
        ],
    },
    # Trang btmc.vn không phản hồi máy chủ GitHub (chặn truy cập tự động) nên tạm tắt.
    # Muốn bật lại: bỏ dấu # ở đầu các dòng bên dưới.
    # {
    #     "brand": "Bảo Tín Minh Châu",
    #     "url": "https://btmc.vn/gia-vang-theo-ngay.html",
    #     "products": [
    #         ("Vàng miếng SJC", ["vangmiengsjc"]),
    #         ("Vàng miếng Rồng Thăng Long", ["vangmiengvrtl", "rongthanglong"]),
    #     ],
    # },
]

# Cận dưới 100 triệu để không nhầm cột "hàm lượng 99.99" với giá. Nếu giá vàng xuống dưới mức này, hạ số này xuống.
MIN_VND, MAX_VND = 1e8, 5e8


def norm(text: str) -> str:
    """Bỏ dấu tiếng Việt, khoảng trắng và ký tự lạ để so khớp tên."""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", text.lower())


def parse_number(cell: str):
    """Đọc số ở đầu ô, vd '14,540 ▲200K' -> 14540.0. Ô '▲200K' bị bỏ qua."""
    m = re.match(r"^\s*(\d[\d.,]*)", cell)
    if not m:
        return None
    s = m.group(1).rstrip(".,")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):  # dấu phân cách hàng nghìn
        return float(re.sub(r"[.,]", "", s))
    return float(s.replace(",", "."))  # số thập phân, vd 145,4


def to_vnd_per_luong(value: float):
    """Đưa về đồng/lượng. Trang hiển thị lúc triệu, lúc nghìn đồng/chỉ..."""
    for mult in (1, 10, 100, 1e3, 1e4, 1e5, 1e6):  # khoảng giá hẹp nên chỉ 1 hệ số khớp
        v = value * mult
        if MIN_VND <= v <= MAX_VND:
            return round(v)
    return None


def find_price(rows, patterns):
    """Trả về (tên dòng, mua, bán) của dòng đầu tiên khớp mẫu, hoặc None."""
    for pattern in patterns:
        for cells in rows:
            name_idx = next((i for i, c in enumerate(cells) if re.search(r"[^\W\d_]", c) and parse_number(c) is None), None)
            if name_idx is None or pattern not in norm(cells[name_idx]):
                continue
            nums = [parse_number(c) for c in cells[name_idx + 1:]]
            prices = [to_vnd_per_luong(n) for n in nums if n is not None]
            prices = [p for p in prices if p]
            if len(prices) >= 2:
                buy, sell = prices[0], prices[1]
                if buy <= sell:
                    return cells[name_idx], buy, sell
    return None


EXTRACT_JS = """
() => {
  const rows = [];
  document.querySelectorAll('tr, [role="row"]').forEach(r => {
    const cells = Array.from(r.querySelectorAll('th,td,[role="cell"],[role="gridcell"]'))
      .map(c => c.innerText.trim().replace(/\\s+/g, ' '));
    if (cells.length >= 2) rows.push(cells);
  });
  return rows;
}
"""


PRICE_TOKEN = re.compile(r"(?<![\w.,▲▼+\-])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)(?![\d.,]*\s*[KkMm%])")


def text_rows(text):
    """Biến mỗi dòng chữ trên trang thành [tên, số, số...] để dò giá."""
    rows = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        nums = PRICE_TOKEN.findall(line)
        if nums:
            rows.append(["T: " + line] + nums)
    # Trang hiển thị dạng lưới: tên và từng con số nằm trên các dòng riêng.
    # Ghép mỗi dòng có chữ với các con số của tối đa 5 dòng ngay sau nó.
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    for i, line in enumerate(lines):
        if not re.search(r"[^\W\d_]", line):
            continue
        nums = []
        for nxt in lines[i + 1:i + 6]:
            if re.search(r"[^\W\d_]{3,}", nxt):  # gặp tên sản phẩm khác thì dừng
                break
            nums += PRICE_TOKEN.findall(nxt)
        if nums:
            rows.append(["W: " + line] + nums)
    return rows


def fetch_rows(page, url):
    """Trả về (các dòng để dò giá, nội dung để ghi vào tệp debug)."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001  trang có kết nối chạy liên tục
        pass
    page.wait_for_timeout(5000)
    table = page.evaluate(EXTRACT_JS)
    text = page.evaluate("() => document.body ? document.body.innerText : ''")
    rows = table + text_rows(text)
    dump = ("=== BANG (" + str(len(table)) + " dong) ===\n" + "\n".join(" | ".join(r) for r in table)
            + "\n\n=== CHU TREN TRANG ===\n" + "\n".join(text.splitlines()[:400]))
    return rows, dump


def append_rows(new_rows):
    DATA.mkdir(exist_ok=True)
    is_new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            w.writeheader()
        w.writerows(new_rows)


def main():
    from playwright.sync_api import sync_playwright

    DATA.mkdir(exist_ok=True)
    now = datetime.now(TZ)
    status = {"run_at": now.isoformat(timespec="seconds"), "sources": {}}
    new_rows, failures = [], 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale="vi-VN", timezone_id="Asia/Ho_Chi_Minh")
        for src in SOURCES:
            brand = src["brand"]
            page = context.new_page()
            try:
                rows, dump = fetch_rows(page, src["url"])
                (DATA / f"debug_{norm(brand)}.txt").write_text(dump, encoding="utf-8")
                found, missing = 0, []
                for product, patterns in src["products"]:
                    hit = find_price(rows, patterns)
                    if hit:
                        _, buy, sell = hit
                        new_rows.append({
                            "timestamp": now.isoformat(timespec="seconds"),
                            "date": now.strftime("%Y-%m-%d"),
                            "brand": brand, "product": product,
                            "buy": buy, "sell": sell,
                        })
                        found += 1
                    else:
                        missing.append(product)
                if missing:
                    failures += 1
                    status["sources"][brand] = {"ok": found > 0, "error": f"Không tìm thấy dòng: {', '.join(missing)} (đọc được {len(rows)} dòng)"}
                else:
                    status["sources"][brand] = {"ok": True}
            except Exception as e:  # noqa: BLE001
                try:  # vẫn ghi lại những gì trang hiển thị để dò lỗi
                    text = page.evaluate("() => document.body ? document.body.innerText : ''")
                    (DATA / f"debug_{norm(brand)}.txt").write_text("LOI: " + str(e)[:300] + "\n\n" + text[:20000], encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
                failures += 1
                status["sources"][brand] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            finally:
                page.close()
        browser.close()

    if new_rows:
        append_rows(new_rows)
    print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)
    DATA.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
