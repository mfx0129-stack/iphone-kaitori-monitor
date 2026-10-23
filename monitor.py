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

# =========================================================
# Discordテスト通知
# =========================================================
# True  = テスト通知を送る
# False = テスト通知を送らない
#
# テストが終わったら False にしてください
TEST_DISCORD = False


# =========================================================
# 対象商品
# =========================================================

TARGETS = {
    "256GB": {
        "jan": "4549995734645",
        "model": "MJX74J/A",
        "retail_price": 239800,
    },
    "512GB": {
        "jan": "4549995734843",
        "model": "MJXC4J/A",
        "retail_price": 274800,
    },
    "1TB": {
        "jan": "4549995735048",
        "model": "MJXG4J/A",
        "retail_price": 344800,
    },
    "Duo 256GB スターホワイト": {
        "jan": "4549995732641",
        "model": "MK244J/A",
        "retail_price": 364800,
    },
    "Duo 256GB ナイトスカイ": {
        "jan": "4549995732696",
        "model": "MK254J/A",
        "retail_price": 364800,
    },
    "Duo 512GB スターホワイト": {
        "jan": "4549995732740",
        "model": "MK264J/A",
        "retail_price": 399800,
    },
    "Duo 512GB ナイトスカイ": {
        "jan": "4549995732795",
        "model": "MK274J/A",
        "retail_price": 399800,
    },
    "Duo 1TB スターホワイト": {
        "jan": "4549995732849",
        "model": "MK284J/A",
        "retail_price": 469800,
    },
    "Duo 1TB ナイトスカイ": {
        "jan": "4549995732894",
        "model": "MK294J/A",
        "retail_price": 469800,
    },
}


# =========================================================
# 監視店舗
# =========================================================

STORES = {
    "買取ソムリエ": "買取ソムリエ",
    "買取ホムラ": "買取ホムラ",
    "森森買取": "森森買取",
    "モバイルミックス": "モバイルミックス",
    "買取るでや": "買取ルデヤ",
    "買取一丁目": "買取１丁目",
}


# =========================================================
# 買取サーチ
# =========================================================

PRODUCT_URL = "https://kaitori-search.app/product/{jan}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    )
}


# =========================================================
# 価格として認識する範囲
# =========================================================

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
            STATE_FILE.read_text(
                encoding="utf-8"
            )
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

    patterns = [
        r"[¥￥]\s*([0-9]{1,3}(?:,[0-9]{3})+)",
        r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+)円",
    ]

    prices = []

    for pattern in patterns:

        matches = re.findall(
            pattern,
            text
        )

        for value in matches:

            try:

                price = int(
                    value.replace(",", "")
                )

                if (
                    MIN_PRICE
                    <= price
                    <= MAX_PRICE
                ):
                    prices.append(price)

            except ValueError:
                pass

    return prices


# =========================================================
# 店舗名の表記ゆれ対策
# =========================================================

def normalize_store_name(name):

    return (
        name
        .replace(" ", "")
        .replace("　", "")
        .replace("１", "1")
        .replace("一丁目", "1丁目")
    )


def store_name_matches(
    text,
    store_name
):

    text_normalized = normalize_store_name(
        text
    )

    store_normalized = normalize_store_name(
        store_name
    )

    return (
        store_normalized
        in text_normalized
    )


# =========================================================
# 店舗価格取得
# =========================================================

def extract_store_price(
    soup,
    store_name,
    target
):

    # ① table / tr
    for row in soup.find_all("tr"):

        cells = row.find_all(
            ["th", "td"]
        )

        if not cells:
            continue

        cell_texts = [
            cell.get_text(
                " ",
                strip=True
            )
            for cell in cells
        ]

        row_text = " ".join(
            cell_texts
        )

        if not store_name_matches(
            row_text,
            store_name
        ):
            continue

        prices = price_from_text(
            row_text
        )

        if prices:
            return prices[0]

    # ② li / article
    for tag in soup.find_all(
        ["li", "article"]
    ):

        text = tag.get_text(
            " ",
            strip=True
        )

        if not store_name_matches(
            text,
            store_name
        ):
            continue

        prices = price_from_text(
            text
        )

        if prices:
            return prices[0]

    # ③ テキスト行
    lines = [
        line.strip()
        for line in soup.get_text(
            "\n"
        ).splitlines()
        if line.strip()
    ]

    for i, line in enumerate(lines):

        if not store_name_matches(
            line,
            store_name
        ):
            continue

        start = max(
            0,
            i - 1
        )

        end = min(
            len(lines),
            i + 5
        )

        window = " ".join(
            lines[start:end]
        )

        prices = price_from_text(
            window
        )

        if prices:
            return prices[0]

    return None


