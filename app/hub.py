"""WebSocket の接続管理とブロードキャスト．

WebSocket はサーバからの一斉配信専用である．クライアントからは何も受け取らない．
この分離により，再接続の処理が「繋ぎ直して受け直すだけ」になる．
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Hub:
    """接続中の WebSocket をまとめて保持する．"""

    def __init__(self) -> None:
        # WebSocket -> トークン．同じ人が複数タブを開くと複数の接続になるため，
        # 人数を数えるときはトークンで重複を除く．
        self._connections: dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket, token: str) -> None:
        await ws.accept()
        self._connections[ws] = token

    def disconnect(self, ws: WebSocket) -> None:
        # 切断の通知が二重に来ることがあるので，存在しなくてもエラーにしない．
        self._connections.pop(ws, None)

    def participant_count(self) -> int:
        """現在の接続人数（トークンで重複を除く）．

        発表者が「今何人見ているか」を把握するための数である．
        グラフの分母（延べの接続者数）とは定義が異なるので混同しないこと．

        投影用ビュー（/review）と発表者ビュー（/present）はトークンを持たずに
        接続する．これを数えると聴衆が1〜2人多く見えるため，空のトークンは除く．
        """
        return len(self.connected_tokens())

    def connected_tokens(self) -> set[str]:
        """現在接続している参加者のトークン．発表開始時に分母の初期値として使う．"""
        return {t for t in self._connections.values() if t}

    async def broadcast(self, message: dict) -> None:
        """全接続へ1つのメッセージを送る．

        送信に失敗した接続は既に切れているものとして取り除く．
        ここで例外を外に出すと，1台のスマホがスリープしただけで
        配信全体が止まってしまう．
        """
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception as exc:  # 切断の形は環境によって異なるため広く捕る
                logger.info("配信に失敗したため接続を破棄する: %s", exc)
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
