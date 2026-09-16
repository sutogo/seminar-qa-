"""データモデル．

全てプロセス内のメモリに保持する．データベースは使わない（ゼミ1回で完結するため）．
サーバを再起動すると全て消える．これは意図した仕様である．

匿名性について：
    Question と Confusion は投稿者のトークンを保持するが，
    **これをクライアントへのレスポンスに含めてはならない．**
    トークンと投稿の対応はこのプロセスの中だけに留める．
"""

from __future__ import annotations

import dataclasses
import time
from typing import Literal

# --------------------------------------------------------------------------
# 学年
# --------------------------------------------------------------------------

Grade = Literal["B3", "B4", "M1", "M2", "D1", "D2", "D3", "teacher"]

# 参加画面に出す順序でもある．
# D2・D3 に現在の在籍者はいないが，来年度以降のために含めている．
GRADES: tuple[Grade, ...] = ("B3", "B4", "M1", "M2", "D1", "D2", "D3", "teacher")

# バッジに出す文字列．教員だけ日本語にする．
GRADE_LABELS: dict[str, str] = {
    "B3": "B3", "B4": "B4",
    "M1": "M1", "M2": "M2",
    "D1": "D1", "D2": "D2", "D3": "D3",
    "teacher": "教員",
}

# FR-12 の丸め先．その発表で投稿者が1名しかいない学年はこれに置き換える．
ROUNDED_LABEL = "学生"

PresentationState = Literal["waiting", "live", "review", "closed"]


# --------------------------------------------------------------------------

@dataclasses.dataclass
class Participant:
    """参加者．氏名・学籍番号・IPアドレスは一切持たない．"""

    id: str
    token: str
    grade: Grade


@dataclasses.dataclass
class Question:
    """質問．`token` は投稿者の識別用で，外部には出さない．"""

    id: str
    presentation_id: str
    token: str
    grade: Grade
    body: str
    created_at: int  # 発表開始からの経過秒
    resolved: bool = False
    # 共感したトークンの集合．1トークン1票なので集合で持てば重複が防げる．
    empathy: set[str] = dataclasses.field(default_factory=set)

    @property
    def empathy_count(self) -> int:
        return len(self.empathy)


@dataclasses.dataclass
class Confusion:
    """「わからん」1回分．絶対時刻は保持せず，発表開始からの経過秒のみを持つ．"""

    token: str
    elapsed_sec: int


@dataclasses.dataclass
class Presentation:
    """発表1件．"""

    id: str
    session_id: str
    presenter: str
    title: str
    state: PresentationState = "waiting"
    started_at: float | None = None  # time.monotonic() の値
    ended_at: float | None = None
    confusions: list[Confusion] = dataclasses.field(default_factory=list)
    # FR-11 の分母．この発表中に一度でもWS接続したトークンの集合．
    # 現在の接続数ではなく延べの人数なので，スリープで切れた者も残る．
    connected_tokens: set[str] = dataclasses.field(default_factory=set)

    def elapsed_sec(self) -> int:
        """発表開始からの経過秒．未開始なら 0，終了後は発表の長さを返す．"""
        if self.started_at is None:
            return 0
        end = self.ended_at if self.ended_at is not None else time.monotonic()
        return int(end - self.started_at)

    def is_current(self) -> bool:
        """進行中（発表中または質疑中）か．"""
        return self.state in ("live", "review")


@dataclasses.dataclass
class Session:
    """ゼミ1回．サーバ起動時に1つだけ自動生成する．"""

    id: str
    title: str
    date: str
    # token -> Participant．トークンで引く場面しかないのでこの形にする．
    participants: dict[str, Participant] = dataclasses.field(default_factory=dict)
    presentations: list[Presentation] = dataclasses.field(default_factory=list)
