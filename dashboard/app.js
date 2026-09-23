
// ضع معرف الـGist هنا أو مرره عبر ?gist=
// يُقبل فقط بصيغة GitHub الصحيحة (32 حرفاً سداسياً عشرياً) — أي قيمة
// أخرى تُتجاهل لمنع حقن مسارات في رابط GitHub API
const GIST_ID_DEFAULT = "";
const qp = new URLSearchParams(location.search);
const _gistParam = (qp.get("gist") || GIST_ID_DEFAULT).trim().toLowerCase();
const GIST_ID = /^[0-9a-f]{32}$/.test(_gistParam) ? _gistParam : "";

const $ = id => document.getElementById(id);
const fmt$ = v => "$" + Number(v).toFixed(2);
const slug = id => String(id || "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "");
let chart = null;

const FALLBACK_COIN = "data:image/svg+xml," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><circle cx="24" cy="24" r="22" fill="#1f2633"/><text x="24" y="30" font-size="20" text-anchor="middle" fill="#64748b">◉</text></svg>`);

let currentCards = [];
let activeFilter = "all";

// بديل شعارات العملات عند فشل التحميل: معالج واحد على مستوى الصفحة
// (مرحلة الالتقاط — أحداث الخطأ لا تنتشر للأعلى) بدل معالج onerror
// مضمَّن في كل بطاقة، لأن المعالجات المضمَّنة محظورة بسياسة CSP
// ولأنها كانت سطح حقن محتمل عبر بيانات الـGist
document.addEventListener("error", e => {
  const img = e.target;
  if (img && img.tagName === "IMG" && img.hasAttribute("data-fallback-img")
      && !img.dataset.fbk) {
    img.dataset.fbk = "1";
    img.src = FALLBACK_COIN;
  }
}, true);

function logoFor(t) {
  if (t.kind === "dex" && t.mint && t.chain)
    return `https://dd.dexscreener.com/ds-data/tokens/${t.chain}/${t.mint}.png`;
  if (t.kind === "binance" && t.symbol) {
    const base = t.symbol.replace(/USDT$/, "").toLowerCase();
    return `https://assets.coincap.io/assets/icons/${base}@2x.png`;
  }
  return FALLBACK_COIN;
}

function chartUrl(t) {
  if (t.kind === "dex" && t.chain && t.pair)
    return `https://dexscreener.com/${t.chain}/${t.pair}`;
  if (t.kind === "binance" && t.symbol)
    return `https://www.binance.com/en/trade/${t.symbol.replace("USDT", "")}_USDT`;
  return null;
}

async function dexPrice(chain, pair) {
  try {
    const r = await fetch(`https://api.dexscreener.com/latest/dex/pairs/${chain}/${pair}`, {cache: "no-store"});
    const j = await r.json();
    const p = (j.pairs || [])[0];
    return p ? parseFloat(p.priceUsd) : null;
  } catch { return null; }
}

