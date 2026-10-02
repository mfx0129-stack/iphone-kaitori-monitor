import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# =========================================================
# 基本設定
# =========================================================

JST = timezone(timedelta(hours=9))
STATE_FILE = Path("state.json")

# 対象商品
TARGETS = {
    "256GB": {
        "jan": "4549995734645",
        "model": "MJX74J/A",
    },
    "512GB": {
        "jan": "4549995734843",
        "model": "MJXC4J/A",
    },
    "1TB": {
        "jan": "4549995735048",
        "model": "MJXG4J/A",
    },
}

# 監視店舗
STORES = {
    "買取ソムリエ": "買取ソムリエ",
    "買取ホムラ": "買取ホムラ",
    "森森買取": "森森買取",
    "モバイルミックス": "モバイルミックス",
    "買取るでや": "買取ルデヤ",
    "買取一丁目": "買取１丁目",
}

# 買取サーチ
PRODUCT_URL = "https://kaitori-search.app/product/{jan}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    )
}

# 価格として認識する範囲
MIN_PRICE = 100_000
MAX_PRICE = 500_000


# =========================================================
# State
# =========================================================

def load_state():
    if not STATE_FILE.exists():
        return {
            "prices": {},
            "last_reports": {},
        }

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {
            "prices": {},
            "last_reports": {},
        }


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# =========================================================
# 価格解析
# =========================================================

def price_from_text(text):
    """
    以下のような価格を取得する。

    ¥259,000
    ￥259,000
    259,000円
    """

    patterns = [
        r"[¥￥]\s*([0-9]{1,3}(?:,[0-9]{3})+)",
        r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+)円",
    ]

    prices = []

    for pattern in patterns:
        matches = re.findall(pattern, text)

        for value in matches:
            try:
                price = int(value.replace(",", ""))

                if MIN_PRICE <= price <= MAX_PRICE:
                    prices.append(price)

            except ValueError:
                pass

    return prices


def normalize_store_name(name):
    """
    表記ゆれを吸収。
    """

    return (
        name
        .replace(" ", "")
        .replace("　", "")
        .replace("１", "1")
        .replace("一丁目", "1丁目")
    )


def store_name_matches(text, store_name):
    """
    店舗名の表記ゆれを考慮。
    """

    text_normalized = normalize_store_name(text)
    store_normalized = normalize_store_name(store_name)

    return store_normalized in text_normalized


# =========================================================
# 店舗価格取得
# =========================================================

def extract_store_price(soup, store_name, target):
    """
    買取サーチの比較表から、

    ・店舗
    ・容量
    ・バーガンディ

    が一致する行の価格だけを取得する。

    div全体を検索すると、複数店舗の価格を
    一緒に拾ってしまうため使用しない。
    """

    jan = TARGETS[target]["jan"]
    model = TARGETS[target]["model"]

    # -----------------------------------------------------
    # ① 比較表の tr を最優先
    # -----------------------------------------------------

    for row in soup.find_all("tr"):

        cells = row.find_all(["th", "td"])

        if not cells:
            continue

        cell_texts = [
            cell.get_text(" ", strip=True)
            for cell in cells
        ]

        row_text = " ".join(cell_texts)

        # 店舗名
        if not store_name_matches(row_text, store_name):
            continue

        # 対象容量
        if target not in row_text:
            continue

        # バーガンディ
        if "バーガンディ" not in row_text:
            continue

        # 価格取得
        prices = price_from_text(row_text)

        if prices:
            return prices[0]

    # -----------------------------------------------------
    # ② li / article の小さい要素
    # -----------------------------------------------------

    for tag in soup.find_all(["li", "article"]):

        text = tag.get_text(" ", strip=True)

        if not store_name_matches(text, store_name):
            continue

        if target not in text:
            continue

        if "バーガンディ" not in text:
            continue

        # JANまたは型番が存在する場合
        if jan not in text and model not in text:
            continue

        prices = price_from_text(text)

        if prices:
            return prices[0]

    # -----------------------------------------------------
    # ③ 店舗名周辺のテキストを確認
    # -----------------------------------------------------

    lines = [
        line.strip()
        for line in soup.get_text("\n").splitlines()
        if line.strip()
    ]

    for i, line in enumerate(lines):

        if not store_name_matches(line, store_name):
            continue

        # 店舗名の前後だけを見る
        start = max(0, i - 2)
        end = min(len(lines), i + 5)

        window = " ".join(lines[start:end])

        if target not in window:
            continue

        if "バーガンディ" not in window:
            continue

        prices = price_from_text(window)

        if prices:
            return prices[0]

    return None


# =========================================================
# 商品ページ取得
# =========================================================

