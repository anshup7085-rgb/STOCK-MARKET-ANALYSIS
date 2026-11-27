/*
 * The dashboard's live layer, run in the phone's browser. It draws the charts from the data the server
 * embedded at build time (#data), and streams live crypto prices straight from Binance's free public
 * market feed (REST for the last hours, a WebSocket for every tick). Prices only: nothing is traded, no key
 * is used, and nothing moves unless the real price moved. If Binance cannot be reached from this device,
 * it says so and shows the server's last recorded prices instead.
 */
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("data").textContent);
  var LW = window.LightweightCharts;
  var COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
  var IST = 19800;                                   // the charts' time axis shows IST
  var REST = "https://api.binance.com/api/v3/klines";
  var WS = "wss://stream.binance.com:443/stream?streams=";
  var css = getComputedStyle(document.documentElement);
  var UP = css.getPropertyValue("--up").trim(), DOWN = css.getPropertyValue("--down").trim();

  function $(id) { return document.getElementById(id); }
  function fmt(x, d) { return x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function digits(p) { return p >= 1000 ? 2 : p >= 1 ? 3 : 4; }
  function pct(x) { return (x > 0 ? "+" : "") + (x * 100).toFixed(2) + "%"; }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // ---- the clock
  function tick() {
    var el = $("clock");
    if (el) el.textContent = new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }) + " IST";
  }
  setInterval(tick, 1000); tick();

  function chart(el, height) {
    return LW.createChart(el, {
      height: height, autoSize: false, width: el.clientWidth,
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#8b95a7", fontSize: 11 },
      grid: { vertLines: { color: "#1a2130" }, horzLines: { color: "#1a2130" } },
      rightPriceScale: { borderColor: "#243045" },
      timeScale: { borderColor: "#243045", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 }, handleScroll: { vertTouchDrag: false }
    });
  }
  var charts = [];
  window.addEventListener("resize", function () {
    charts.forEach(function (c) { c[0].applyOptions({ width: c[1].clientWidth }); });
  });

  // ---- live crypto
  var live = $("live-chart"), current = "BTCUSDT", series = null, ws = null, retry = 2000, last = {}, lastMsg = 0;
  function status(text, cls) {
    var s = $("live-status");
    if (s) { s.textContent = text; s.className = "chip " + cls; }
  }
  function setTicker(sym, price, open) {
    var row = document.querySelector('[data-sym="' + sym + '"]');
    if (!row) return;
    var p = row.querySelector(".px"), c = row.querySelector(".ch");
    var prev = last[sym];
    p.textContent = "$" + fmt(price, digits(price));
    if (open) {
      var ch = price / open - 1;
      c.textContent = pct(ch);
      c.className = "ch " + (ch > 0 ? "up" : ch < 0 ? "down" : "");
    }
    if (prev !== undefined && price !== prev) {
      row.classList.remove("flash-up", "flash-down");
      void row.offsetWidth;
      row.classList.add(price > prev ? "flash-up" : "flash-down");
    }
    last[sym] = price;
  }
  function bar(k) {                                  // a Binance kline as a chart bar, in IST
    return { time: Math.floor(k[0] / 1000) + IST, open: +k[1], high: +k[2], low: +k[3], close: +k[4] };
  }
  function history(sym) {
    return fetch(REST + "?symbol=" + sym + "&interval=1m&limit=240").then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (rows) {
      if (sym !== current) return;
      series.setData(rows.map(bar));
      live.parentNode.querySelector(".chart-title").textContent = sym.replace("USDT", "") + " / USDT · 1-minute candles · swipe back for 4 hours";
    });
  }
  function connect() {
    if (ws) { ws.onclose = null; ws.close(); }
    var streams = COINS.map(function (s) { return s.toLowerCase() + "@miniTicker"; });
    streams.push(current.toLowerCase() + "@kline_1m");
    status("connecting", "wait");
    try { ws = new WebSocket(WS + streams.join("/")); } catch (e) { offline(); return; }
    ws.onopen = function () { retry = 2000; status("LIVE", "live"); };
    ws.onmessage = function (ev) {
      var m = JSON.parse(ev.data), d = m.data || {};
      lastMsg = Date.now();
      if (d.e === "24hrMiniTicker") setTicker(d.s, +d.c, +d.o);
      else if (d.e === "kline" && d.s === current && series) {
        var k = d.k;
        series.update({ time: Math.floor(k.t / 1000) + IST, open: +k.o, high: +k.h, low: +k.l, close: +k.c });
      }
    };
    ws.onclose = function () {
      if (lastMsg) status("reconnecting", "wait"); else status("offline", "off");
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 30000);
    };
    ws.onerror = function () { if (!lastMsg) offline(); };
  }
  function offline() {
    status("offline", "off");
    var note = $("live-note");
    if (note) note.textContent = "Binance's live feed cannot be reached from this device; the prices shown are the server's last recorded minute.";
    (D.crypto_last || []).forEach(function (r) { setTicker(r.sym, r.price, r.open); });
  }
  function pick(sym) {
    current = sym;
    document.querySelectorAll(".tick").forEach(function (b) { b.classList.toggle("on", b.dataset.sym === sym); });
    if (series) series.setData([]);
    history(sym).catch(function () { offline(); });
    connect();
  }
  if (live && LW) {
    var c = chart(live, 260);
    charts.push([c, live]);
    series = c.addCandlestickSeries({ upColor: UP, downColor: DOWN, wickUpColor: UP, wickDownColor: DOWN, borderVisible: false });
    document.querySelectorAll(".tick").forEach(function (b) {
      b.addEventListener("click", function () { pick(b.dataset.sym); });
    });
    (D.crypto_last || []).forEach(function (r) { setTicker(r.sym, r.price, r.open); });
    last = {};
    pick(current);
    setInterval(function () {                        // a silent feed is not a live one
      if (lastMsg && Date.now() - lastMsg > 20000) status("stale", "wait");
    }, 5000);
  }

  // ---- NIFTY 50, end of day
  var nEl = $("nifty-chart");
  if (nEl && LW && D.nifty && D.nifty.length) {
    var nc = chart(nEl, 220);
    charts.push([nc, nEl]);
    nc.applyOptions({ timeScale: { timeVisible: false } });
    var ns = nc.addCandlestickSeries({ upColor: UP, downColor: DOWN, wickUpColor: UP, wickDownColor: DOWN, borderVisible: false });
    ns.setData(D.nifty);
    nc.timeScale().fitContent();
  }

  // ---- the paper book's accounts
  var pEl = $("paper-chart");
  if (pEl && LW && D.paper && Object.keys(D.paper).length) {
    var pc = chart(pEl, 200);
    charts.push([pc, pEl]);
    pc.applyOptions({ timeScale: { timeVisible: false } });
    var colours = ["#4da3ff", "#16c784", "#f5a623", "#b48cff", "#ff7aa8", "#7fdbda"], i = 0, legend = [];
    Object.keys(D.paper).forEach(function (name) {
      var col = colours[i++ % colours.length];
      pc.addLineSeries({ color: col, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }).setData(D.paper[name]);
      legend.push('<span><i style="background:' + col + '"></i>' + esc(name) + "</span>");
    });
    pc.timeScale().fitContent();
    var lg = $("paper-legend");
    if (lg) lg.innerHTML = legend.join("");
  }

  // ---- the themes heatmap
  var heat = $("heat");
  function paint(key) {
    if (!heat) return;
    var rows = (D.themes || []).filter(function (r) { return r[key] !== null && r[key] !== undefined; });
    rows.sort(function (a, b) { return b[key] - a[key]; });
    var top = Math.max.apply(null, rows.map(function (r) { return Math.abs(r[key]); }).concat([0.05]));
    heat.innerHTML = rows.map(function (r) {
      var v = r[key], a = Math.min(1, Math.abs(v) / top) * 0.85 + 0.1;
      var bg = v >= 0 ? "rgba(22,199,132," + a.toFixed(2) + ")" : "rgba(234,57,67," + a.toFixed(2) + ")";
      return '<div class="tile" style="background:' + bg + '"><b>' + esc(r.label) + "</b><span>" + pct(v) + "</span></div>";
    }).join("");
    document.querySelectorAll(".heat-key").forEach(function (b) { b.classList.toggle("on", b.dataset.key === key); });
  }
  document.querySelectorAll(".heat-key").forEach(function (b) {
    b.addEventListener("click", function () { paint(b.dataset.key); });
  });
  paint("rel3M");
})();