// سعر حي لعملات Binance (API عام مجاني، بلا مفتاح)
async function binancePrice(symbol) {
  try {
    const r = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`, {cache: "no-store"});
    const j = await r.json();
    return j.price ? parseFloat(j.price) : null;
  } catch { return null; }
}

// مزاج السوق: الخوف والطمع + البيتكوين (مصادر عامة مجانية)
async function loadSentiment() {
  try {
    const r = await fetch("https://api.alternative.me/fng/?limit=1", {cache: "no-store"});
    const j = await r.json();
    const v = parseInt(((j.data || [])[0] || {}).value);
    if (!isNaN(v)) {
      $("fng-val").textContent = v + "/100";
      const [label, color] = v <= 24 ? ["خوف شديد", "#f87171"]
        : v <= 44 ? ["خوف", "#fb923c"]
        : v <= 55 ? ["محايد", "#facc15"]
        : v <= 74 ? ["طمع", "#a3e635"]
        : ["طمع شديد", "#4ade80"];
      const l = $("fng-label");
      l.textContent = label; l.style.color = color;
      $("fng-marker").style.right = v + "%";
    }
  } catch {}
  try {
    const r = await fetch("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true", {cache: "no-store"});
    const j = await r.json();
    const b = j.bitcoin;
    if (b && b.usd) {
      $("btc-price").textContent = "$" + Number(b.usd).toLocaleString("en-US");
      const chg = b.usd_24h_change || 0;
      const e = $("btc-chg");
      e.textContent = (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%";
      e.className = "font-bold " + (chg >= 0 ? "pos" : "neg");
    }
  } catch {}
}

// فلترة الصفقات: الكل / الرابحة / الخاسرة
function renderCards(list) {
  if (!list.length) {
    const msg = activeFilter === "win" ? "لا توجد صفقات رابحة حالياً"
      : activeFilter === "loss" ? "لا توجد صفقات خاسرة — ممتاز! 🎉"
      : "لا توجد صفقات مفتوحة حالياً";
    $("t-grid").innerHTML = `<div class="glass p-8 text-center text-slate-500 col-span-full">${msg}</div>`;
  } else {
    $("t-grid").innerHTML = list.map(({id, t, live, px, pnl}) => {
      const cls = pnl >= 0 ? "pos" : "neg";
      const cu = chartUrl(t);
      const kindChip = t.kind === "binance"
        ? `<span class="chip">Binance</span>`
        : `<span class="chip">DEX · ${escHtml(t.chain || "")}</span>`;
      // حالة الجني الجزئي: كم بيعنا وكم ربحنا فعلياً + وقف التعادل
      const tpHit = (t.tp_hit || []).filter(Boolean).length;
      const isBE = t.be || tpHit > 0;
      const origQty = (t.invested && t.entry) ? t.invested / t.entry : 0;
      const remPct = origQty ? Math.round(100 * (t.qty || 0) / origQty) : 100;
      const partialChip = isBE
        ? `<div class="mb-2 text-[11px] font-bold text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-2 py-1" dir="rtl">🎯 جني 50% | SL @ الدخول — مؤمنة (Risk-Free) 🛡️ · ربح مُحقق ${((t.realized || 0) >= 0 ? "+" : "") + fmt$((t.realized || 0))} · المتبقي ${remPct}%</div>`
        : "";
      // تحذير انهيار السيولة: نفس تنبيه 🚨 المُرسل إلى Telegram — ظاهر هنا أيضاً
      const warnChip = t.warned
        ? `<div class="mb-2 text-[11px] font-bold text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg px-2 py-1" dir="rtl">🚨 خطر! السيولة تنهار أو مشكلة في العقد — راجع التنبيه</div>`
        : "";
      return `<div id="trade-${slug(id)}" class="trade-card glass flash p-4">
        <div class="flex items-center gap-3 mb-3">
          <img src="${escHtml(logoFor(t))}" alt="" class="w-11 h-11 rounded-full bg-white/5"
               data-fallback-img>
          <div class="flex-1 min-w-0">
            <div class="font-extrabold truncate">${escHtml(t.name || id)}</div>
            <div class="flex gap-1 mt-1">${kindChip}</div>
          </div>
        </div>
        ${partialChip}
        ${warnChip}
        <div class="text-3xl font-black ${cls} mb-2" dir="ltr">${pnl >= 0 ? "+" : ""}${pnl.toFixed(1)}%</div>
        <div class="text-xs text-slate-400 space-y-1 mb-3" dir="ltr">
          <div class="flex justify-between"><span>Entry</span><span class="text-slate-200">${Number(t.entry).toPrecision(4)}</span></div>
          <div class="flex justify-between"><span>Live</span><span class="text-slate-200">${live ? Number(live).toPrecision(4) : "—"}</span></div>
          <div class="flex justify-between"><span>Invested</span><span class="text-slate-200">${fmt$(t.invested || 0)}</span></div>
        </div>
        ${cu ? `<a class="btn-chart text-white" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer" href="${escHtml(cu)}"><i data-lucide="candlestick-chart" class="w-4 h-4"></i> الشارت</a>` : ""}
      </div>`;
    }).join("");
  }
  if (window.lucide) lucide.createIcons();
}

function applyFilter(f) {
  activeFilter = f;
  document.querySelectorAll(".ftab").forEach(x => x.classList.toggle("active", x.dataset.f === f));
  const list = f === "win" ? currentCards.filter(c => c.pnl >= 0)
    : f === "loss" ? currentCards.filter(c => c.pnl < 0)
    : currentCards;
  renderCards(list);
}

async function load() {
  loadSentiment();
  if (!GIST_ID) { $("setup").classList.remove("hidden"); return; }
  $("setup").classList.add("hidden");
  $("sync-hint").textContent = "جارٍ الجلب…";
  try {
    // no-store: تجاوز كاش المتصفح — أحدث نسخة من الـGist مباشرة
    const r = await fetch(`https://api.github.com/gists/${GIST_ID}`, {cache: "no-store"});
    if (!r.ok) throw new Error("gist " + r.status);
    const g = await r.json();
    const file = g.files["state.json"];
    if (!file) throw new Error("no state.json");
    await render(JSON.parse(file.content));
    $("sync-hint").textContent = "تم قبل لحظات";
  } catch (e) {
    $("dash").classList.remove("hidden");
    $("t-grid").innerHTML = `<div class="glass p-6 text-center neg col-span-full">تعذّر قراءة الحالة: ${escHtml(e.message)}</div>`;
    $("sync-hint").textContent = "فشل الجلب";
  }
}

