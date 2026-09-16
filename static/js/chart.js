// わからん率の折れ線を SVG で直接生成する．描画ライブラリは使わない．
//
// 縦軸は 0〜100% に固定する．最大値に合わせて伸縮させると，
// 発表ごとに軸の意味が変わり，中間発表と期末発表を並べて比べられなくなる．
//
// 横軸は発表開始からの経過時間である．絶対時刻は記録していない．

const NS = "http://www.w3.org/2000/svg";

// 余白．左は割合のラベル，下は時刻のラベルのために取る．
const PAD = { top: 16, right: 16, bottom: 28, left: 56 };
// 縦軸の目盛り．高さが足りないとラベルが重なって潰れるため，
// 狭いときは 0 / 50 / 100 だけにする．
const Y_TICKS = [0, 0.25, 0.5, 0.75, 1];
const Y_TICKS_NARROW = [0, 0.5, 1];
const NARROW_H = 120;

/**
 * @param {HTMLElement} host   描画先．中身は置き換えられる
 * @param {object} chart       protocol.md の chart メッセージ
 * @param {{animate?: boolean}} options
 */
export function renderChart(host, chart, options = {}) {
  const animate = options.animate !== false;

  const rect = host.getBoundingClientRect();
  // 非表示のまま呼ばれると 0 になる．その場合は描かない．
  if (rect.width < 1 || rect.height < 1) return;

  const w = Math.round(rect.width);
  const h = Math.round(rect.height);
  const plot = {
    x: PAD.left,
    y: PAD.top,
    w: w - PAD.left - PAD.right,
    h: h - PAD.top - PAD.bottom,
  };

  const svg = el("svg", {
    viewBox: `0 0 ${w} ${h}`,
    width: "100%", height: "100%",
    role: "img",
    "aria-label": "わからんの割合の推移",
  });

  const buckets = chart.buckets || [];
  const bucketSec = chart.bucket_sec || 30;
  // 区間は [t, t+bucket_sec) を表す．終端まで線を引きたいので幅を1つ足す．
  const totalSec = buckets.length > 0
    ? buckets[buckets.length - 1].t + bucketSec
    : bucketSec;

  const toX = (sec) => plot.x + (totalSec === 0 ? 0 : (sec / totalSec) * plot.w);
  const toY = (rate) => plot.y + plot.h - Math.min(rate, 1) * plot.h;

  drawGrid(svg, plot, toY, plot.h < NARROW_H ? Y_TICKS_NARROW : Y_TICKS);
  if (chart.peak) drawPeak(svg, chart.peak, plot, toX, animate);
  drawLine(svg, buckets, bucketSec, toX, toY, animate);
  drawTimeLabels(svg, buckets, bucketSec, totalSec, toX, plot);

  host.replaceChildren(svg, caption(chart));
}

// --------------------------------------------------------------------------

function drawGrid(svg, plot, toY, ticks) {
  for (const rate of ticks) {
    const y = toY(rate);
    svg.append(el("line", {
      class: "chart-grid",
      x1: plot.x, y1: y, x2: plot.x + plot.w, y2: y,
    }));
    svg.append(text(`${Math.round(rate * 100)}%`, {
      class: "chart-label",
      x: plot.x - 8, y: y + 4, "text-anchor": "end",
    }));
  }
}

/** わからんが集中した区間を帯で示す． */
function drawPeak(svg, peak, plot, toX, animate) {
  const x1 = toX(peak.start_sec);
  const x2 = toX(peak.end_sec);
  const band = el("rect", {
    class: "chart-peak" + (animate ? " is-animated" : ""),
    x: x1, y: plot.y, width: Math.max(x2 - x1, 2), height: plot.h,
  });
  svg.append(band);
}

function drawLine(svg, buckets, bucketSec, toX, toY, animate) {
  if (buckets.length === 0) return;

  // 各区間の中央に点を置く．区間の代表値だと分かるようにするため．
  const points = buckets.map((b) => [toX(b.t + bucketSec / 2), toY(b.rate)]);

  if (points.length === 1) {
    svg.append(el("circle", { class: "chart-dot", cx: points[0][0], cy: points[0][1], r: 5 }));
    return;
  }

  const d = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const path = el("path", { class: "chart-line", d });
  svg.append(path);

  if (animate) {
    // 左から描き足されるように見せる．
    const len = path.getTotalLength();
    path.style.setProperty("--len", String(len));
    path.classList.add("is-animated");
  }

  // 頂点に小さな点を打つ．折れ線だけだと区間の境目が読みにくい．
  for (const [x, y] of points) {
    svg.append(el("circle", { class: "chart-dot", cx: x, cy: y, r: 3 }));
  }
}

function drawTimeLabels(svg, buckets, bucketSec, totalSec, toX, plot) {
  if (buckets.length === 0) return;

  // ラベルが6個前後になるように間引く．投影で読めることを優先する．
  const step = Math.max(1, Math.ceil(buckets.length / 6));
  const y = plot.y + plot.h + 20;

  for (let i = 0; i < buckets.length; i += step) {
    svg.append(text(mmss(buckets[i].t), {
      class: "chart-label",
      x: toX(buckets[i].t + bucketSec / 2), y, "text-anchor": "middle",
    }));
  }
  // 終端は必ず出す．発表の長さが分かるようにするため．
  svg.append(text(mmss(totalSec), {
    class: "chart-label", x: plot.x + plot.w, y, "text-anchor": "end",
  }));
}

function caption(chart) {
  const p = document.createElement("p");
  p.className = "chart-caption faint";

  const parts = [`参加 ${chart.denominator ?? 0} 名中`];
  if (chart.peak) {
    parts.push(
      `最も集中したのは ${mmss(chart.peak.start_sec)} 〜 ${mmss(chart.peak.end_sec)}`
      + `（${Math.round(chart.peak.rate * 100)}%）`
    );
  } else {
    parts.push("「わからん」はありませんでした");
  }
  p.textContent = parts.join("　");
  return p;
}

// --------------------------------------------------------------------------

function el(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function text(content, attrs) {
  const node = el("text", attrs);
  node.textContent = content;
  return node;
}

function mmss(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
