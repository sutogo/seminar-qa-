#!/usr/bin/env python3
"""ダミーの参加者を生成する検証用スクリプト．

**一人ではテストできない機能がある．**
共感による並び替えも，わからん率のグラフも，複数人が同時に触らないと
動作を確認できない．スマートフォン1台では質問1件・共感1票の状態しか作れず，
投影したときのレイアウトの破綻も発見できない．

このスクリプトは指定した人数分の参加者を作り，WebSocket で接続したうえで，
発表中はランダムな間隔で「わからん」と質問を送り，
質疑中は質問にランダムに共感する．

使い方：

    python tools/fake_audience.py                    # 12人を 127.0.0.1:8000 へ
    python tools/fake_audience.py --count 20         # 20人
    python tools/fake_audience.py --host 192.168.1.5:8000
    python tools/fake_audience.py --watch            # 受信したメッセージを全て表示

`--watch` は段階1の確認用である．状態の変更が全端末に届いているかを，
画面が無くても目で確かめられる．

依存は httpx と websockets（uvicorn[standard] に含まれる）のみ．
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys

import httpx
from websockets.asyncio.client import connect

# 学年の分布．研究室の実態に寄せて，B4 と M1 を厚くする．
GRADE_POOL = ["B3", "B3", "B4", "B4", "B4", "M1", "M1", "M1", "M2", "M2", "D1", "teacher"]

# ダミーの質問文．長さがまちまちなものを混ぜ，投影時の折り返しを確認できるようにする．
SAMPLE_QUESTIONS = [
    "荷重バランスの算出式が追えませんでした．どの座標系で見ていますか",
    "被験者数は何名ですか",
    "既存の術中計測機器と比べた利点を教えてください",
    "センサの校正はどのくらいの頻度で必要ですか",
    "再現性の検証はどのように行いましたか",
    "スフェロイドの培養期間はどれくらいでしょうか",
    "この手法を実際の手術室で使う場合，滅菌はどうなりますか",
    "統計処理に使った検定の選択理由が知りたいです",
    "先行研究との差分がよく分かりませんでした",
    "グラフの縦軸の単位は何ですか",
    "今後どのあたりを詰めていく予定ですか",
    "サンプル間のばらつきが大きく見えますが，原因の見当はついていますか",
]


class Fake:
    """ダミー参加者1人分．"""

    def __init__(self, index: int, base: str, session_id: str, watch: bool) -> None:
        self.index = index
        self.base = base
        self.session_id = session_id
        self.watch = watch
        self.grade = random.choice(GRADE_POOL)
        self.token = ""
        # 直近に受け取った発表の状態．送信の判断に使う．
        self.presentation_id: str | None = None
        self.presentation_state: str = "waiting"
        # 既に共感した質問．二重に送らないために覚えておく．
        self.empathized: set[str] = set()
        self.asked = 0
        # 受信した questions メッセージから拾った質問ID．共感の対象に使う．
        self.known_question_ids: list[str] = []

    def log(self, text: str) -> None:
        print(f"  [{self.index:2d}:{self.grade:<7}] {text}", flush=True)

    async def join(self, client: httpx.AsyncClient) -> None:
        """参加してトークンを得る．"""
        r = await client.post(
            f"/api/sessions/{self.session_id}/participants",
            json={"grade": self.grade},
        )
        r.raise_for_status()
        self.token = r.json()["token"]

    async def run(self, client: httpx.AsyncClient) -> None:
        """WebSocket に繋ぎ，受信と送信を並行して回す．"""
        url = f"ws://{self.base}/ws?s={self.session_id}&t={self.token}"
        async with connect(url) as ws:
            await asyncio.gather(
                self._receive_loop(ws),
                self._send_loop(client),
            )

    async def _receive_loop(self, ws) -> None:
        """サーバからの配信を受け取り続ける．こちらからは何も送らない．"""
        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get("type")

            if kind == "state":
                pres = msg.get("presentation")
                before = self.presentation_state
                if pres is None:
                    self.presentation_id, self.presentation_state = None, "waiting"
                else:
                    self.presentation_id = pres["id"]
                    self.presentation_state = pres["state"]
                # 状態が変わったときだけ出す．人数の変化で毎回出すと流れてしまう．
                if self.watch or before != self.presentation_state:
                    self.log(
                        f"state {before} -> {self.presentation_state}"
                        f"（接続 {msg['participant_count']}人）"
                    )
            elif kind == "questions":
                self.known_question_ids = [item["id"] for item in msg["items"]]
                if self.watch:
                    self.log(f"questions {len(msg['items'])}件")
            elif kind == "chart" and self.watch:
                peak = msg.get("peak")
                self.log(f"chart {len(msg['buckets'])}区間 peak={peak}")

    async def _send_loop(self, client: httpx.AsyncClient) -> None:
        """発表の状態に応じて投稿する．"""
        while True:
            await asyncio.sleep(random.uniform(3, 12))
            pid = self.presentation_id
            if pid is None:
                continue

            try:
                if self.presentation_state == "live":
                    await self._act_live(client, pid)
                elif self.presentation_state == "review":
                    await self._act_review(client, pid)
            except httpx.HTTPError as exc:
                # 1人が転んでも他の参加者は動かし続ける．
                self.log(f"送信に失敗した: {exc}")

    async def _act_live(self, client: httpx.AsyncClient, pid: str) -> None:
        """発表中：主に「わからん」，たまに質問．"""
        if random.random() < 0.65:
            await client.post(
                f"/api/presentations/{pid}/confusion", json={"token": self.token}
            )
            self.log("わからん")
        elif self.asked < 2:
            body = random.choice(SAMPLE_QUESTIONS)
            r = await client.post(
                f"/api/presentations/{pid}/questions",
                json={"token": self.token, "body": body},
            )
            if r.status_code == 201:
                self.asked += 1
                self.log(f"質問 {r.json()['question_id']}：{body[:24]}…")

    async def _act_review(self, client: httpx.AsyncClient, pid: str) -> None:
        """質疑中：まだ共感していない質問に共感する．

        対象は WebSocket で受け取った questions メッセージから拾う．
        送信のためにサーバへ問い合わせ直す必要はない．
        """
        candidates = [
            q for q in self.known_question_ids if q not in self.empathized
        ]
        if not candidates:
            return
        qid = random.choice(candidates)
        res = await client.post(
            f"/api/questions/{qid}/empathy", json={"token": self.token}
        )
        if res.status_code == 200:
            self.empathized.add(qid)
            self.log(f"共感 {qid}（計 {res.json()['empathy_count']}）")


async def main() -> int:
    parser = argparse.ArgumentParser(description="ダミー参加者を生成する")
    parser.add_argument("--host", default="127.0.0.1:8000",
                        help="サーバの host:port（既定 127.0.0.1:8000）")
    parser.add_argument("--session", default="",
                        help="セッションID（既定：サーバから自動取得する）")
    parser.add_argument("--count", type=int, default=12, help="人数（既定 12）")
    parser.add_argument("--watch", action="store_true",
                        help="受信したメッセージを全て表示する（段階1の確認用）")
    args = parser.parse_args()

    base_url = f"http://{args.host}"
    print(f"\n  {base_url} へ {args.count} 人のダミー参加者を接続する\n")

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        # セッションIDは起動ごとに変わるため，既定ではサーバに問い合わせる．
        session_id = args.session
        if not session_id:
            try:
                r = await client.get("/api/sessions/current")
                r.raise_for_status()
                session_id = r.json()["session_id"]
            except httpx.HTTPError as exc:
                print(f"  サーバに繋がらない：{exc}\n")
                return 1
            print(f"  セッション {session_id} に参加する")

        fakes = [Fake(i + 1, args.host, session_id, args.watch)
                 for i in range(args.count)]

        # 参加登録．ここで失敗するならサーバかセッションIDが違う．
        try:
            await asyncio.gather(*(f.join(client) for f in fakes))
        except httpx.HTTPError as exc:
            print(f"  参加に失敗した：{exc}")
            print("  サーバが起動しているか，--session のIDが正しいかを確認する．\n")
            return 1

        print(f"  {len(fakes)} 人が参加した．Ctrl-C で終了する．\n")
        try:
            await asyncio.gather(*(f.run(client) for f in fakes))
        except asyncio.CancelledError:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n  終了した．\n")