# =========================================================
# 商品ページ取得
# =========================================================

def fetch_store(
    store,
    store_name
):

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
# 金額表示
# =========================================================

def money(value):

    if isinstance(value, int):
        return f"{value:,}円"

    return "掲載無し"


# =========================================================
# 定価との差額
# =========================================================

def price_difference(
    price,
    capacity
):

    if not isinstance(price, int):
        return "掲載無し"

    retail_price = TARGETS[
        capacity
    ]["retail_price"]

    difference = (
        price
        - retail_price
    )

    if difference >= 0:
        return f"+{difference:,}円"

    return f"{difference:,}円"


# =========================================================
# 日次レポート
# =========================================================

def make_report(
    data,
    now
):

    out = [
        "📊 iPhone 18 Pro Max / iPhone Duo 買取価格",
        "新品未開封・Pro Max バーガンディ / Duo 全2色",
        now.strftime(
            "%Y/%m/%d %H:%M"
        ),
        "",
    ]

    for capacity in TARGETS:

        retail_price = TARGETS[
            capacity
        ]["retail_price"]

        out.append(
            f"【{capacity if capacity.startswith('Duo') else 'Pro Max ' + capacity}】"
        )

        out.append(
            f"Apple定価："
            f"{retail_price:,}円"
        )

        values = []

        for store in STORES:

            price = (
                data
                .get(store, {})
                .get(capacity)
            )

            if isinstance(price, int):

                diff = price_difference(
                    price,
                    capacity
                )

                out.append(
                    f"{store}："
                    f"{price:,}円 "
                    f"（定価差額 {diff}）"
                )

                values.append(
                    (price, store)
                )

            else:

                out.append(
                    f"{store}：掲載無し"
                )

        # 最高価格
        if values:

            max_price = max(
                price
                for price, store
                in values
            )

            winners = " / ".join(
                store
                for price, store
                in values
                if price == max_price
            )

            max_diff = price_difference(
                max_price,
                capacity
            )

            out.append(
                f"🏆 最高："
                f"{max_price:,}円 "
                f"（定価差額 {max_diff}）"
            )

            out.append(
                f"店舗：{winners}"
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
        "iPhone 18 Pro Max / Duo 買取価格監視"
    )

    print(
        f"実行時刻："
        f"{now:%Y/%m/%d %H:%M:%S}"
    )

    print(
        "========================================"
    )

    # =====================================================
    # Discordテスト通知
    # =====================================================

    if TEST_DISCORD:

        print(
            "Discordテスト通知を送信します"
        )

        send_discord(
            "🔔 Discordテスト通知\n"
            "iPhone買取価格監視システムは"
            "正常にDiscordへ接続されています。"
        )

    state = load_state()

    current = {}

    errors = []

    # =====================================================
    # 各店舗の価格取得
    # =====================================================

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

            for capacity, price in prices.items():

                if price is None:

                    print(
                        f"  - {store} "
                        f"{capacity}: 掲載無し"
                    )

        except Exception as e:

            current[store] = {
                capacity: None
                for capacity in TARGETS
            }

            error_message = (
                f"{store}: "
                f"{type(e).__name__}: {e}"
            )

            errors.append(
                error_message
            )

            print(
                f"❌ {error_message}"
            )

    # =====================================================
    # 価格UP検知
    # =====================================================

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

            previous_price = (
                old_prices.get(key)
            )

            if (
                isinstance(
                    previous_price,
                    int
                )
                and isinstance(
                    new_price,
                    int
                )
                and new_price
                > previous_price
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
            if isinstance(
                new_price,
                int
            ):

                old_prices[key] = new_price

    state["prices"] = old_prices

    # =====================================================
    # 日次レポート
    # =====================================================

    if now.hour in (13, 15, 18):

        slot = (
            f"{now:%Y-%m-%d}"
            f"-{now.hour}"
        )

        last_reports = state.setdefault(
            "last_reports",
            {}
        )

        hour_key = str(now.hour)

        if (
            last_reports.get(
                hour_key
            )
            != slot
        ):

            report = make_report(
                current,
                now
            )

            send_discord(
                report
            )

            last_reports[
                hour_key
            ] = slot

    # =====================================================
    # State保存
    # =====================================================

    save_state(state)

    # =====================================================
    # エラー表示
    # =====================================================

    if errors:

        print("")
        print(
            "⚠ 一部店舗で取得エラー"
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