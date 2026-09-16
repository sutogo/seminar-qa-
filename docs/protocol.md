# 通信仕様

クライアントからサーバへの書き込みは REST，サーバからクライアントへの更新配信は WebSocket に分離する．
WebSocket はサーバ発の一方向であり，クライアントからは何も送信しない．

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

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/sessions` | `{title, date}` | `{session_id, join_url, qr_png_url}` |
| GET | `/api/sessions/{sid}` | — | `{session_id, title, participant_count, presentations:[...]}` |

### 参加

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/sessions/{sid}/participants` | `{grade}` | `{token}` |

`grade` は `B3` / `B4` / `M1` / `M2` / `D1` / `teacher` のいずれか．
`token` はランダムな32文字．クライアントは `localStorage` に保存し，以降の全ての POST に含める．

### 発表

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/sessions/{sid}/presentations` | `{presenter, title}` | `{presentation_id}` |
| POST | `/api/presentations/{pid}/start` | — | `204` |
| POST | `/api/presentations/{pid}/end` | — | `204` |
| POST | `/api/presentations/{pid}/close` | — | `{teams_posted: bool}` |

`end` で質疑モードへ移行．`close` で完了し，Teams への投稿を行う．

### 投稿

| メソッド | パス | 本文 | 応答 |
|---|---|---|---|
| POST | `/api/presentations/{pid}/confusion` | `{token}` | `204` |
| POST | `/api/presentations/{pid}/questions` | `{token, body}` | `{question_id}` |
| POST | `/api/questions/{qid}/empathy` | `{token}` | `{empathy_count}` |
| DELETE | `/api/questions/{qid}/empathy` | `{token}` | `{empathy_count}` |
| POST | `/api/questions/{qid}/resolve` | — | `204` |

制約：

- `confusion` は同一トークンから**30秒に1回**まで．超過分は `204` を返して黙って捨てる
- `body` は200字以内．超過は `400`
- `empathy` は1トークンにつき1質問1回まで．重複は現在値を返す
- `confusion` の記録値は発表開始からの経過秒（`elapsed_sec`）である．絶対時刻は保持しない

## WebSocket

接続先：`/ws?s={session_id}&t={token}`

サーバは接続直後に `state` と `questions` を1回ずつ送る．以降は変化時に該当メッセージを配信する．

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

### `questions`

質問の追加，共感の増減，消化状態の変更で送る．常に全件を送る（差分は送らない）．
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

`rate` は接続人数に対する割合．`peak` は `rate` が最大となる連続区間．

### 再接続

切断時は1秒後から指数的に間隔を延ばして再接続する（上限10秒）．
再接続に成功したらサーバが `state` と `questions` を再送するため，
クライアント側で差分を保持する必要はない．

## Teams 投稿

`close` 時に Power Automate Workflows の Webhook URL へ POST する．
ペイロードは Adaptive Card 形式（`MessageCard` は廃止済みのため使用不可）．

含める内容：

- 発表者名，発表タイトル，日時
- 参加人数，質問件数
- わからんが集中した時間帯（`peak`）と最大割合
- 質問一覧（共感数順，上位5件まで）

投稿の失敗はアプリの動作を止めない．失敗をログに残し，画面には影響させないこと．