async function render(s) {
  $("dash").classList.remove("hidden");
  const p = s.paper || {};
  const pos = p.positions || {};
  const ids = Object.keys(pos);

  // البطل
  let equity = p.cash || 0;
  const cards = [];
  for (const id of ids) {
    const t = pos[id];
    const live = (t.chain && t.pair) ? await dexPrice(t.chain, t.pair)
      : (t.kind === "binance" && t.symbol) ? await binancePrice(t.symbol)
      : null;
    const px = live || t.entry;
    equity += (t.qty || 0) * px;
    cards.push({id, t, live, px});
  }
  const start = p.start || 100;
  const pnl = equity - start, pnlPct = start ? pnl / start * 100 : 0;
  const eqEl = $("h-equity");
  eqEl.textContent = fmt$(equity);
  eqEl.classList.toggle("neg", pnl < 0);
  const pnlEl = $("h-pnl");
  pnlEl.innerHTML = `<span class="${pnl >= 0 ? "pos" : "neg"}">${pnl >= 0 ? "+" : ""}${fmt$(pnl)} (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%)</span>`;
  $("h-cash").textContent = fmt$(p.cash || 0);
  $("h-open").textContent = ids.length;
  $("h-time").textContent = new Date().toLocaleTimeString("ar-MA", {hour: "2-digit", minute: "2-digit"});

  // مؤشرات
  const tr = p.trades || 0;
  $("s-win").textContent = tr ? Math.round(100 * (p.wins || 0) / tr) + "%" : "—";
  const hist = s.history || [];
  const wins = hist.filter(h => ["tp1", "tp2", "tp3"].includes(h.outcome)).length;
  $("s-hit").textContent = hist.length ? Math.round(100 * wins / hist.length) + "%" : "—";
  $("s-rug").textContent = hist.filter(h => h.outcome === "rug").length || "0";
  $("s-bl").textContent = Object.keys(s.rug_blacklist || {}).length || "0";
  $("s-sig").textContent = Object.keys(s.alerted || {}).length;
  $("s-pos").textContent = Object.keys(s.positions || {}).length;

  // بطاقات الصفقات + الفلاتر
  $("t-count").textContent = ids.length ? ids.length + " مفتوحة" : "";
  currentCards = cards.map(c => ({...c, pnl: (c.px - c.t.entry) / c.t.entry * 100}));
  currentCards.sort((a, b) => b.pnl - a.pnl);
  $("fc-all").textContent = currentCards.length || "";
  $("fc-win").textContent = currentCards.filter(c => c.pnl >= 0).length || "";
  $("fc-loss").textContent = currentCards.filter(c => c.pnl < 0).length || "";
  applyFilter(activeFilter);

  // الرسم البياني: نجاح يومي
  const days = {};
  for (const h of hist) {
    const d = new Date(h.time * 1000).toISOString().slice(0, 10);
    days[d] = days[d] || {w: 0, n: 0};
    days[d].n++;
    if (["tp1", "tp2", "tp3"].includes(h.outcome)) days[d].w++;
  }
  const labels = Object.keys(days).sort().slice(-14);
  const data = labels.map(d => Math.round(100 * days[d].w / days[d].n));
  if (chart) chart.destroy();
  chart = new Chart($("chart"), {
    type: "line",
    data: {labels, datasets: [{data, borderColor: "#4ade80", backgroundColor: "rgba(74,222,128,.12)", fill: true, tension: .35, pointRadius: 3}]},
    options: {plugins: {legend: {display: false}}, scales: {y: {min: 0, max: 100, ticks: {color: "#64748b"}}, x: {ticks: {color: "#64748b", maxTicksLimit: 7}}}}
  });

  // سجل الصفقات المغلقة (الأرشيف التاريخي)
  renderClosed(p.closed_trades || []);

  // آخر التنبيهات — "إنصات" الداشبورد: نفس أحداث Telegram من البيانات
  renderAlerts(s.alert_log || []);

  if (window.lucide) lucide.createIcons();
}

