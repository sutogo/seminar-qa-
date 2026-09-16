// 表示順の安定化．
//
// サーバは共感が1票入るたびに全件を配信する．そのまま並べ替えると，
// 質問を読んでいる最中にカードが動き，投影では読めなくなる．
// 順位が新しいことと，画面が動いてよいことは別である．
//
// そこで表示順を手元に保持し，次の場合にだけサーバの並びへ揃える．
//
//   1. 質疑に入って最初に描くとき
//   2. 消化状態が変わったとき（発表者が次の質問へ進む瞬間）
//   3. 明示的に並べ替えを指示されたとき
//
// それ以外の更新では，共感数だけがその場で書き換わり，カードは動かない．
// 新しく届いた質問は，消化済みの手前（未消化の末尾）に足す．
// 共感0の新しい質問はもともと未消化の最後に並ぶので，これで正しい位置になる．

export function createOrderKeeper() {
  let displayed = [];
  let resolvedKey = null;
  let forced = false;

  return {
    /** 次の arrange で強制的にサーバの並びへ揃える． */
    requestResort() { forced = true; },

    /** 発表が変わったときなど，最初から組み直す． */
    reset() { displayed = []; resolvedKey = null; forced = false; },

    /**
     * @param {Array<{id: string, resolved: boolean}>} items サーバの並び順
     * @returns {{order: string[], reordered: boolean, pending: number}}
     *   order      表示に使う並び
     *   reordered  今回サーバの並びへ揃えたか（演出を出すかの判断に使う）
     *   pending    サーバの並びと位置が異なる質問の件数
     */
    arrange(items) {
      const serverOrder = items.map((q) => q.id);
      const key = items.filter((q) => q.resolved).map((q) => q.id).sort().join(",");

      const isFirst = displayed.length === 0;
      const resolvedChanged = resolvedKey !== null && key !== resolvedKey;
      resolvedKey = key;

      const reordered = isFirst || resolvedChanged || forced;
      forced = false;

      if (reordered) {
        displayed = serverOrder;
      } else {
        const alive = new Set(serverOrder);
        const kept = displayed.filter((id) => alive.has(id));
        const known = new Set(kept);
        const added = serverOrder.filter((id) => !known.has(id));
        displayed = added.length === 0 ? kept : insertBeforeResolved(kept, added, items);
      }

      return { order: displayed, reordered, pending: countMisplaced(displayed, serverOrder) };
    },
  };
}

/** 新しい質問を，消化済みの手前に差し込む． */
function insertBeforeResolved(order, added, items) {
  const resolved = new Set(items.filter((q) => q.resolved).map((q) => q.id));
  const head = [];
  const tail = [];
  let seenResolved = false;

  for (const id of order) {
    if (resolved.has(id)) seenResolved = true;
    (seenResolved ? tail : head).push(id);
  }
  return [...head, ...added, ...tail];
}

/** サーバの並びと位置が食い違う件数．「並べ替え待ち」の表示に使う． */
function countMisplaced(displayed, serverOrder) {
  let n = 0;
  for (let i = 0; i < displayed.length; i++) {
    if (displayed[i] !== serverOrder[i]) n++;
  }
  return n;
}
