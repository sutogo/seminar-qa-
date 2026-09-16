"""質疑終了時の記録（Markdown）の生成．

記録の「生成」と「配送」を分離する（requirements.md §9）．
Teams のチャネルへ投稿する権限が無くても記録は必ず残る，という前提を守るため，
このモジュールは外部への通信を一切行わない．
配送は teams.py が担い，そちらは任意である．
"""

from __future__ import annotations

from .models import Presentation
from .store import Store

# 記録に載せる質問の件数．これ以上は「ほか N 件」とだけ書く．
TOP_N = 5


def build_markdown(store: Store, pres: Presentation) -> str:
    """発表1件分の記録を Markdown で組み立てる．

    そのまま Teams のチャットや OneNote に貼れることを狙う．
    """
    questions = store.questions_payload(pres)["items"]
    chart = store.chart_payload(pres)
    peak = chart["peak"]

    minutes, seconds = divmod(pres.elapsed_sec(), 60)

    lines: list[str] = [
        f"## {pres.title}",
        "",
        f"- 発表者：{pres.presenter}",
        f"- 日付：{store.session.date}",
        f"- 発表時間：{minutes}分{seconds}秒",
        f"- 参加人数：{chart['denominator']} 名",
        f"- 質問件数：{len(questions)} 件",
    ]

    if peak is None:
        lines.append("- わからんの集中：なし")
    else:
        start_m, start_s = divmod(peak["start_sec"], 60)
        end_m, end_s = divmod(peak["end_sec"], 60)
        lines.append(
            f"- わからんが集中した時間帯："
            f"{start_m}:{start_s:02d} 〜 {end_m}:{end_s:02d}"
            f"（最大 {round(peak['rate'] * 100)}%）"
        )

    lines += ["", "### 質問（共感数順）", ""]

    if not questions:
        lines.append("質問はありませんでした．")
    else:
        for i, q in enumerate(questions[:TOP_N], start=1):
            mark = "（消化済）" if q["resolved"] else ""
            lines.append(
                f"{i}. {q['body']}  \n"
                f"   — {q['grade_label']}／共感 {q['empathy_count']}{mark}"
            )
        remainder = len(questions) - TOP_N
        if remainder > 0:
            lines += ["", f"ほか {remainder} 件．"]

    return "\n".join(lines) + "\n"