const ALERT_KIND = {
  danger: "🚨 خطر",
  breakeven: "⚖️ تعادل",
  wallet: "💼 محفظة",
  takeprofit: "🎯 جني",
  stoploss: "🛑 وقف",
  avoid: "⛔ مرفوضة",
  buy: "🟢 شراء",
  warn: "⚠️ تحذير",
  digest: "📊 ملخص",
  info: "🔔 تنبيه",
};

function escHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderAlerts(log) {
  const el = $("a-list");
  if (!log.length) {
    el.innerHTML = `<div class="glass p-6 text-center text-slate-500">لا تنبيهات بعد — ستظهر هنا نفس رسائل Telegram من أول Run</div>`;
    return;
  }
  el.innerHTML = [...log].reverse().map(a => {
    const label = ALERT_KIND[a.kind] || ALERT_KIND.info;
    const ts = a.t ? fmtTS(a.t) : "—";
    return `<div class="glass p-3">
      <div class="flex items-center justify-between gap-2 mb-1">
        <span class="chip shrink-0">${label}</span>
        <span class="text-[11px] text-slate-500 font-mono" dir="ltr">${ts}</span>
      </div>
      <div class="text-sm text-slate-200 leading-relaxed whitespace-pre-line">${escHtml(a.text)}</div>
    </div>`;
  }).join("");
}

const REASON_LABEL = {
  TP1: "🎯 جني جزئي 50%",
  BE: "⚖️ تعادل (خروج عند الدخول)",
  TP: "🎯 اكتمال الأهداف",
  SL: "🛑 وقف الخسارة",
  RUG: "🚨 انهيار (Rug)",
  EXPIRED: "⏰ انتهاء المدة",
};

let activeCFilter = "all";
let closedArch = [];

