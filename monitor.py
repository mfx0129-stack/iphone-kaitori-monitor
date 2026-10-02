import json, os, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
STATE_FILE = Path('state.json')

TARGETS = {
    '256GB': {'jan': '4549995734645', 'model': 'MJX74J/A'},
    '512GB': {'jan': '4549995734843', 'model': 'MJXC4J/A'},
    '1TB':   {'jan': '4549995735048', 'model': 'MJXG4J/A'},
}

# 買取Xが各店舗の最新価格を掲載している店舗ページを利用。
# 店舗側のHTML変更に強くするため、JAN・型番・商品名の3条件で照合する。
STORES = {
    # 買取るでや = 買取ルデヤ
    '買取ソムリエ': '買取ソムリエ',
    '買取ホムラ': '買取ホムラ',
    '森森買取': '森森買取',
    'モバイルミックス': 'モバイルミックス',
    '買取るでや': '買取ルデヤ',
    '買取一丁目': '買取一丁目',
}

PRODUCT_URL = 'https://kaitori-search.app/product/{jan}'


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; KaitoriPriceMonitor/1.0; +https://github.com/)'
}


def load_state():
    if not STATE_FILE.exists():
        return {'prices': {}, 'last_reports': {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {'prices': {}, 'last_reports': {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def price_from_text(text):
    # 「¥290,500」「290,500円」などを拾う。相場価格を誤採用しないため、商品名直後の店舗価格を優先。
    vals = re.findall(r'(?:¥|￥)\s*([0-9]{2,3}(?:,[0-9]{3})+)|(?<![0-9])([0-9]{2,3}(?:,[0-9]{3})+)円', text)
    nums = []
    for a, b in vals:
        s = a or b
        try: nums.append(int(s.replace(',', '')))
        except ValueError: pass
    return nums


def extract_price(soup, target):
    # ページ本文を行単位で見て、対象商品の行から価格を取得。
    lines = [x.strip() for x in soup.get_text('\n').splitlines() if x.strip()]
    needles = [
        f"iPhone 18 Pro Max {target} バーガンディ",
        TARGETS[target]['jan'],
        TARGETS[target]['model'],
    ]
    for i, line in enumerate(lines):
        if all(n in line for n in [f"iPhone 18 Pro Max {target}", 'バーガンディ']):
            window = ' '.join(lines[i:i+4])
            nums = price_from_text(window)
            # 商品価格と相場価格が並ぶ場合、ページ構造では店舗価格が後ろに出る。
            if nums:
                return nums[-1]
    # 型番/JANが同じ行にある場合のフォールバック
    for i, line in enumerate(lines):
        if TARGETS[target]['jan'] in line or TARGETS[target]['model'] in line:
            window = ' '.join(lines[max(0,i-1):i+4])
            if 'バーガンディ' in window:
                nums = price_from_text(window)
                if nums:
                    return nums[-1]
    return None


def extract_store_price(soup, store_name, target):
    text = soup.get_text(' ', strip=True)
    # 商品ページは「店舗名 + 価格」が表形式で掲載されるため、
    # 行・セル単位を優先し、HTML構造が変わった場合は周辺テキストへフォールバック。
    for row in soup.find_all(['tr', 'li', 'article', 'div']):
        t = row.get_text(' ', strip=True)
        if store_name in t and TARGETS[target]['jan'] in t:
            nums = price_from_text(t)
            if nums:
                return nums[-1]
    # JANが行に出ない構造へのフォールバック
    for row in soup.find_all(['tr', 'li', 'article', 'div']):
        t = row.get_text(' ', strip=True)
        if store_name in t and 'iPhone 18 Pro Max' in t and target in t and 'バーガンディ' in t:
            nums = price_from_text(t)
            if nums:
                return nums[-1]
    return None


def fetch_store(store, store_name):
    result = {}
    for cap, info in TARGETS.items():
        url = PRODUCT_URL.format(jan=info['jan'])
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        result[cap] = extract_store_price(soup, store_name, cap)
    return result


def send_discord(content):
    webhook = os.getenv('DISCORD_WEBHOOK', '').strip()
    if not webhook:
        print('DISCORD_WEBHOOK is not set; notification skipped')
        return False
    r = requests.post(webhook, json={'content': content}, timeout=20)
    r.raise_for_status()
    return True


def money(v):
    return f'{v:,}円' if isinstance(v, int) else '取得失敗'


def make_report(data, now):
    out = [f'📊 iPhone 18 Pro Max 買取価格（新品未開封）\\n{now.strftime("%Y/%m/%d %H:%M")}']
    for cap in TARGETS:
        out.append(f'\\n【{cap}・バーガンディ】')
        vals = []
        for store in STORES:
            p = data.get(store, {}).get(cap)
            out.append(f'{store}：{money(p)}')
            if isinstance(p, int): vals.append((p, store))
        if vals:
            mx = max(x[0] for x in vals)
            winners = ' / '.join(s for p,s in vals if p == mx)
            out.append(f'🏆 最高：{money(mx)}（{winners}）')
    return '\n'.join(out)


def main():
    now = datetime.now(JST)
    state = load_state()
    current = {}
    errors = []

    for store, store_name in STORES.items():
        try:
            current[store] = fetch_store(store, store_name)
            print(store, current[store])
        except Exception as e:
            current[store] = {cap: None for cap in TARGETS}
            errors.append(f'{store}: {e}')

    # 上昇通知：前回取得値より上がったものだけ。
    increases = []
    old = state.get('prices', {})
    for store in STORES:
        for cap in TARGETS:
            new = current.get(store, {}).get(cap)
            key = f'{store}|{cap}'
            prev = old.get(key)
            if isinstance(prev, int) and isinstance(new, int) and new > prev:
                increases.append((store, cap, prev, new))
            if isinstance(new, int):
                old[key] = new
    state['prices'] = old

    if increases:
        msg = ['🔔 買取価格UP（新品未開封）']
        for store, cap, prev, new in increases:
            msg.append(f'📱 iPhone 18 Pro Max バーガンディ {cap}')
            msg.append(f'{store}：{prev:,}円 → {new:,}円（+{new-prev:,}円）')
        send_discord('\n'.join(msg))

    # 13:00 / 15:00 / 18:00 JST の日次レポート。GitHub Actionsの遅延を考慮して±2分。
    if now.hour in (13, 15, 18) and now.minute <= 2:
        slot = f'{now:%Y-%m-%d}-{now.hour}'
        if state.get('last_reports', {}).get(str(now.hour)) != slot:
            send_discord(make_report(current, now))
            state.setdefault('last_reports', {})[str(now.hour)] = slot

    save_state(state)

    if errors:
        print('取得失敗:', *errors, sep='\n- ')
        # 一部店舗の取得失敗ではジョブ全体を失敗にしない。


if __name__ == '__main__':
    main()
