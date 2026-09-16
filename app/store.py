"""インメモリの状態保持と操作．

このアプリの状態は全てここに集約する．main.py は HTTP と WebSocket の
入出力だけを担当し，状態を直接いじらない．

排他制御は行っていない．FastAPI のイベントループは単一スレッドで回り，
以下のメソッドは全て同期関数（await を挟まない）なので，
処理の途中で他のリクエストが割り込むことはない．
"""

from __future__ import annotations

import secrets
import time

from .models import (
    GRADE_LABELS,
    ROUNDED_LABEL,
    Confusion,
    Grade,
    Participant,
    Presentation,
    Question,
    Session,
)

# 「わからん」の受付間隔．同一トークンからこの秒数に1回だけ受け付ける．
CONFUSION_INTERVAL_SEC = 30
# グラフの集計幅．
BUCKET_SEC = 30
# 質問本文の上限．
BODY_MAX_LEN = 200


class Store:
    """セッション1つ分の状態．"""

    def __init__(self, title: str, date: str) -> None:
        self.session = Session(id="s_01", title=title, date=date)
        # 連番の採番用．表示にも使うので p_01 / q_01 の形にする．
        self._presentation_seq = 0
        self._question_seq = 0
        self._participant_seq = 0
        # (発表ID, トークン) -> 最後に「わからん」を受け付けた時刻．
        self._last_confusion: dict[tuple[str, str], float] = {}
        # 全発表分の質問．件数は高々数十なので，発表ごとの絞り込みは線形探索で足りる．
        self._questions: list[Question] = []

    # ----------------------------------------------------------------------
    # 参加者
    # ----------------------------------------------------------------------

    def add_participant(self, grade: Grade) -> str:
        """参加者を登録してトークンを返す．トークンは32文字の16進文字列．"""
        self._participant_seq += 1
        token = secrets.token_hex(16)
        self.session.participants[token] = Participant(
            id=f"u_{self._participant_seq:02d}", token=token, grade=grade
        )
        return token

    def participant(self, token: str) -> Participant | None:
        return self.session.participants.get(token)

    # ----------------------------------------------------------------------
    # 発表
    # ----------------------------------------------------------------------

    def create_presentation(self, presenter: str, title: str) -> Presentation:
        self._presentation_seq += 1
        pres = Presentation(
            id=f"p_{self._presentation_seq:02d}",
            session_id=self.session.id,
            presenter=presenter,
            title=title,
        )
        self.session.presentations.append(pres)
        return pres

    def presentation(self, pid: str) -> Presentation | None:
        for p in self.session.presentations:
            if p.id == pid:
                return p
        return None

    def current_presentation(self) -> Presentation | None:
        """進行中（発表中または質疑中）の発表．無ければ None．

        FR-03 により，これは同時に1つしか存在しない．
        """
        for p in self.session.presentations:
            if p.is_current():
                return p
        return None

    def start(self, pres: Presentation, initial_tokens: set[str]) -> None:
        """発表を開始する．

        FR-03：未完了の発表が残っていれば自動的に完了させる．
        前の発表者が「質疑終了」を押し忘れても，次の発表が始まれば復旧する．

        `initial_tokens` には開始時点で既に接続しているトークンを渡す．
        これを分母の初期値とすることで，発表開始前から繋いでいた参加者が
        グラフの分母から漏れない．
        """
        for other in self.session.presentations:
            if other is not pres and other.is_current():
                other.state = "closed"
                if other.ended_at is None:
                    other.ended_at = time.monotonic()

        pres.state = "live"
        pres.started_at = time.monotonic()
        pres.ended_at = None
        pres.confusions.clear()
        pres.connected_tokens = set(initial_tokens)

    def end(self, pres: Presentation) -> None:
        """発表を終了し，質疑モードへ移す．"""
        pres.ended_at = time.monotonic()
        pres.state = "review"

    def close(self, pres: Presentation) -> None:
        """質疑を終了し，発表を完了させる．"""
        if pres.ended_at is None:
            pres.ended_at = time.monotonic()
        pres.state = "closed"

    # ----------------------------------------------------------------------
    # わからん
    # ----------------------------------------------------------------------

    def add_confusion(self, pres: Presentation, token: str) -> bool:
        """「わからん」を記録する．受け付けたら True．

        同一トークンから CONFUSION_INTERVAL_SEC 秒に1回までとする．
        超過分は False を返すが，呼び出し側は 204 を返して黙って捨てる．
        エラーにすると，連打した参加者の画面にだけ失敗が出て混乱するため．
        """
        now = time.monotonic()
        key = (pres.id, token)
        last = self._last_confusion.get(key)
        if last is not None and now - last < CONFUSION_INTERVAL_SEC:
            return False

        self._last_confusion[key] = now
        pres.confusions.append(
            Confusion(token=token, elapsed_sec=pres.elapsed_sec())
        )
        return True

    # ----------------------------------------------------------------------
    # 質問と共感
    # ----------------------------------------------------------------------

    def add_question(self, pres: Presentation, token: str, body: str) -> Question:
        participant = self.session.participants[token]
        self._question_seq += 1
        q = Question(
            id=f"q_{self._question_seq:02d}",
            presentation_id=pres.id,
            token=token,
            grade=participant.grade,
            body=body,
            created_at=pres.elapsed_sec(),
        )
        self._questions.append(q)
        return q

    def questions_of(self, pres: Presentation) -> list[Question]:
        return [q for q in self._questions if q.presentation_id == pres.id]

    def question(self, qid: str) -> Question | None:
        for q in self._questions:
            if q.id == qid:
                return q
        return None

    def add_empathy(self, q: Question, token: str) -> int:
        """共感を1票入れる．同じトークンの重複は集合が吸収する．"""
        q.empathy.add(token)
        return q.empathy_count

    def remove_empathy(self, q: Question, token: str) -> int:
        q.empathy.discard(token)
        return q.empathy_count

    def set_resolved(self, q: Question, resolved: bool) -> None:
        q.resolved = resolved

    # ----------------------------------------------------------------------
    # 配信用のペイロード組み立て
    # ----------------------------------------------------------------------

    def grade_labels_for(self, pres: Presentation) -> dict[str, str]:
        """FR-12：その発表における学年 -> 表示ラベルの対応を作る．

        投稿者（人数．投稿件数ではない）が1名しかいない学年は「学生」に丸める．
        研究室は小規模であり，丸めがないと学年表示が実名と同じ意味を持つ．
        教員は常に「教員」とし，丸めの対象外とする（誰の質問かを
        発表者が判断して拾えるようにするため．意図した非対称である）．
        """
        posters: dict[str, set[str]] = {}
        for q in self.questions_of(pres):
            posters.setdefault(q.grade, set()).add(q.token)

        labels: dict[str, str] = {}
        for grade, tokens in posters.items():
            if grade == "teacher":
                labels[grade] = GRADE_LABELS[grade]
            elif len(tokens) <= 1:
                labels[grade] = ROUNDED_LABEL
            else:
                labels[grade] = GRADE_LABELS[grade]
        return labels

    def questions_payload(self, pres: Presentation) -> dict:
        """`questions` メッセージの中身．

        並び順はサーバ側で確定させる：消化済みを末尾へ，
        次に共感数の降順，同数なら投稿の早い順．
        クライアントは届いた順に描くだけでよい．
        """
        labels = self.grade_labels_for(pres)
        items = sorted(
            self.questions_of(pres),
            key=lambda q: (q.resolved, -q.empathy_count, q.created_at),
        )
        return {
            "type": "questions",
            "items": [
                {
                    "id": q.id,
                    "grade_label": labels.get(q.grade, ROUNDED_LABEL),
                    "body": q.body,
                    "empathy_count": q.empathy_count,
                    "resolved": q.resolved,
                    "created_at": q.created_at,
                    # 注意：token と生の grade は絶対に含めない
                }
                for q in items
            ],
        }

    def state_payload(self, participant_count: int) -> dict:
        """`state` メッセージの中身．"""
        pres = self.current_presentation()
        return {
            "type": "state",
            "participant_count": participant_count,
            "presentation": None if pres is None else {
                "id": pres.id,
                "presenter": pres.presenter,
                "title": pres.title,
                "state": pres.state,
                "elapsed_sec": pres.elapsed_sec(),
                "question_count": len(self.questions_of(pres)),
            },
        }

    def chart_payload(self, pres: Presentation) -> dict:
        """`chart` メッセージの中身．

        分母はその発表中に一度でも接続したトークン数である．
        各区間の count も「押した人数」とし，同一人物の複数回を1と数える．
        こうしておくと rate が 1.0 を超えることがない．
        """
        total = pres.elapsed_sec()
        denom = len(pres.connected_tokens) or 1
        bucket_count = max(1, (total // BUCKET_SEC) + 1)

        # 区間ごとに，押した人のトークンを集合で集める．
        tokens_per_bucket: list[set[str]] = [set() for _ in range(bucket_count)]
        for c in pres.confusions:
            index = min(c.elapsed_sec // BUCKET_SEC, bucket_count - 1)
            tokens_per_bucket[index].add(c.token)

        buckets = [
            {
                "t": i * BUCKET_SEC,
                "count": len(tokens),
                "rate": round(len(tokens) / denom, 2),
            }
            for i, tokens in enumerate(tokens_per_bucket)
        ]

        return {
            "type": "chart",
            "bucket_sec": BUCKET_SEC,
            "buckets": buckets,
            "peak": _peak_of(buckets),
            "denominator": denom,  # 凡例に「参加 N 名中」と出すために添える
        }


def _peak_of(buckets: list[dict]) -> dict | None:
    """rate が最大となる連続区間を求める．

    わからんが1件も無い発表では最大値が 0 になる．
    この場合，全区間がピークということになって意味を成さないので None を返す．
    受け取った側は帯を描かない．
    """
    top = max((b["rate"] for b in buckets), default=0.0)
    if top <= 0:
        return None

    best_start = best_len = 0
    run_start = run_len = 0
    for i, b in enumerate(buckets):
        if b["rate"] == top:
            if run_len == 0:
                run_start = i
            run_len += 1
            if run_len > best_len:
                best_start, best_len = run_start, run_len
        else:
            run_len = 0

    return {
        "start_sec": buckets[best_start]["t"],
        "end_sec": buckets[best_start + best_len - 1]["t"] + BUCKET_SEC,
        "rate": top,
    }
