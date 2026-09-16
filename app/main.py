"""FastAPI のエントリポイント．ルーティングと配信のきっかけだけを持つ．

設計の要点：
    - クライアントからサーバへの書き込みは全て HTTP（REST）
    - サーバからクライアントへの更新配信は全て WebSocket（サーバ発の一方向）
    この分離を崩さないこと．再接続が「繋ぎ直して受け直すだけ」で済む．

状態の操作は store.py に閉じている．ここでは状態を直接いじらない．
"""

from __future__ import annotations

import argparse
import datetime
import io
import logging
import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .hub import Hub
from .models import GRADES, Grade
from .record import build_markdown
from .store import BODY_MAX_LEN, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

app = FastAPI(title="ゼミ質疑支援アプリ")

# セッションはサーバ起動時に1つだけ自動生成する（requirements.md §5）．
# ゼミ1回で完結するため，1プロセス＝1セッションで足りる．
#
# 日付は必ず入れる．記録が当日の唯一の出力である以上，日付の無い記録は
# 後から中間発表と期末発表を区別できず，価値が大きく下がる．
# 環境変数で上書きできるが，未指定なら起動日を使う．
store = Store(
    title=os.environ.get("SEMINAR_TITLE", "ゼミ"),
    date=os.environ.get("SEMINAR_DATE") or datetime.date.today().isoformat(),
)
hub = Hub()


# --------------------------------------------------------------------------
# リクエストの本文
# --------------------------------------------------------------------------

class JoinBody(BaseModel):
    grade: Grade


class PresentationBody(BaseModel):
    presenter: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)


class TokenBody(BaseModel):
    token: str


class QuestionBody(BaseModel):
    token: str
    body: str


# --------------------------------------------------------------------------
# 配信のきっかけ
#
# 状態を変えた側が，変わったものだけを配信する．
# --------------------------------------------------------------------------

async def push_state() -> None:
    await hub.broadcast(store.state_payload(hub.participant_count()))


async def push_questions() -> None:
    """質問一覧を配信する．

    **発表中（live）は配信しない．** 発表中に質問の本文を参加者の端末へ
    届けないための措置である（requirements.md FR-06）．
    クライアント側で描画を抑止するのではなく，サーバが送らないことで担保する．
    """
    pres = store.current_presentation()
    if pres is None or pres.state != "review":
        return
    await hub.broadcast(store.questions_payload(pres))


# --------------------------------------------------------------------------
# セッション
# --------------------------------------------------------------------------

def _join_url(request: Request, sid: str) -> str:
    """参加用URLを組み立てる．

    ホスト名はリクエストの Host ヘッダから取る．
    発表者が http://192.168.1.5:8000/present を開けば，
    QRも同じIPを指すため，IPアドレスを設定に書く必要がない．

    裏返しとして，localhost で開くとQRも localhost を指し，
    スマートフォンからは一切繋がらない．この警告は画面側で出す．
    """
    host = request.headers.get("host", f"127.0.0.1:{request.url.port or 8000}")
    return f"{request.url.scheme}://{host}/join?s={sid}"


def _session_payload(request: Request) -> dict:
    return {
        "session_id": store.session.id,
        "title": store.session.title,
        "date": store.session.date,
        "join_url": _join_url(request, store.session.id),
        "qr_png_url": f"/api/sessions/{store.session.id}/qr.png",
        "participant_count": hub.participant_count(),
        "grades": list(GRADES),
        "presentations": [
            {
                "id": p.id,
                "presenter": p.presenter,
                "title": p.title,
                "state": p.state,
            }
            for p in store.session.presentations
        ],
    }


@app.get("/api/sessions/current")
async def get_current_session(request: Request) -> dict:
    return _session_payload(request)


@app.get("/api/sessions/{sid}")
async def get_session(sid: str, request: Request) -> dict:
    if sid != store.session.id:
        raise HTTPException(404, "セッションが存在しない")
    return _session_payload(request)


@app.get("/api/sessions/{sid}/qr.png")
async def get_qr(sid: str, request: Request) -> Response:
    """参加用URLのQRコードを PNG で返す．"""
    if sid != store.session.id:
        raise HTTPException(404, "セッションが存在しない")
    import qrcode

    img = qrcode.make(_join_url(request, sid))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        # 参加URLはセッション中に変わらないので，端末側で持っていてよい．
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/sessions/{sid}/participants", status_code=201)
async def join(sid: str, body: JoinBody) -> dict:
    if sid != store.session.id:
        raise HTTPException(404, "セッションが存在しない")
    token = store.add_participant(body.grade)
    return {"token": token}


# --------------------------------------------------------------------------
# 発表
# --------------------------------------------------------------------------

@app.post("/api/sessions/{sid}/presentations", status_code=201)
async def create_presentation(sid: str, body: PresentationBody) -> dict:
    if sid != store.session.id:
        raise HTTPException(404, "セッションが存在しない")
    pres = store.create_presentation(body.presenter, body.title)
    return {"presentation_id": pres.id}


def _require_presentation(pid: str):
    pres = store.presentation(pid)
    if pres is None:
        raise HTTPException(404, "発表が存在しない")
    return pres


@app.post("/api/presentations/{pid}/start", status_code=204)
async def start_presentation(pid: str) -> Response:
    pres = _require_presentation(pid)
    # 開始時点で既に接続している参加者を，グラフの分母の初期値とする．
    store.start(pres, hub.connected_tokens())
    await push_state()
    return Response(status_code=204)


