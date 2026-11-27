/*
 * The DESK page's live engine, in the phone's browser. Real data only: Binance's free public streams
 * (24-hour tickers, every aggregated trade, 1-minute candles) and the project's records embedded at build
 * time (#data). Every panel that moves is driven by those trades; with no feed, nothing moves and the page
 * says it is offline. No key, no order: it only watches.
 */
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("data").textContent);
  var LW = window.LightweightCharts;
  var COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
  var WHALE = { BTCUSDT: 100000, ETHUSDT: 100000, SOLUSDT: 25000, BNBUSDT: 25000, XRPUSDT: 25000 };
  var REST = "https://api.binance.com/api/v3/klines";
  var WS = "wss://stream.binance.com:443/stream?streams=";
  var IST = 19800;
  var G = "#35d39a", RD = "#ff4d5e", Y = "#ffc93c", AM = "#ff9a3c", OR = "#ff6a3d", MU = "#8d7368";
  var dpr = Math.min(window.devicePixelRatio || 1, 2);

  function $(id) { return document.getElementById(id); }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function fmt(x, d) { return x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function dig(p) { return p >= 1000 ? 2 : p >= 1 ? 3 : 4; }
  function pct(x, d) { return (x > 0 ? "+" : "") + (x * 100).toFixed(d === undefined ? 2 : d) + "%"; }
  function money(x) { return (x < 0 ? "−" : "+") + "₹" + Math.abs(x).toLocaleString("en-IN", { maximumFractionDigits: 0 }); }
  function istNow() { return new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }); }
  function canvas(id) {
    var c = $(id); if (!c) return null;
    var h = +c.getAttribute("height");
    c.width = Math.round(c.clientWidth * dpr); c.height = Math.round(h * dpr); c.style.height = h + "px";
    var x = c.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { c: c, x: x, w: c.clientWidth, h: h };
  }

  // ---------------------------------------------------------------- the log and the feed
  var logLines = [], feedItems = [];
  function log(text, cls) {
    logLines.unshift('<div><span class=ts>' + istNow() + "</span>" + (cls ? '<span class="' + cls + '">' : "<span>") + esc(text) + "</span></div>");
    logLines = logLines.slice(0, 14);
    var el = $("log"); if (el) el.innerHTML = logLines.join("");
  }
  function feed(text) { feedItems.unshift(text); feedItems = feedItems.slice(0, 10); }
  function drawFeed() {
    var el = $("feed"); if (!el) return;
    var prices = COINS.filter(function (c) { return lastPx[c] !== undefined; }).map(function (c) {
      return c.replace("USDT", "") + " $" + fmt(lastPx[c], dig(lastPx[c])) + (open24[c] ? " " + pct(lastPx[c] / open24[c] - 1) : "");
    });
    var share = $("s-share") ? $("s-share").textContent : "—";
    if (connected && share !== "—") prices.push(current.replace("USDT", "") + " buyers " + share + " (60s)");
    var items = prices.concat(feedItems);
    if (!items.length) items = [connected ? "waiting for the first trades" : "no live feed on this device"];
    var one = '<span class="tag live">LIVE FEED</span>' + items.map(esc).join("  ·  ") + "  ·  ";
    el.innerHTML = one + one;
  }

  // ---------------------------------------------------------------- the records (built on the server)
  (function records() {
    var w = $("wallet"), t = D.paper_total || {};
    if (w) {
      if (t.accounts) {
        var pnl = t.now - t.start, used = t.holding / t.accounts;
        w.innerHTML = '<div class=mu>PAPER P&amp;L SINCE ' + esc(t.since) + "</div>" +
          '<div class="big' + (pnl < 0 ? " neg" : "") + '">' + money(pnl) + "</div>" +
          '<div class=mu>₹' + fmt(t.start, 0) + " → <span class=am>₹" + fmt(t.now, 0) + "</span> · no-tax ledger</div>" +
          '<div class=kv><div>ACCOUNTS<b>' + t.accounts + "</b></div><div>HOLDING<b>" + t.holding + "</b></div><div>DAYS<b>" + t.days + "</b></div></div>" +
          '<div class=mu style="margin-top:6px">capital in use ' + Math.round(used * 100) + '%</div><div class=bar><i style="width:' + Math.round(used * 100) + '%"></i></div>';
      } else w.innerHTML = '<div class=mu>The crypto paper book has no record on this server yet.</div>';
    }
    var b = $("brainbox"), br = D.brain || {};
    if (b) {
      if (br.up || br.big) {
        var rows = "";
        [["up", "NIFTY closes higher"], ["big", "a bigger move than usual"]].forEach(function (q) {
          var f = br[q[0]]; if (!f) return;
          rows += '<div style="margin:6px 0 2px">' + q[1] + ' <span class=r style="float:right"><b class=am>' + Math.round(f.p * 100) +
            "%</b> <span class=mu>usually " + Math.round(f.base * 100) + "%</span></span></div>" +
            '<div class=bar style="height:8px"><i style="width:' + Math.round(f.p * 100) + "%;background:" + (f.p >= f.base ? G : RD) + '"></i></div>';
        });
        b.innerHTML = '<div class=mu>for the session after ' + esc(br.after) + "</div>" + rows +
          '<div class=legend>A walk-forward ridge model on NSE\'s free daily files. Its record: skill on BIG, none on UP (log step 24).</div>';
        if (br.big && $("s-big")) $("s-big").textContent = Math.round(br.big.p * 100) + "%";
      } else b.innerHTML = '<div class=mu>No forecast on this server yet.</div>';
    }
    var s = $("score");
    if (s) s.innerHTML = "<table><tr><th>STRATEGY</th><th>RESULT</th><th>VERDICT</th></tr>" + (D.scoreboard || []).map(function (r) {
      return "<tr><td>" + esc(r.name) + '<div class=mu>' + esc(r.market) + (r.note ? " · " + esc(r.note) : "") + (r.step ? " · step " + r.step : "") +
        "</div></td><td>" + esc(r.result) + '</td><td><span class="v ' + esc(r.verdict) + '">' + esc(r.verdict) + "</span></td></tr>";
    }).join("") + "</table>";
    var tr = $("trades");
    if (tr) tr.innerHTML = (D.trades || []).length ? D.trades.map(function (x) {
      var net = x.net === null || x.net === undefined ? "" : '<span style="float:right" class="' + (x.net >= 0 ? "g" : "rd") + '">' + money(x.net) + "</span>";
      return '<div class=tr><span class="tag">' + esc(x.book) + "</span> " + esc(x.what) + net + '<div class=t>' + esc(x.t) + " · " + esc(x.note) + "</div></div>";
    }).join("") : '<div class=mu>No paper trade recorded on this server yet.</div>';
    (D.research || []).slice(0, 3).reverse().forEach(function (r) { log("research step " + r.step + ": " + r.what, "mu"); });
    if (br.after) log("brain forecast on file for the session after " + br.after, "am");
    if (t.day) log("crypto paper book last processed " + t.day, "am");
    (D.research || []).slice(0, 4).forEach(function (r) { feed("step " + r.step + " · " + r.what); });
  })();

  // ---------------------------------------------------------------- live state
  var current = "BTCUSDT", lastPx = {}, open24 = {}, lastMsg = 0, ws = null, retry = 2000, connected = false;
  var sec = {};                        // per coin: second -> [buyQty, sellQty, lastPrice, count]
  var buckets = {};                    // per coin+side: counts in 5-second buckets, newest last
  var N = 96, act = new Float32Array(N), side = new Int8Array(N);
  COINS.forEach(function (c) { sec[c] = {}; buckets[c + "B"] = new Array(24).fill(0); buckets[c + "S"] = new Array(24).fill(0); });

  function status(text, cls) { var s = $("s-status"); if (s) { s.textContent = text; s.className = "tag " + (cls || ""); } }
  function setCoin(sym, price, open) {
    var b = document.querySelector('.coin[data-sym="' + sym + '"]'); if (!b) return;
    var prev = lastPx[sym];
    b.querySelector(".px").textContent = "$" + fmt(price, dig(price));
    if (open) { var ch = price / open - 1, c = b.querySelector(".ch"); c.textContent = pct(ch); c.className = "ch " + (ch >= 0 ? "g" : "rd"); }
    if (prev !== undefined && prev !== price) { b.classList.remove("flash-up", "flash-down"); void b.offsetWidth; b.classList.add(price > prev ? "flash-up" : "flash-down"); }
    lastPx[sym] = price;
  }
  function onTrade(d) {
    var sym = d.s, p = +d.p, q = +d.q, buy = !d.m, s = Math.floor(d.T / 1000), m = sec[sym];
    var cell = m[s] || (m[s] = [0, 0, p, 0]);
    if (buy) cell[0] += q; else cell[1] += q;
    cell[2] = p; cell[3] += 1;
    buckets[sym + (buy ? "B" : "S")][23] += 1;
    var k = (COINS.indexOf(sym) * 19 + Math.floor(p * 1000 / (p > 100 ? 50 : 1)) + (d.a || 0) % 3) % N;
    act[k] = Math.min(1, act[k] + (q * p > 20000 ? 1 : 0.45)); side[k] = buy ? 1 : -1;
    if (q * p >= WHALE[sym]) {
      var txt = "WHALE " + (buy ? "BUY " : "SELL ") + fmt(q, q >= 100 ? 0 : 3) + " " + sym.replace("USDT", "") + " · $" + fmt(q * p / 1000, 0) + "k @ " + fmt(p, dig(p));
      log(txt, buy ? "g" : "rd"); feed(txt);
    }
  }
  function connect() {
    if (ws) { ws.onclose = null; try { ws.close(); } catch (e) { /* closing */ } }
    var st = [];
    COINS.forEach(function (c) { st.push(c.toLowerCase() + "@miniTicker", c.toLowerCase() + "@aggTrade"); });
    st.push(current.toLowerCase() + "@kline_1m");
    status("CONNECTING", "");
    try { ws = new WebSocket(WS + st.join("/")); } catch (e) { offline(); return; }
    ws.onopen = function () { retry = 2000; };
    ws.onmessage = function (ev) {
      var d = (JSON.parse(ev.data) || {}).data || {};
      if (!connected) { connected = true; status("LIVE", "live"); log("connected to Binance's public feed: 5 coins, every trade", "g"); }
      lastMsg = Date.now();
      if (d.e === "aggTrade") onTrade(d);
      else if (d.e === "24hrMiniTicker") { open24[d.s] = +d.o; setCoin(d.s, +d.c, +d.o); }
      else if (d.e === "kline" && d.s === current) onKline(d.k);
    };
    ws.onclose = function () {
      if (connected) { status("RECONNECTING", ""); log("feed dropped; reconnecting", "y"); } else offline();
      connected = false;
      setTimeout(connect, retry); retry = Math.min(retry * 2, 30000);
    };
  }
  function offline() {
    status("OFFLINE", "");
    if (!offline.said) {
      offline.said = true;
      log("Binance's live feed cannot be reached from this device: nothing here moves without it", "rd");
      (D.crypto_last || []).forEach(function (r) { setCoin(r.sym, r.price, r.open); });
    }
  }
  setInterval(function () { if (connected && Date.now() - lastMsg > 20000) status("STALE", ""); }, 5000);

  // ---------------------------------------------------------------- order flow, per second, last 5 minutes
  var flow = canvas("flow");
  function drawFlow() {
    if (!flow) return;
    var x = flow.x, w = flow.w, h = flow.h, now = Math.floor(Date.now() / 1000), m = sec[current], n = 300;
    x.clearRect(0, 0, w, h);
    var buys = [], sells = [], px = [], maxV = 0, lo = Infinity, hi = -Infinity, last = null;
    for (var i = n - 1; i >= 0; i--) {
      var c = m[now - i]; var b = c ? c[0] : 0, s = c ? c[1] : 0;
      if (c) last = c[2];
      buys.push(b); sells.push(s); px.push(last);
      maxV = Math.max(maxV, b, s);
      if (last !== null) { lo = Math.min(lo, last); hi = Math.max(hi, last); }
    }
    var mid = h * 0.62, amp = h * 0.36, dx = w / n;
    x.strokeStyle = "#2c1111"; x.beginPath(); x.moveTo(0, mid); x.lineTo(w, mid); x.stroke();
    for (i = 0; i < n; i++) {
      if (!maxV) break;
      var bh = Math.sqrt(buys[i] / maxV) * amp, sh = Math.sqrt(sells[i] / maxV) * amp;
      x.fillStyle = "rgba(53,211,154,.75)"; x.fillRect(i * dx, mid - bh, Math.max(1, dx - .3), bh);
      x.fillStyle = "rgba(255,77,94,.75)"; x.fillRect(i * dx, mid, Math.max(1, dx - .3), sh);
    }
    if (hi > lo) {
      x.strokeStyle = Y; x.lineWidth = 1.4; x.beginPath(); var started = false;
      for (i = 0; i < n; i++) {
        if (px[i] === null) continue;
        var yy = 6 + (1 - (px[i] - lo) / (hi - lo)) * (h * 0.34);
        if (!started) { x.moveTo(i * dx, yy); started = true; } else x.lineTo(i * dx, yy);
      }
      x.stroke();
    }
    var b60 = 0, s60 = 0, count = 0;
    for (i = 0; i < 60; i++) { var c2 = m[now - i]; if (c2) { b60 += c2[0]; s60 += c2[1]; } }
    COINS.forEach(function (c) { for (var j = 0; j < 60; j++) { var e = sec[c][now - j]; if (e) count += e[3]; } });
    if (b60 + s60 > 0) {
      var share = b60 / (b60 + s60);
      $("s-share").textContent = Math.round(share * 100) + "%";
      $("flow-share").innerHTML = '<span class="' + (share >= .5 ? "g" : "rd") + '">buyers ' + Math.round(share * 100) + "% · 60s</span>";
    }
    if (connected) $("s-rate").textContent = fmt(count, 0);
    COINS.forEach(function (c) { for (var k in sec[c]) if (+k < now - 400) delete sec[c][k]; });
  }

  // ---------------------------------------------------------------- trade arrival heatmap
  var arr = $("arrivals"), cells = {};
  if (arr) {
    var html = "";
    COINS.forEach(function (c) {
      ["B", "S"].forEach(function (s) {
        html += '<div class=lab>' + c.replace("USDT", "") + " " + s + "</div>";
        for (var i = 0; i < 24; i++) html += '<div class=c id="h-' + c + s + i + '"></div>';
      });
    });
    arr.innerHTML = html;
  }
  function shiftHeat() {
    var maxC = 1, total = 0;
    COINS.forEach(function (c) { ["B", "S"].forEach(function (s) { buckets[c + s].forEach(function (v) { maxC = Math.max(maxC, v); }); }); });
    COINS.forEach(function (c) {
      ["B", "S"].forEach(function (s) {
        var row = buckets[c + s];
        for (var i = 0; i < 24; i++) {
          var el = cells[c + s + i] || (cells[c + s + i] = $("h-" + c + s + i));
          var v = row[i], a = v ? 0.18 + 0.82 * Math.sqrt(v / maxC) : 0;
          if (el) el.style.background = v ? (s === "B" ? "rgba(255,201,60," + a.toFixed(2) + ")" : "rgba(255,106,61," + a.toFixed(2) + ")") : "#1e0b0b";
        }
        total += row[23];
        row.shift(); row.push(0);
      });
    });
    if (connected) $("heat-rate").textContent = fmt(total / 5, 1) + " trades/s";
  }
  setInterval(shiftHeat, 5000);

  // ---------------------------------------------------------------- the order-flow ring
  var ring = canvas("ring"), rot = 0;
  function drawRing() {
    if (!ring) return;
    var x = ring.x, w = ring.w, h = ring.h, cx = w / 2, cy = h / 2, R = Math.min(w * .34, h * .5), r = R * .42, tilt = .5;
    x.clearRect(0, 0, w, h);
    rot += connected ? 0.0025 + 0.01 * (act.reduce(function (a, b) { return a + b; }, 0) / N) : 0;
    for (var i = 0; i < N; i++) {
      var u = i / N * Math.PI * 2 + rot, a = act[i];
      x.beginPath();
      for (var j = 0; j <= 24; j++) {
        var v = j / 24 * Math.PI * 2;
        var px = cx + (R + r * Math.cos(v)) * Math.cos(u), py = cy + ((R + r * Math.cos(v)) * Math.sin(u)) * tilt - r * Math.sin(v) * .8;
        if (j === 0) x.moveTo(px, py); else x.lineTo(px, py);
      }
      var base = "rgba(255,140,80," + (0.06 + 0.06 * Math.max(0, Math.sin(u))).toFixed(3) + ")";
      x.strokeStyle = a > 0.02 ? (side[i] > 0 ? "rgba(53,211,154," : "rgba(255,77,94,") + (0.15 + 0.85 * a).toFixed(2) + ")" : base;
      x.lineWidth = a > 0.02 ? 0.6 + 1.6 * a : 0.6;
      x.stroke();
      act[i] *= 0.965;
    }
    var g = x.createRadialGradient(cx, cy, 2, cx, cy, R * .55);
    g.addColorStop(0, "rgba(10,3,3,1)"); g.addColorStop(1, "rgba(10,3,3,0)");
    x.fillStyle = g; x.beginPath(); x.ellipse(cx, cy, R * .62, R * .3, 0, 0, Math.PI * 2); x.fill();
    var pulse = connected ? 0.5 + 0.5 * Math.sin(Date.now() / 300) : 0;
    x.fillStyle = "rgba(255,106,61," + (0.25 + 0.35 * pulse).toFixed(2) + ")";
    x.beginPath(); x.arc(cx, cy, 15 + 3 * pulse, 0, Math.PI * 2); x.fill();
    x.fillStyle = "#1a0503"; x.font = "700 10px ui-monospace,monospace"; x.textAlign = "center"; x.fillText("ML", cx, cy + 3.5);
    var share = $("s-share").textContent;
    x.fillStyle = "#ecdccf"; x.font = "10px ui-monospace,monospace";
    x.fillText(connected ? current.replace("USDT", "") + " buyers " + share + " · 60s" : "no feed: the ring is still", cx, cy + R * .3 + 26);
    $("ring-sig").textContent = connected ? "trades lit: " + Math.round(act.reduce(function (a, b) { return a + (b > .05 ? 1 : 0); }, 0)) + "/" + N : "";
  }

  // ---------------------------------------------------------------- candles, the Kalman filter, the phase portrait
  var chart = null, candles = null, level = null, closes = [], times = [], kf = null;
  var dc = $("desk-chart");
  if (dc && LW) {
    chart = LW.createChart(dc, {
      height: 230, width: dc.clientWidth,
      layout: { background: { type: "solid", color: "transparent" }, textColor: MU, fontSize: 10 },
      grid: { vertLines: { color: "#1f0c0c" }, horzLines: { color: "#1f0c0c" } },
      rightPriceScale: { borderColor: "#2c1111" }, timeScale: { borderColor: "#2c1111", timeVisible: true },
      crosshair: { mode: 0 }, handleScroll: { vertTouchDrag: false }
    });
    candles = chart.addCandlestickSeries({ upColor: G, downColor: RD, wickUpColor: G, wickDownColor: RD, borderVisible: false });
    level = chart.addLineSeries({ color: Y, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    window.addEventListener("resize", function () { chart.applyOptions({ width: dc.clientWidth }); });
  }
  function kalman(zs) {                  // a local-level Kalman filter; noise levels from the data themselves
    var d = [];
    for (var i = 1; i < zs.length; i++) d.push(zs[i] - zs[i - 1]);
    var v = d.reduce(function (a, b) { return a + b * b; }, 0) / Math.max(1, d.length);
    var R = v * 0.5 || 1e-9, Q = v * 0.15 || 1e-9, xh = zs[0], P = R, out = [], innov = [], K = 0;
    zs.forEach(function (z) { P += Q; innov.push(z - xh); K = P / (P + R); xh += K * (z - xh); P *= (1 - K); out.push(xh); });
    return { level: out, innov: innov, K: K, sd: Math.sqrt(v) };
  }
  function refreshModels() {
    if (closes.length < 20) return;
    kf = kalman(closes);
    if (level) level.setData(times.map(function (t, i) { return { time: t, value: kf.level[i] }; }));
    $("kgain").textContent = "gain K " + kf.K.toFixed(3);
    drawInnov(); drawPhase();
  }
  var innov = canvas("innov");
  function drawInnov() {
    if (!innov || !kf) return;
    var x = innov.x, w = innov.w, h = innov.h, xs = kf.innov.slice(-90), m = Math.max.apply(null, xs.map(Math.abs).concat([1e-9]));
    x.clearRect(0, 0, w, h);
    x.strokeStyle = "#2c1111"; x.beginPath(); x.moveTo(0, h / 2); x.lineTo(w, h / 2); x.stroke();
    var dx = w / xs.length;
    xs.forEach(function (v, i) {
      var bh = v / m * (h / 2 - 6);
      x.fillStyle = v >= 0 ? "rgba(53,211,154,.8)" : "rgba(255,77,94,.8)";
      x.fillRect(i * dx + .5, v >= 0 ? h / 2 - bh : h / 2, Math.max(1, dx - 1.5), Math.abs(bh));
    });
  }
  var phase = canvas("phase");
  function drawPhase() {
    if (!phase || !kf || closes.length < 80) return;
    var x = phase.x, w = phase.w, h = phase.h, pts = [], sd = kf.sd / closes[closes.length - 1];
    for (var i = closes.length - 60; i < closes.length; i++) {
      var r1 = Math.log(closes[i] / closes[i - 1]) / sd, r15 = Math.log(closes[i] / closes[i - 15]) / (sd * Math.sqrt(15));
      pts.push([r1, r15]);
    }
    x.clearRect(0, 0, w, h);
    x.strokeStyle = "#2c1111"; x.beginPath(); x.moveTo(w / 2, 0); x.lineTo(w / 2, h); x.moveTo(0, h / 2); x.lineTo(w, h / 2); x.stroke();
    var reach = Math.max(1.5, Math.min(3.8, Math.max.apply(null, pts.map(function (q) { return Math.max(Math.abs(q[0]), Math.abs(q[1])); }))));
    var sc = (Math.min(w, h) / 2 - 10) / reach;
    pts.forEach(function (p, i) {
      var a = (i + 1) / pts.length, px = w / 2 + Math.max(-3.8, Math.min(3.8, p[0])) * sc, py = h / 2 - Math.max(-3.8, Math.min(3.8, p[1])) * sc;
      if (i) { var q = pts[i - 1]; x.strokeStyle = "rgba(255,154,60," + (a * .6).toFixed(2) + ")"; x.beginPath();
        x.moveTo(w / 2 + Math.max(-3.8, Math.min(3.8, q[0])) * sc, h / 2 - Math.max(-3.8, Math.min(3.8, q[1])) * sc); x.lineTo(px, py); x.stroke(); }
      if (i === pts.length - 1) { x.fillStyle = Y; x.beginPath(); x.arc(px, py, 4, 0, Math.PI * 2); x.fill(); }
    });
    x.fillStyle = MU; x.font = "9px ui-monospace,monospace"; x.textAlign = "left";
    x.fillText("rising", w / 2 + 4, 10); x.fillText("falling", w / 2 + 4, h - 4); x.textAlign = "right"; x.fillText("1m →", w - 2, h / 2 - 3);
  }
  function onKline(k) {
    var t = Math.floor(k.t / 1000) + IST, c = +k.c;
    if (candles) candles.update({ time: t, open: +k.o, high: +k.h, low: +k.l, close: c });
    if (times.length && times[times.length - 1] === t) closes[closes.length - 1] = c;
    else if (!times.length || t > times[times.length - 1]) {
      times.push(t); closes.push(c);
      if (closes.length > 3 && kf) {
        var z = Math.log(closes[closes.length - 2] / closes[closes.length - 3]) / (kf.sd / closes[closes.length - 2]);
        if (Math.abs(z) >= 3) log(current.replace("USDT", "") + " minute move " + pct(closes[closes.length - 2] / closes[closes.length - 3] - 1) + " (" + z.toFixed(1) + " sd)", z > 0 ? "g" : "rd");
      }
      refreshModels();
    }
    $("chart-last").innerHTML = 'last <span class=y>' + fmt(c, dig(c)) + "</span>" + (open24[current] ? ' <span class="' + (c >= open24[current] ? "g" : "rd") + '">' + pct(c / open24[current] - 1) + "</span>" : "");
  }
  function history(sym) {
    return fetch(REST + "?symbol=" + sym + "&interval=1m&limit=240").then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (rows) {
        if (sym !== current) return;
        times = rows.map(function (k) { return Math.floor(k[0] / 1000) + IST; }); closes = rows.map(function (k) { return +k[4]; });
        if (candles) candles.setData(rows.map(function (k) { return { time: Math.floor(k[0] / 1000) + IST, open: +k[1], high: +k[2], low: +k[3], close: +k[4] }; }));
        refreshModels();
      });
  }
  function pick(sym) {
    current = sym;
    document.querySelectorAll(".coin").forEach(function (b) { b.classList.toggle("on", b.dataset.sym === sym); });
    ["flow-sym", "phase-sym"].forEach(function (id) { if ($(id)) $(id).textContent = sym.replace("USDT", ""); });
    if ($("chart-sym")) $("chart-sym").textContent = sym.replace("USDT", "-USDT");
    times = []; closes = []; kf = null;
    if (candles) { candles.setData([]); level.setData([]); }
    history(sym).catch(function () { offline(); });
    connect();
  }
  document.querySelectorAll(".coin").forEach(function (b) { b.addEventListener("click", function () { pick(b.dataset.sym); }); });

  // ---------------------------------------------------------------- the clocks and the loops
  function clock() { $("utc").textContent = istNow() + " IST"; }
  setInterval(clock, 1000); clock();
  setInterval(drawFlow, 1000);
  setInterval(drawFeed, 30000);
  setTimeout(drawFeed, 6000);
  (function frame() { drawRing(); requestAnimationFrame(frame); })();
  drawFeed();
  pick(current);
})();
