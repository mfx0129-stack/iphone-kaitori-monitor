# iPhone 18 Pro Max 買取価格監視

## 監視対象
- iPhone 18 Pro Max
- バーガンディ
- 256GB / 512GB / 1TB
- 新品未開封
- 2TBは対象外

## 監視店舗
- 買取ソムリエ
- 買取ホムラ
- 森森買取
- モバイルミックス
- 買取るでや（買取ルデヤ）
- 買取一丁目

## 通知
- 前回価格より上昇した場合：即時Discord通知
- 毎日13:00 / 15:00 / 18:00 JST：全商品の価格一覧をDiscord通知
- 値下げ・価格変化なし：上昇通知なし
- 初回取得：基準価格として保存し、上昇通知なし

## GitHub設定
1. `monitor.py`、`requirements.txt`、`.github/workflows/monitor.yml`、`state.json`をリポジトリに置く。
2. Settings → Secrets and variables → Actions → New repository secret
3. Name：`DISCORD_WEBHOOK`
4. Value：Discord Webhook URL
5. Actionsのworkflowを一度 `Run workflow` で実行。

GitHub Actionsは10分間隔で価格を確認します。13/15/18時の定時通知もこの実行から行います。

## 重要
価格取得元は、各買取店の価格を商品単位で比較掲載している「買取サーチ」の商品ページです。6店舗のうち、買取るでやは「買取ルデヤ」として照合します。各店舗公式ページはHTML構造がそれぞれ異なるため、共通の商品ページを利用して誤検知を減らしています。比較サイト側の更新遅延がある場合、その遅延はこの監視にも反映されます。
