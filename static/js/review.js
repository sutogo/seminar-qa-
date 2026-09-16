// 質疑ビュー（スクリーン投影）．
//
// 共感数の多い順に質問を並べ，上から順に消化する．
// 並び順はサーバが確定させるので，こちらは届いた順に描くだけでよい．
//
// 共感が入って順位が変わる瞬間が，この作品で最も見られる箇所である．
// View Transitions API を使い，並び替えを自前の FLIP 無しで見せる．

import { api, storage } from "./api.js";
import { connect } from "./ws.js";

const $ = (id) => document.getElementById(id);

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// 描画待ちの最新データと，実行中かどうか．
// 共感は1票ごとにサーバから全件が配信される．読み上げた直後に数人が同時に
// 押すと，320ms のアニメーションが終わる前に次の更新が来る．
// そのたびに startViewTransition を呼ぶと，前の演出が中断されてガタつく．
// 実行中は最新のデータだけを覚えておき，終わってからまとめて描く．
// 途中の状態を飛ばしても，サーバは毎回全件を送るので不整合にはならない．
let pending = null;
let running = false;

// --------------------------------------------------------------------------
// 描画
// --------------------------------------------------------------------------

function render(items) {
  pending = items;
  if (!running) flush();
}

async function flush() {
  while (pending) {
    const items = pending;
    pending = null;

    // 非対応の環境では DOM が普通に更新されるだけで壊れない．
    if (!document.startViewTransition || reducedMotion) {
      paint(items);
      continue;
    }

    running = true;
    const transition = document.startViewTransition(() => paint(items));
    // 中断されると finished は reject する．演出の失敗で描画を止めない．
    await transition.finished.catch(() => {});
    running = false;
  }
}

function paint(items) {
  const list = $("questions");
  list.replaceChildren();

  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "質問はまだありません．";
    list.append(empty);
    return;
  }

  for (const q of items) list.append(buildCard(q));
}

function buildCard(q) {
  const card = document.createElement("article");
  card.className = "card q-card" + (q.resolved ? " resolved" : "");
  // カードごとに固有の名前を振ると，並び替えが個別に追従する．
  card.style.viewTransitionName = `q-${q.id}`;

  const main = document.createElement("div");
  main.className = "q-main";

  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = q.grade_label;

  const body = document.createElement("div");
  body.className = "card-body";
  body.textContent = q.body;

  main.append(badge, body);

  const side = document.createElement("div");
  side.className = "q-side";

  const num = document.createElement("div");
  num.className = "empathy-num";
  num.textContent = String(q.empathy_count);

  const label = document.createElement("div");
  label.className = "faint";
  label.textContent = "共感";

  const btn = document.createElement("button");
  btn.className = "btn btn-resolve";
  btn.type = "button";
  btn.textContent = q.resolved ? "戻す" : "消化";
  btn.addEventListener("click", () => toggleResolve(q));

  side.append(num, label, btn);
  card.append(main, side);
  return card;
}

async function toggleResolve(q) {
  try {
    if (q.resolved) await api.unresolve(q.id);
    else await api.resolve(q.id);
    // 結果はサーバからの questions 配信で反映される．
    // ここで手元の DOM を書き換えると，配信と二重に描いて並びが乱れる．
  } catch (err) {
    console.error("消化状態の変更に失敗した", err);
  }
}

// --------------------------------------------------------------------------
// 状態
// --------------------------------------------------------------------------

function applyState(msg) {
  const pres = msg.presentation;
  const meta = [];

  if (pres) {
    $("pres-title").textContent = pres.title;
    $("pres-presenter").textContent = pres.presenter;
    meta.push(`質問 ${pres.question_count} 件`);
  } else {
    $("pres-title").textContent = "質疑";
    $("pres-presenter").textContent = "発表を待っています";
    render([]);
  }
  meta.push(`接続中 ${msg.participant_count} 人`);
  $("meta").textContent = meta.join("　");
}

// --------------------------------------------------------------------------

function main() {
  // 投影用のため参加は不要である．トークンを持たずに接続する．
  connect(storage.sessionId || "", storage.token || "", {
    onOpen: () => document.body.classList.remove("is-offline"),
    onClose: () => document.body.classList.add("is-offline"),
    onMessage: (msg) => {
      if (msg.type === "state") applyState(msg);
      else if (msg.type === "questions") render(msg.items);
      // chart は段階5 で扱う．
    },
  });
}

main();
