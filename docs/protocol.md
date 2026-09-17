# 通信仕様

クライアントからサーバへの書き込みは REST，サーバからクライアントへの更新配信は WebSocket に分離する．
WebSocket はサーバ発の一方向であり，クライアントからは何も送信しない．

版：0.4 ／ 2026-09-16

0.3 からの変更点：記録の生成と配送を分離し，Teams 投稿を任意とした（§記録の出力）．
`close` の応答に `record_md` を追加．学年に `D2` / `D3` を追加．

0.2 からの変更点：QR画像のエンドポイントを追加，セッションの起動時自動生成を明記，
`participant_count` とグラフの分母の定義を分離，発表中は `questions` を配信しない方針へ変更，
消化の取り消しを追加．

## 状態遷移

発表（Presentation）は次の4状態を持つ．

```
待機(waiting) ──開始──▶ 発表中(live) ──終了──▶ 質疑中(review) ──質疑終了──▶ 完了(closed)
```

- 同一セッション内で `live` または `review` の発表は**同時に1つまで**
- 新しい発表が `start` されたとき，未完了の発表があれば自動的に `closed` にする

## REST API

全て JSON．参加者を伴う操作は本文に `token` を含める．

### セッション

**セッションはサーバ起動時に1つだけ自動生成する．**
ゼミ1回で完結し，データをプロセス内に保持する以上，1プロセス＝1セッションで足りる．
セッションを選択・作成する画面は設けない．タイトルと日付は起動時の引数で与える．

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/sessions` | `{title, date}` | `{session_id, join_url, qr_png_url}` |
| GET | `/api/sessions/current` | — | `{session_id, title, participant_count, presentations:[...]}` |
| GET | `/api/sessions/{sid}` | — | 同上 |
| GET | `/api/sessions/{sid}/qr.png` | — | `image/png`（参加用QR） |

`POST /api/sessions` は仕様として残すが，本版の画面からは呼ばない．

`join_url` と `qr_png_url` のホスト名は，**リクエストの `Host` ヘッダから組み立てる．**
発表者が `http://192.168.1.5:8000/present` を開けば QR も同じIPを指すため，
IPアドレスをコードや設定に書く必要がない．

**ただし到達できないアドレスで開くと，QR も同じ宛先を指す．**
`Host` が `0.0.0.0` / `localhost` / `127.0.0.1` / `::1` の場合は，
サーバがこのPCのLAN側IPアドレスへ置き換える．
特に `0.0.0.0` は uvicorn の起動メッセージに出るため，そのまま開かれやすい．
置き換えられなかった場合は画面に警告を出すこと．
置き換えが起きた場合も，別経路のIPが選ばれている可能性があるため画面に知らせる．

### 参加

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/sessions/{sid}/participants` | `{grade}` | `{token}` |

`grade` は `B3` / `B4` / `M1` / `M2` / `D1` / `D2` / `D3` / `teacher` のいずれか．
`token` はランダムな32文字．クライアントは `localStorage` に保存し，以降の全ての POST に含める．

### 発表

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/sessions/{sid}/presentations` | `{presenter, title}` | `{presentation_id}` |
| POST | `/api/presentations/{pid}/start` | — | `204` |
| POST | `/api/presentations/{pid}/end` | — | `204` |
| POST | `/api/presentations/{pid}/close` | — | `{teams_posted: bool, record_md: string}` |

`end` で質疑モードへ移行．`close` で完了し，Teams への投稿を行う．

