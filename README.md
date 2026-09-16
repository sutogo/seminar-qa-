# ゼミ質疑支援アプリ

研究室のゼミ（中間発表・期末発表レビュー）で使う質疑支援 Web アプリ．
聴衆がスマートフォンから匿名で質問を投稿し，質疑の場で共感数順に消化する．

## Claude Code で作業を始める前に

`CLAUDE.md` を先に読むこと．技術構成の確定事項と禁止事項が書いてある．

## 文書

| ファイル | 内容 |
|---|---|
| `CLAUDE.md` | Claude Code への指示．制約・方針・実装順序 |
| `docs/requirements.md` | 要件定義書 |
| `docs/protocol.md` | REST / WebSocket の通信仕様 |
| `docs/ui-guidelines.md` | 配色・フォント・アニメーション仕様 |
| `docs/wireframe.html` | 画面ワイヤーフレーム（ブラウザで開く） |

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 起動

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

`--host 0.0.0.0` は必須．これがないとスマートフォンから接続できない．

サーバPCのIPアドレスを確認する：

```bash
# macOS / Linux
ipconfig getifaddr en0 || hostname -I
# Windows
ipconfig
```

参加者は `/present` に表示されるQRコードを読んで参加する．
参加用URLは `http://<サーバPCのIP>:8000/join?s=<session_id>` の形だが，手入力は想定しない．

セッションはサーバ起動時に自動生成されるため，作成操作は不要である．

**`/present` は必ずLAN側のIPアドレスで開くこと．** QRのホスト名はアクセスに使われた
URLから組み立てるため，`http://localhost:8000/present` で開くと
QRも `localhost` を指し，スマートフォンから接続できない．

## 検証用のダミー参加者

一人では複数人前提の機能を確認できないため，ダミーの参加者を流す．

```bash
python tools/fake_audience.py --session <session_id> --count 12
```

## 当日の進行

1. サーバを起動する（セッションは自動生成される）
2. スクリーンにQRコードを投影して参加者に読ませる
3. 発表者が `/present` を開き「発表開始」
4. 発表終了後，スクリーンを `/review` に切り替えて質疑
5. 「質疑終了」で Teams へ投稿．次の発表者に交代

## 既知の前提

- 学内 Wi-Fi のクライアント間通信が遮断されていると動作しない．事前に確認すること
- Teams 連携には Power Automate Workflows の Webhook URL が必要
  （環境変数 `TEAMS_WEBHOOK_URL` に設定する）