// طابع زمني كامل بدقة الثانية — بصيغة منصات التداول: 2026.09.21 12:25:01
function fmtTS(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function closedMatches(t, f) {
  if (f === "all" || !t.close_time) return true;
  const now = Date.now() / 1000;
  if (f === "today") {
    const d = new Date(); d.setHours(0, 0, 0, 0);
    return t.close_time >= d.getTime() / 1000;
  }
  if (f === "week") return t.close_time >= now - 7 * 86400;
  if (f === "month") return t.close_time >= now - 30 * 86400;
  return true;
}

function applyClosedFilter(f) {
  activeCFilter = f;
  document.querySelectorAll(".cftab").forEach(x => x.classList.toggle("active", x.dataset.cf === f));
  const list = closedArch.filter(t => closedMatches(t, f));
  renderClosedList(list);
}

function renderClosed(arch) {
  closedArch = arch || [];
  applyClosedFilter(activeCFilter);
}

function renderClosedList(list) {
  $("c-count").textContent = list.length ? list.length + " صفقة" : "";
  if (!closedArch.length) {
    $("c-pnl").textContent = "—"; $("c-win").textContent = "—"; $("c-n").textContent = "0";
    $("c-list").innerHTML = `<div class="glass p-6 text-center text-slate-500">لا توجد صفقات مغلقة بعد — الأرشيف يبدأ من هذا التحديث</div>`;
    return;
  }
  if (!list.length) {
    $("c-pnl").textContent = "—"; $("c-win").textContent = "—"; $("c-n").textContent = "0";
    $("c-list").innerHTML = `<div class="glass p-6 text-center text-slate-500">لا توجد صفقات في هذه الفترة</div>`;
    return;
  }
  const total = list.filter(t => !(t.partial || t.reason === "TP1"))
                    .reduce((a, t) => a + (t.pnl || 0), 0);
  const wins = list.filter(t => !(t.partial || t.reason === "TP1") && (t.pnl || 0) > 0).length;
  const closedN = list.filter(t => !(t.partial || t.reason === "TP1")).length;
  const wr = closedN ? Math.round(100 * wins / closedN) : 0;
  const pnlEl = $("c-pnl");
  pnlEl.textContent = `${total >= 0 ? "+" : ""}$${total.toFixed(2)}`;
  pnlEl.className = "font-black text-xl " + (total >= 0 ? "pos" : "neg");
  const winEl = $("c-win");
  winEl.textContent = wr + "%";
  winEl.className = "font-extrabold text-xl " + (wr >= 50 ? "pos" : "neg");
  $("c-n").textContent = closedN;
  const rows = [...list].reverse().map(t => {
    const pnl = t.pnl || 0, cls = pnl >= 0 ? "pos" : "neg";
    const reason = REASON_LABEL[t.reason] || t.reason || "—";
    const isPartial = t.partial || t.reason === "TP1";
    const partialTag = isPartial
      ? `<span class="chip" title="حدث جزئي — مستثنى من مجاميع الربح والنجاح">جزئي</span>` : "";
    return `<div class="glass p-3">
      <div class="flex items-center justify-between gap-2 mb-1">
        <div class="font-extrabold truncate">${escHtml(t.name || "—")}</div>
        <span class="shrink-0 flex gap-1"><span class="chip">${escHtml(reason)}</span>${partialTag}</span>
      </div>
      <div class="text-[11px] text-slate-500 mb-2 font-mono" dir="ltr">${fmtTS(t.close_time)}</div>
      <div class="flex items-end justify-between gap-2">
        <div class="text-xs text-slate-400 space-y-0.5" dir="ltr">
          <div>Entry <span class="text-slate-200">${Number(t.entry).toPrecision(4)}</span></div>
          <div>Exit <span class="text-slate-200">${Number(t.exit).toPrecision(4)}</span></div>
          <div>Invested <span class="text-slate-200">$${Number(t.invested || 0).toFixed(2)}</span></div>
        </div>
        <div class="text-right shrink-0" dir="ltr">
          <div class="font-black text-2xl ${cls}">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}</div>
          <div class="text-sm font-bold ${cls}">${(t.pnl_pct || 0) >= 0 ? "+" : ""}${Number(t.pnl_pct || 0).toFixed(1)}%</div>
        </div>
      </div>
    </div>`;
  }).join("");
  $("c-list").innerHTML = rows;
}

$("refresh").onclick = load;
document.querySelectorAll(".ftab").forEach(b => b.onclick = () => applyFilter(b.dataset.f));
document.querySelectorAll(".cftab").forEach(b => b.onclick = () => applyClosedFilter(b.dataset.cf));
setInterval(load, 60000);
load();
if (window.lucide) lucide.createIcons();