def fetch_store(store, store_name):

    result = {}

    for capacity, info in TARGETS.items():

        url = PRODUCT_URL.format(
            jan=info["jan"]
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        price = extract_store_price(
            soup,
            store_name,
            capacity
        )

        result[capacity] = price

    return result


# =========================================================
# Discord
# =========================================================

def send_discord(content):

    webhook = os.getenv(
        "DISCORD_WEBHOOK",
        ""
    ).strip()

    if not webhook:
        print(
            "DISCORD_WEBHOOK is not set; "
            "notification skipped"
        )
        return False

    response = requests.post(
        webhook,
        json={
            "content": content
        },
        timeout=20
    )

    response.raise_for_status()

    return True


# =========================================================
# 表示
# =========================================================

def money(value):

    if isinstance(value, int):
        return f"{value:,}円"

    return "取得失敗"


def make_report(data, now):

    out = [
        "📊 iPhone 18 Pro Max 買取価格（新品未開封）",
        now.strftime("%Y/%m/%d %H:%M"),
        ""
    ]

    for capacity in TARGETS:

        out.append(
            f"【{capacity}・バーガンディ】"
        )

        values = []

        for store in STORES:

            price = (
                data
                .get(store, {})
                .get(capacity)
            )

            out.append(
                f"{store}：{money(price)}"
            )

            if isinstance(price, int):
                values.append(
                    (price, store)
                )

        if values:

            max_price = max(
                price
                for price, _ in values
            )

            winners = " / ".join(
                store
                for price, store in values
                if price == max_price
            )

            out.append(
                f"🏆 最高：{money(max_price)}"
                f"（{winners}）"
            )

        out.append("")

    return "\n".join(out)


# =========================================================
# メイン
# =========================================================

def main():

    now = datetime.now(JST)

    print(
        "========================================"
    )
    print(
        "iPhone 18 Pro Max 買取価格監視"
    )
    print(
        f"実行時刻：{now:%Y/%m/%d %H:%M:%S}"
    )
    print(
        "========================================"
    )

    state = load_state()

    current = {}

    errors = []

    # -----------------------------------------------------
    # 各店舗取得
    # -----------------------------------------------------

    for store, store_name in STORES.items():

        try:

            prices = fetch_store(
                store,
                store_name
            )

            current[store] = prices

            print(
                f"{store}: {prices}"
            )

            # 取得失敗を明示
            for capacity, price in prices.items():

                if price is None:
                    print(
                        f"  ⚠ {store} "
                        f"{capacity}: 取得失敗"
                    )

        except Exception as e:

            current[store] = {
                capacity: None
                for capacity in TARGETS
            }

            error_message = (
                f"{store}: {type(e).__name__}: {e}"
            )

            errors.append(
                error_message
            )

            print(
                f"❌ {error_message}"
            )

    # -----------------------------------------------------
    # 異常チェック
    # -----------------------------------------------------

    valid_prices = []

    for store in STORES:

        for capacity in TARGETS:

            price = (
                current
                .get(store, {})
                .get(capacity)
            )

            if isinstance(price, int):
                valid_prices.append(
                    price
                )

    # 全店舗が完全に同じ価格の場合は警告
    if len(valid_prices) >= 3:

        unique_prices = set(valid_prices)

        if len(unique_prices) <= 3:

            print(
                "⚠ 価格解析チェック："
                "複数店舗の価格が同一になっています。"
            )

            print(
                "   サイト側の価格が同一の場合もあるため、"
                "この警告だけではエラーとは判定しません。"
            )

    # -----------------------------------------------------
    # 価格UP検知
    # -----------------------------------------------------

    increases = []

    old_prices = state.get(
        "prices",
        {}
    )

    for store in STORES:

        for capacity in TARGETS:

            new_price = (
                current
                .get(store, {})
                .get(capacity)
            )

            key = (
                f"{store}|{capacity}"
            )

            previous_price = old_prices.get(
                key
            )

            # 前回→今回で上昇した場合のみ通知
            if (
                isinstance(previous_price, int)
                and isinstance(new_price, int)
                and new_price > previous_price
            ):

                increases.append(
                    (
                        store,
                        capacity,
                        previous_price,
                        new_price
                    )
                )

            # 有効な価格だけ保存
            if isinstance(new_price, int):

                old_prices[key] = new_price

    state["prices"] = old_prices

    # -----------------------------------------------------
    # 価格UP Discord通知
    # -----------------------------------------------------

    if increases:

        message = [
            "🔔 買取価格UP（新品未開封）",
            ""
        ]

        for (
            store,
            capacity,
            previous,
            new
        ) in increases:

            message.append(
                f"📱 iPhone 18 Pro Max "
                f"バーガンディ {capacity}"
            )

            message.append(
                f"{store}："
                f"{previous:,}円 → "
                f"{new:,}円 "
                f"(+{new - previous:,}円)"
            )

            message.append("")

        send_discord(
            "\n".join(message)
        )

    # -----------------------------------------------------
    # 日次レポート
    #
    # 13:00 / 15:00 / 18:00
    # GitHub Actionsの遅延を考慮して0～2分
    # -----------------------------------------------------

    if (
        now.hour in (13, 15, 18)
        and now.minute <= 2
    ):

        slot = (
            f"{now:%Y-%m-%d}-{now.hour}"
        )

        last_reports = state.setdefault(
            "last_reports",
            {}
        )

        if (
            last_reports.get(
                str(now.hour)
            )
            != slot
        ):

            report = make_report(
                current,
                now
            )

            send_discord(report)

            last_reports[
                str(now.hour)
            ] = slot

    # -----------------------------------------------------
    # State保存
    # -----------------------------------------------------

    save_state(state)

    # -----------------------------------------------------
    # エラー表示
    # -----------------------------------------------------

    if errors:

        print("")
        print(
            "⚠ 一部店舗で取得失敗"
        )

        for error in errors:
            print(
                f"- {error}"
            )

    print("")
    print(
        "========================================"
    )
    print(
        "監視処理完了"
    )
    print(
        "========================================"
    )


if __name__ == "__main__":
    main()