@app.post("/api/presentations/{pid}/end", status_code=204)
async def end_presentation(pid: str) -> Response:
    pres = _require_presentation(pid)
    store.end(pres)
    # 質疑モードへの移行．ここで初めて質問一覧とグラフを配信する．
    await push_state()
    await push_questions()
    await hub.broadcast(store.chart_payload(pres))
    return Response(status_code=204)


@app.post("/api/presentations/{pid}/close")
async def close_presentation(pid: str) -> dict:
    pres = _require_presentation(pid)
    record_md = build_markdown(store, pres)
    store.close(pres)
    await push_state()

    # Teams への投稿は任意である（段階6 で teams.py を追加する）．
    # 未実装・未設定・失敗のいずれでも，record_md は必ず返す．
    teams_posted = False
    return {"teams_posted": teams_posted, "record_md": record_md}


# --------------------------------------------------------------------------
# 投稿
# --------------------------------------------------------------------------

def _require_token(token: str) -> str:
    if store.participant(token) is None:
        raise HTTPException(401, "参加していない")
    return token


@app.post("/api/presentations/{pid}/confusion", status_code=204)
async def post_confusion(pid: str, body: TokenBody) -> Response:
    pres = _require_presentation(pid)
    _require_token(body.token)
    # 受付間隔を超えた分は黙って捨てる．エラーにすると連打した人の
    # 画面にだけ失敗が出て，発表中に気が散る．
    if store.add_confusion(pres, body.token):
        await push_state()
    return Response(status_code=204)


@app.post("/api/presentations/{pid}/questions", status_code=201)
async def post_question(pid: str, body: QuestionBody) -> dict:
    pres = _require_presentation(pid)
    _require_token(body.token)

    text = body.body.strip()
    if not text:
        raise HTTPException(400, "本文が空である")
    if len(text) > BODY_MAX_LEN:
        raise HTTPException(400, f"本文は{BODY_MAX_LEN}字以内である")

    q = store.add_question(pres, body.token, text)
    # 件数は発表中も配信してよい（本文は含まれない）．
    await push_state()
    await push_questions()
    return {"question_id": q.id}


def _require_question(qid: str):
    q = store.question(qid)
    if q is None:
        raise HTTPException(404, "質問が存在しない")
    return q


@app.post("/api/questions/{qid}/empathy")
async def add_empathy(qid: str, body: TokenBody) -> dict:
    q = _require_question(qid)
    _require_token(body.token)
    count = store.add_empathy(q, body.token)
    await push_questions()
    return {"empathy_count": count}


@app.delete("/api/questions/{qid}/empathy")
async def remove_empathy(qid: str, body: TokenBody) -> dict:
    q = _require_question(qid)
    _require_token(body.token)
    count = store.remove_empathy(q, body.token)
    await push_questions()
    return {"empathy_count": count}


@app.post("/api/questions/{qid}/resolve", status_code=204)
async def resolve(qid: str) -> Response:
    store.set_resolved(_require_question(qid), True)
    await push_questions()
    return Response(status_code=204)


@app.delete("/api/questions/{qid}/resolve", status_code=204)
async def unresolve(qid: str) -> Response:
    """消化を取り消す．投影中の誤操作を戻すためのもの．"""
    store.set_resolved(_require_question(qid), False)
    await push_questions()
    return Response(status_code=204)


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """サーバ発の一方向配信．クライアントからは何も受け取らない．"""
    token = ws.query_params.get("t", "")
    await hub.connect(ws, token)

    # この発表中に接続した人として記録する（グラフの分母）．
    pres = store.current_presentation()
    if pres is not None and token:
        pres.connected_tokens.add(token)

    try:
        # 接続直後は state を送る．質疑中なら質問一覧とグラフも送る．
        # グラフを再送するのは，投影用PCが再接続したときに
        # 折れ線が消えたままにならないようにするため．
        await ws.send_json(store.state_payload(hub.participant_count()))
        if pres is not None and pres.state == "review":
            await ws.send_json(store.questions_payload(pres))
            await ws.send_json(store.chart_payload(pres))

        # 接続人数が変わったので全員に知らせる．
        await push_state()

        # クライアントからは何も来ない．切断を検出するためだけに待つ．
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.info("WebSocket が異常終了した: %s", exc)
    finally:
        hub.disconnect(ws)
        await push_state()


# --------------------------------------------------------------------------
# 画面
# --------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _page(name: str) -> Response:
    """static 配下の HTML を返す．未実装の段階では案内を出す．"""
    path = os.path.join(STATIC_DIR, name)
    if os.path.exists(path):
        return FileResponse(path)
    return HTMLResponse(
        f"<h1>{name} は未実装である</h1>"
        "<p>実装順序（CLAUDE.md）に従って作成する．</p>",
        status_code=404,
    )


@app.get("/")
async def index() -> Response:
    return RedirectResponse("/present")


@app.get("/join")
async def page_join() -> Response:
    return _page("join.html")


@app.get("/live")
async def page_live() -> Response:
    return _page("live.html")


@app.get("/present")
async def page_present() -> Response:
    return _page("present.html")


@app.get("/review")
async def page_review() -> Response:
    return _page("review.html")


# --------------------------------------------------------------------------

def _cli() -> None:
    """`python -m app.main` で起動するときの入り口．

    通常は README のとおり uvicorn から起動する．
    こちらはセッションのタイトルと日付を指定したいときに使う．
    """
    parser = argparse.ArgumentParser(description="ゼミ質疑支援アプリ")
    parser.add_argument("--title", default="ゼミ", help="セッションのタイトル")
    parser.add_argument("--date", default="", help="日付（既定：起動日）")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    store.session.title = args.title
    if args.date:
        store.session.date = args.date

    import uvicorn
    # --host 0.0.0.0 は必須．これがないとスマートフォンから接続できない．
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    _cli()