### 投稿

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/presentations/{pid}/confusion` | `{token}` | `204` |
| POST | `/api/presentations/{pid}/questions` | `{token, body}` | `{question_id}` |
| POST | `/api/questions/{qid}/empathy` | `{token}` | `{empathy_count}` |
| DELETE | `/api/questions/{qid}/empathy` | `{token}` | `{empathy_count}` |
| POST | `/api/questions/{qid}/resolve` | — | `204` |
| DELETE | `/api/questions/{qid}/resolve` | — | `204` |

制約：

- `confusion` は同一トークンから**30秒に1回**まで．超過分は `204` を返して黙って捨てる
- `body` は200字以内．超過は `400`
- `empathy` は1トークンにつき1質問1回まで．重複は現在値を返す
- `confusion` の記録値は発表開始からの経過秒（`elapsed_sec`）である．絶対時刻は保持しない
- `resolve` はべき等．投影中の誤操作を戻せるよう，`DELETE` で消化を解除できる
- `DELETE /api/questions/{qid}/empathy` は本文に `token` を伴う．
  ブラウザの `fetch` は本文付きの `DELETE` を送れるが，
  ライブラリによっては簡易メソッドが本文を落とす（httpx の `delete()` など）．
  `api.js` では `fetch(url, {method:"DELETE", body})` を直接使うこと

## WebSocket

接続先：`/ws?s={session_id}&t={token}`

サーバは接続直後に `state` を送る．
`questions` は**質疑中（`review`）に接続した場合のみ**あわせて送る（理由は後述）．
以降は変化時に該当メッセージを配信する．

### `state`

発表の状態が変わったとき，および参加者数が変わったときに送る．
クライアントはこれを受けて画面モードを切り替える．

```json
{
  "type": "state",
  "participant_count": 12,
  "presentation": {
    "id": "p_01",
    "presenter": "渡嘉敷 浩祐",
    "title": "TKA術中荷重計測システムの構築",
    "state": "live",
    "elapsed_sec": 492,
    "question_count": 7
  }
}
```

`presentation` は待機中の発表しかない場合 `null`．

`participant_count` は**現在のWS接続数**である（トークンで重複排除する）．
席を外した者や，スリープで切断された者は含まれない．
発表者が「今何人見ているか」を把握するための数であり，
`chart` の `rate` の分母とは**定義が異なる**．

### `questions`

**発表中（`live`）は配信しない．** 質疑モードへ移行した時点で初回を配信する．
発表中に質問の本文を参加者の端末へ届けないための措置である．
クライアント側で描画を抑止するのではなく，**サーバが送らないこと**で担保する．
参加者自身の投稿一覧は `POST /api/presentations/{pid}/questions` の応答から
クライアントが組み立て，`localStorage` に保持する．

質疑中は，質問の追加，共感の増減，消化状態の変更で送る．常に全件を送る（差分は送らない）．
並び順はサーバ側で確定させる：**共感数の降順 → 投稿時刻の昇順**．
`resolved` が真のものは末尾へ回す．

```json
{
  "type": "questions",
  "items": [
    {
      "id": "q_03",
      "grade_label": "B4",
      "body": "荷重バランスの算出式が追えませんでした．どの座標系で見ていますか",
      "empathy_count": 8,
      "resolved": false,
      "created_at": 312
    }
  ]
}
```

`grade_label` は**丸め処理を適用した後**の表示用文字列である．
その発表で投稿者が1名しかいない学年は `"学生"` に置き換える．
`teacher` は常に `"教員"` と表示し，丸めの対象外とする．
生の `grade` をクライアントに送ってはならない．

### `chart`

質疑モードへの移行時に1回送る．発表中は送らない．

```json
{
  "type": "chart",
  "bucket_sec": 30,
  "buckets": [
    {"t": 0,  "count": 0, "rate": 0.00},
    {"t": 30, "count": 1, "rate": 0.08},
    {"t": 60, "count": 5, "rate": 0.42}
  ],
  "peak": {"start_sec": 420, "end_sec": 570, "rate": 0.67}
}
```

`rate` の分母は，**その発表中に一度でもWS接続したユニークなトークン数**である．
発表開始時に集合をクリアし，接続のたびにトークンを加える．
スリープで切断された者も数に残り，途中からの入場も拾える．

`state.participant_count`（現在の接続数）とは定義が異なるため，
画面上も「接続中 N」「参加 N 名中」のように書き分けること．

`peak` は `rate` が最大となる連続区間．

### 再接続

切断時は1秒後から指数的に間隔を延ばして再接続する（上限10秒）．
再接続に成功したらサーバが `state`（質疑中であれば `questions` も）を再送するため，
クライアント側で差分を保持する必要はない．
ただし発表中の自分の投稿一覧のみは，サーバから再送されないため `localStorage` から復元する．

## 記録の出力

`close` 時に，発表1件分の記録を Markdown で生成する．
**記録の生成と配送を分離する．** 配送手段（Teams）が使えない場合でも記録は必ず残る．

含める内容：

- 発表者名，発表タイトル，日時
- 参加人数（その発表中に接続した人数），質問件数
- わからんが集中した時間帯（`peak`）と最大割合
- 質問一覧（共感数順，上位5件まで）

### 画面への表示（既定．常に行う）

`close` の応答に記録の Markdown を `record_md` として含める．
`/review` は「記録をコピー」ボタンを表示し，発表者が任意の場所
（Teams のチャット，OneNote 等）へ自分で貼る．
外部への通信も，Teams の権限も要らない．

### Teams への投稿（任意）

環境変数 `TEAMS_WEBHOOK_URL` が設定されている場合に限り，
同じ内容を Power Automate Workflows の Webhook へ POST する．
未設定であれば投稿を試みず，`teams_posted` は `false` とする．

ペイロードは Adaptive Card 形式（`MessageCard` は廃止済みのため使用不可）．
Workflows はカードを `attachments` で包んだ次の形を要求する．

```json
{
  "type": "message",
  "attachments": [
    {
      "contentType": "application/vnd.microsoft.card.adaptive",
      "content": { "type": "AdaptiveCard", "version": "1.4", "body": [] }
    }
  ]
}
```

投稿の失敗はアプリの動作を止めない．失敗をログに残し，画面には影響させないこと．
`record_md` は投稿の成否にかかわらず必ず返す．
