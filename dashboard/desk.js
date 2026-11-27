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
    if (sym === current) spectrumAdd(p, q, buy);
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
    st.push(current.toLowerCase() + "@kline_1m", current.toLowerCase() + "@depth10@100ms");
    status("CONNECTING", "");
    try { ws = new WebSocket(WS + st.join("/")); } catch (e) { offline(); return; }
    ws.onopen = function () { retry = 2000; };
    ws.onmessage = function (ev) {
      var msg = JSON.parse(ev.data) || {}, d = msg.data || {};
      if (!connected) { connected = true; status("LIVE", "live"); log("connected to Binance's public feed: 5 coins, every trade", "g"); }
      lastMsg = Date.now();
      if (msg.stream && msg.stream.indexOf("@depth10") > 0) onDepth(d);
      else if (d.e === "aggTrade") onTrade(d);
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
    times = []; closes = []; kf = null; book = null; specSec = {}; cvd = 0; cvdSec = {};
    ["depth-sym", "spec-sym"].forEach(function (id) { if ($(id)) $(id).textContent = sym.replace("USDT", ""); });
    if (candles) { candles.setData([]); level.setData([]); }
    history(sym).catch(function () { offline(); });
    connect();
  }
  document.querySelectorAll(".coin").forEach(function (b) { b.addEventListener("click", function () { pick(b.dataset.sym); }); });

  // ---------------------------------------------------------------- order-book wall (spot, the 20 best levels)
  var book = null, depthC = canvas("depth");
  function onDepth(d) { book = d; }
  function drawDepth() {
    if (!depthC) return;
    if (!book || !book.bids || !book.asks || !book.bids.length || !book.asks.length) {
      depthC.x.clearRect(0, 0, depthC.w, depthC.h); depthC.x.fillStyle = MU; depthC.x.font = "10px ui-monospace,monospace";
      depthC.x.textAlign = "left"; depthC.x.fillText(connected ? "waiting for the order book" : "no order book without the live feed", 10, 20);
      return;
    }
    var x = depthC.x, w = depthC.w, h = depthC.h;
    var bids = book.bids.map(function (l) { return [+l[0], +l[1]]; }), asks = book.asks.map(function (l) { return [+l[0], +l[1]]; });
    var cb = [], ca = [], sb = 0, sa = 0;
    bids.forEach(function (l) { sb += l[1]; cb.push([l[0], sb]); });
    asks.forEach(function (l) { sa += l[1]; ca.push([l[0], sa]); });
    var lo = bids[bids.length - 1][0], hi = asks[asks.length - 1][0], top = Math.max(sb, sa), mid = (bids[0][0] + asks[0][0]) / 2;
    function X(pr) { return 8 + (pr - lo) / (hi - lo || 1) * (w - 16); }
    function Yd(q) { return h - 16 - Math.sqrt(q / top) * (h - 30); }
    x.clearRect(0, 0, w, h);
    [[cb, "rgba(53,211,154,"], [ca, "rgba(255,77,94,"]].forEach(function (sd) {
      var pts = sd[0], col = sd[1];
      x.beginPath(); x.moveTo(X(pts[0][0]), h - 16);
      pts.forEach(function (pt) { x.lineTo(X(pt[0]), Yd(pt[1])); });
      x.lineTo(X(pts[pts.length - 1][0]), h - 16); x.closePath();
      var g = x.createLinearGradient(0, 0, 0, h); g.addColorStop(0, col + ".55)"); g.addColorStop(1, col + ".05)");
      x.fillStyle = g; x.fill(); x.strokeStyle = col + ".95)"; x.lineWidth = 1.2; x.stroke();
    });
    x.strokeStyle = Y; x.setLineDash([3, 3]); x.beginPath(); x.moveTo(X(mid), 8); x.lineTo(X(mid), h - 16); x.stroke(); x.setLineDash([]);
    x.fillStyle = MU; x.font = "9px ui-monospace,monospace"; x.textAlign = "left"; x.fillText(fmt(lo, dig(lo)), 8, h - 4);
    x.textAlign = "right"; x.fillText(fmt(hi, dig(hi)), w - 8, h - 4); x.textAlign = "center"; x.fillStyle = Y; x.fillText(fmt(mid, dig(mid)), X(mid), h - 4);
    var imb = sb / (sb + sa);
    $("depth-imb").innerHTML = '<span class="' + (imb >= .5 ? "g" : "rd") + '">bids ' + Math.round(imb * 100) + "%</span> · spread " + fmt(asks[0][0] - bids[0][0], dig(mid));
  }

  // ---------------------------------------------------------------- trade-size spectrum and cumulative delta
  var BINS = 12, specSec = {}, shown = new Float32Array(BINS * 2), cvd = 0, cvdSec = {}, specC = canvas("spectrum"), cvdC = canvas("cvd");
  function spectrumAdd(p, q, buy) {
    var usd = p * q, b = Math.max(0, Math.min(BINS - 1, Math.floor((Math.log10(Math.max(usd, 1)) - 1) * 2)));
    var s = Math.floor(Date.now() / 1000), e = specSec[s] || (specSec[s] = new Float32Array(BINS * 2));
    e[b + (buy ? 0 : BINS)] += 1;
    cvd += buy ? q : -q; cvdSec[s] = cvd;
  }
  function drawSpectrum() {
    if (!specC) return;
    var x = specC.x, w = specC.w, h = specC.h, now = Math.floor(Date.now() / 1000), tgt = new Float32Array(BINS * 2), m = 1, i;
    for (var k in specSec) { if (+k < now - 30) { delete specSec[k]; continue; } for (i = 0; i < BINS * 2; i++) tgt[i] += specSec[k][i]; }
    for (i = 0; i < BINS * 2; i++) m = Math.max(m, tgt[i]);
    x.clearRect(0, 0, w, h);
    var bw = (w - 20) / BINS, mid = h / 2 - 6;
    for (i = 0; i < BINS; i++) {
      shown[i] += (Math.sqrt(tgt[i] / m) - shown[i]) * 0.18; shown[i + BINS] += (Math.sqrt(tgt[i + BINS] / m) - shown[i + BINS]) * 0.18;
      var up = shown[i] * (mid - 4), dn = shown[i + BINS] * (mid - 4), x0 = 10 + i * bw + 1;
      var steps = 6;
      for (var s2 = 0; s2 < steps; s2++) {
        var hu = up / steps, hd = dn / steps, a = 0.35 + 0.65 * (s2 + 1) / steps;
        if (hu > .5) { x.fillStyle = "rgba(53,211,154," + a.toFixed(2) + ")"; x.fillRect(x0, mid - (s2 + 1) * hu, bw - 3, hu - 1); }
        if (hd > .5) { x.fillStyle = "rgba(255,77,94," + a.toFixed(2) + ")"; x.fillRect(x0, mid + s2 * hd + 1, bw - 3, hd - 1); }
      }
    }
    x.fillStyle = MU; x.font = "9px ui-monospace,monospace"; x.textAlign = "center";
    ["$10", "$100", "$1k", "$10k", "$100k", "$1M"].forEach(function (lab, j) { x.fillText(lab, 10 + (j * 2 + 1) * bw, h - 2); });
  }
  function drawCvd() {
    if (!cvdC) return;
    var x = cvdC.x, w = cvdC.w, h = cvdC.h, now = Math.floor(Date.now() / 1000), pts = [], last = null, i;
    for (i = 599; i >= 0; i--) { if (cvdSec[now - i] !== undefined) last = cvdSec[now - i]; pts.push(last); }
    for (var k in cvdSec) if (+k < now - 700) delete cvdSec[k];
    var vals = pts.filter(function (v) { return v !== null; });
    x.clearRect(0, 0, w, h);
    if (vals.length < 2) return;
    var lo = Math.min.apply(null, vals.concat([0])), hi = Math.max.apply(null, vals.concat([0])), rng = hi - lo || 1;
    function Yc(v) { return 6 + (1 - (v - lo) / rng) * (h - 12); }
    x.strokeStyle = "#2c1111"; x.beginPath(); x.moveTo(0, Yc(0)); x.lineTo(w, Yc(0)); x.stroke();
    x.beginPath(); var st = false;
    pts.forEach(function (v, j) { if (v === null) return; var xx = j / 599 * w; if (!st) { x.moveTo(xx, Yc(v)); st = true; } else x.lineTo(xx, Yc(v)); });
    x.strokeStyle = cvd >= 0 ? G : RD; x.lineWidth = 1.6; x.stroke();
    $("cvd-now").innerHTML = 'delta <span class="' + (cvd >= 0 ? "g" : "rd") + '">' + (cvd >= 0 ? "+" : "") + fmt(cvd, Math.abs(cvd) >= 100 ? 0 : 2) + "</span>";
  }

  // ---------------------------------------------------------------- futures: liquidations and funding
  var FWS = "wss://fstream.binance.com/stream?streams=!forceOrder@arr/" + COINS.map(function (c) { return c.toLowerCase() + "@markPrice@1s"; }).join("/");
  var fws = null, fConnected = false, fTried = 0, blips = [], marks = {}, radarC = canvas("radar"), sweep = 0;
  function connectF() {
    if (fws) { fws.onclose = null; try { fws.close(); } catch (e) { /* closing */ } }
    fTried += 1;
    try { fws = new WebSocket(FWS); } catch (e) { fOff(); return; }
    fws.onmessage = function (ev) {
      var m = JSON.parse(ev.data) || {}, d = m.data;
      if (!fConnected) { fConnected = true; log("connected to Binance's futures feed: liquidations and funding", "g"); $("fund-note").textContent = "live"; }
      if (d && d.e === "markPriceUpdate") marks[d.s] = d;
      else if (d && d.e === "forceOrder") onLiq(d.o);
    };
    fws.onclose = function () {
      var was = fConnected; fConnected = false;
      if (!was && fTried >= 2) { fOff(); return; }
      setTimeout(connectF, was ? 3000 : 8000);
    };
  }
  function fOff() {
    $("fund-note").textContent = "unreachable";
    $("liq-sum").textContent = "unreachable";
    var n = $("liq-note"); if (n) n.innerHTML = "Binance's futures feed cannot be reached from this device or network, so the radar and the funding board stay empty. Nothing here is made up.";
    var f = $("funding"); if (f) f.innerHTML = '<div class=mu>No futures feed on this device.</div>';
    log("Binance's futures feed is unreachable from this device: radar and funding stay empty", "y");
  }
  function onLiq(o) {
    var usd = +o.q * +(o.ap || o.p), longGone = o.S === "SELL", sym = o.s, a = 0;
    for (var i = 0; i < sym.length; i++) a = (a * 31 + sym.charCodeAt(i)) % 3600;
    blips.push({ t: Date.now(), usd: usd, long: longGone, ang: a / 3600 * Math.PI * 2, sym: sym });
    if (usd >= 50000) { var txt = "LIQUIDATED " + (longGone ? "LONG " : "SHORT ") + sym.replace("USDT", "") + " · $" + fmt(usd / 1000, 0) + "k"; log(txt, longGone ? "rd" : "g"); feed(txt); }
  }
  function drawRadar() {
    if (!radarC) return;
    var x = radarC.x, w = radarC.w, h = radarC.h, cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 8, now = Date.now(), i;
    x.clearRect(0, 0, w, h);
    x.strokeStyle = "rgba(255,106,61,.18)";
    for (i = 1; i <= 4; i++) { x.beginPath(); x.arc(cx, cy, R * i / 4, 0, Math.PI * 2); x.stroke(); }
    x.beginPath(); x.moveTo(cx - R, cy); x.lineTo(cx + R, cy); x.moveTo(cx, cy - R); x.lineTo(cx, cy + R); x.stroke();
    if (fConnected) {
      sweep += 0.02;
      var g = x.createConicGradient ? x.createConicGradient(sweep - 0.6, cx, cy) : null;
      if (g) { g.addColorStop(0, "rgba(255,106,61,0)"); g.addColorStop(0.09, "rgba(255,106,61,.28)"); g.addColorStop(0.1, "rgba(255,106,61,0)");
        x.fillStyle = g; x.beginPath(); x.arc(cx, cy, R, 0, Math.PI * 2); x.fill(); }
    }
    var lo = 0, sh = 0;
    blips = blips.filter(function (b) { return now - b.t < 300000; });
    blips.forEach(function (b) {
      var age = (now - b.t) / 300000, r = R * (0.08 + 0.92 * age), size = 2 + Math.max(0, Math.log10(b.usd) - 2) * 3;
      var px = cx + r * Math.cos(b.ang), py = cy + r * Math.sin(b.ang), a = 1 - age;
      x.fillStyle = (b.long ? "rgba(255,77,94," : "rgba(53,211,154,") + (0.25 + 0.75 * a).toFixed(2) + ")";
      x.beginPath(); x.arc(px, py, size, 0, Math.PI * 2); x.fill();
      if (now - b.t < 1500) { x.strokeStyle = b.long ? RD : G; x.beginPath(); x.arc(px, py, size + (now - b.t) / 60, 0, Math.PI * 2); x.stroke(); }
      if (b.usd >= 50000 && a > .4) { x.fillStyle = "rgba(236,220,207," + a.toFixed(2) + ")"; x.font = "9px ui-monospace,monospace"; x.fillText(b.sym.replace("USDT", ""), px + size + 2, py + 3); }
      if (b.long) lo += b.usd; else sh += b.usd;
    });
    if (fConnected) $("liq-sum").innerHTML = '5 min: <span class=rd>longs $' + fmt(lo / 1000, 0) + 'k</span> · <span class=g>shorts $' + fmt(sh / 1000, 0) + "k</span>";
  }
  function drawFunding() {
    var f = $("funding"); if (!f || !fConnected) return;
    var rows = COINS.filter(function (c) { return marks[c]; }).map(function (c) {
      var u = marks[c], r = +u.r, left = Math.max(0, +u.T - Date.now()), hh = Math.floor(left / 3600000), mm = Math.floor(left / 60000) % 60, ss = Math.floor(left / 1000) % 60;
      return "<tr><td>" + c.replace("USDT", "") + "</td><td class=num>" + fmt(+u.p, dig(+u.p)) + '</td><td class="num ' + (r >= 0 ? "g" : "rd") + '">' + (r * 100).toFixed(4) +
        '%</td><td class="num ' + (r >= 0 ? "g" : "rd") + '">' + (r * 3 * 365 * 100).toFixed(1) + '%</td><td class="num cd">' + hh + ":" + String(mm).padStart(2, "0") + ":" + String(ss).padStart(2, "0") + "</td></tr>";
    }).join("");
    f.innerHTML = '<table><tr><th>COIN</th><th class=num>MARK</th><th class=num>8H RATE</th><th class=num>YEARLY</th><th class=num>NEXT</th></tr>' + rows + "</table>";
  }

  // ---------------------------------------------------------------- correlation, last 60 minutes
  function correlation() {
    var el = $("corr"); if (!el) return;
    Promise.all(COINS.map(function (c) {
      return fetch(REST + "?symbol=" + c + "&interval=1m&limit=61").then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    })).then(function (all) {
      var rets = all.map(function (rows) { var out = []; for (var i = 1; i < rows.length; i++) out.push(Math.log(+rows[i][4] / +rows[i - 1][4])); return out; });
      function c(a, b) {
        var n = Math.min(a.length, b.length), ma = 0, mb = 0, i; for (i = 0; i < n; i++) { ma += a[i]; mb += b[i]; } ma /= n; mb /= n;
        var sab = 0, saa = 0, sbb = 0; for (i = 0; i < n; i++) { sab += (a[i] - ma) * (b[i] - mb); saa += (a[i] - ma) * (a[i] - ma); sbb += (b[i] - mb) * (b[i] - mb); }
        return saa && sbb ? sab / Math.sqrt(saa * sbb) : 0;
      }
      var html = "<div class=lab></div>" + COINS.map(function (k) { return "<div class=lab>" + k.replace("USDT", "") + "</div>"; }).join("");
      COINS.forEach(function (a, i) {
        html += "<div class=lab>" + a.replace("USDT", "") + "</div>";
        COINS.forEach(function (b, j) {
          var v = i === j ? 1 : c(rets[i], rets[j]), al = Math.min(1, Math.abs(v));
          var bg = v >= 0 ? "rgba(255,154,60," + (0.08 + 0.8 * al).toFixed(2) + ")" : "rgba(77,163,255," + (0.08 + 0.8 * al).toFixed(2) + ")";
          html += '<div style="background:' + bg + ";color:" + (al > .55 ? "#1a0503" : "#ecdccf") + '">' + v.toFixed(2) + "</div>";
        });
      });
      el.innerHTML = html;
    }).catch(function () { el.innerHTML = "<div class=mu style='grid-column:1/-1'>The price history cannot be reached from this device.</div>"; });
  }

  // ---------------------------------------------------------------- the sector rotation map (from the server)
  var rotC = canvas("rot"), rotStart = Date.now(), rotDone = false;
  function drawRotation() {
    if (!rotC || rotDone) return;
    if (Date.now() - rotStart > 2600) rotDone = true;
    var data = D.rotation || [], x = rotC.x, w = rotC.w, h = rotC.h;
    x.clearRect(0, 0, w, h);
    if (!data.length) { x.fillStyle = MU; x.font = "11px ui-monospace,monospace"; x.fillText("No NSE history on this server yet.", 10, 20); return; }
    if ($("rot-asof")) $("rot-asof").textContent = "week of " + data[0].asof;
    var heads = data.map(function (d) { return d.trail[d.trail.length - 1]; });
    var sx = Math.max(0.8, Math.max.apply(null, heads.map(function (v) { return Math.abs(v[0] - 100); }))) * 1.25;
    var sy = Math.max(0.8, Math.max.apply(null, heads.map(function (v) { return Math.abs(v[1] - 100); }))) * 1.25;
    function X(v) { return w / 2 + (v - 100) / sx * (w / 2 - 6); }
    function Yr(v) { return h / 2 - (v - 100) / sy * (h / 2 - 6); }
    function quad(p) { return p[0] >= 100 ? (p[1] >= 100 ? 0 : 1) : (p[1] >= 100 ? 3 : 2); }
    var COL = ["53,211,154", "255,201,60", "255,77,94", "77,163,255"], NAMES = ["LEADING", "WEAKENING", "LAGGING", "IMPROVING"];
    [[0, w / 2, 0, w - 6, 12, "right"], [1, w / 2, h / 2, w - 6, h - 6, "right"], [2, 0, h / 2, 6, h - 6, "left"], [3, 0, 0, 6, 12, "left"]].forEach(function (q) {
      x.fillStyle = "rgba(" + COL[q[0]] + ",.07)"; x.fillRect(q[1], q[2], w / 2, h / 2);
      x.fillStyle = MU; x.font = "700 9px ui-monospace,monospace"; x.textAlign = q[5]; x.fillText(NAMES[q[0]], q[3], q[4]);
    });
    x.strokeStyle = "#3a1616"; x.beginPath(); x.moveTo(w / 2, 0); x.lineTo(w / 2, h); x.moveTo(0, h / 2); x.lineTo(w, h / 2); x.stroke();
    var grow = Math.min(1, (Date.now() - rotStart) / 2500);
    var far = data.map(function (d, i) { var p = heads[i]; return [Math.hypot((p[0] - 100) / sx, (p[1] - 100) / sy), i]; })
      .sort(function (a, b) { return b[0] - a[0]; }).slice(0, 10).map(function (a) { return a[1]; });
    x.save(); x.beginPath(); x.rect(0, 0, w, h); x.clip();
    data.forEach(function (d, k) {
      var t = d.trail, n = Math.max(2, Math.ceil(t.length * grow)), last = t[n - 1], lx = X(last[0]), ly = Yr(last[1]), col = COL[quad(last)];
      for (var i = 1; i < n; i++) {
        x.strokeStyle = "rgba(" + col + "," + (0.12 + 0.55 * i / t.length).toFixed(2) + ")"; x.lineWidth = 1.2;
        x.beginPath(); x.moveTo(X(t[i - 1][0]), Yr(t[i - 1][1])); x.lineTo(X(t[i][0]), Yr(t[i][1])); x.stroke();
      }
      x.fillStyle = "rgb(" + col + ")"; x.beginPath(); x.arc(lx, ly, 3.2, 0, Math.PI * 2); x.fill();
      if (far.indexOf(k) >= 0) {
        x.fillStyle = "rgba(236,220,207,.9)"; x.font = "9px ui-monospace,monospace"; x.textAlign = lx > w * .7 ? "right" : "left";
        x.fillText(d.label, lx + (lx > w * .7 ? -5 : 5), ly - 4);
      }
    });
    x.restore();
    var lists = [[], [], [], []];
    data.forEach(function (d, i) { lists[quad(heads[i])].push(d.label); });
    var el = $("rot-list");
    if (el) el.innerHTML = [0, 3, 1, 2].map(function (q) {
      return '<div><b style="color:rgb(' + COL[q] + ')">' + NAMES[q] + "</b> " + (lists[q].length ? lists[q].map(esc).join(", ") : "none") + "</div>";
    }).join("");
  }

  // ---------------------------------------------------------------- the world market clock
  var worldC = canvas("world");
  var MARKETS = [["NSE", "Asia/Kolkata", 555, 930, "#ff9a3c"], ["TOKYO", "Asia/Tokyo", 540, 900, "#ff5fa2"],
                 ["LONDON", "Europe/London", 480, 990, "#4da3ff"], ["NEW YORK", "America/New_York", 570, 960, "#35d39a"]];
  function local(tz, d) {
    var p = {}; new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour12: false, weekday: "short", hour: "2-digit", minute: "2-digit" })
      .formatToParts(d).forEach(function (q) { p[q.type] = q.value; });
    return { wd: p.weekday, min: (+p.hour % 24) * 60 + (+p.minute) };
  }
  function drawWorld() {
    if (!worldC) return;
    var x = worldC.x, w = worldC.w, h = worldC.h, cx = w / 2, cy = h / 2 + 2, R = Math.min(w, h) / 2 - 10, now = new Date();
    var utcMin = now.getUTCHours() * 60 + now.getUTCMinutes() + now.getUTCSeconds() / 60;
    function ang(m) { return (m / 1440) * Math.PI * 2 - Math.PI / 2; }
    x.clearRect(0, 0, w, h);
    x.strokeStyle = "rgba(255,201,60,.55)"; x.lineWidth = 3; x.beginPath(); x.arc(cx, cy, R, 0, Math.PI * 2); x.stroke();
    x.fillStyle = MU; x.font = "9px ui-monospace,monospace"; x.textAlign = "center";
    for (var hh = 0; hh < 24; hh += 3) { var a = ang(hh * 60); x.fillText(String(hh).padStart(2, "0"), cx + (R - 14) * Math.cos(a), cy + (R - 14) * Math.sin(a) + 3); }
    var list = ['<span class=y>● CRYPTO open 24/7</span>'];
    MARKETS.forEach(function (mk, i) {
      var l = local(mk[1], now), off = l.min - (now.getUTCHours() * 60 + now.getUTCMinutes()); if (off > 720) off -= 1440; if (off < -720) off += 1440;
      var o = mk[2] - off, c = mk[3] - off, rr = R - 30 - i * 13;
      var open = ["Sat", "Sun"].indexOf(l.wd) < 0 && l.min >= mk[2] && l.min < mk[3];
      x.strokeStyle = open ? mk[4] : "rgba(141,115,104,.35)"; x.lineWidth = open ? 6 : 4; x.lineCap = "round";
      x.beginPath(); x.arc(cx, cy, rr, ang(o), ang(c)); x.stroke();
      list.push('<span style="color:' + (open ? mk[4] : "#8d7368") + '">● ' + mk[0] + " " + (open ? "OPEN" : "closed") + "</span>");
    });
    x.lineCap = "butt";
    var na = ang(utcMin);
    x.strokeStyle = "#ecdccf"; x.lineWidth = 1.5; x.beginPath(); x.moveTo(cx, cy); x.lineTo(cx + (R - 4) * Math.cos(na), cy + (R - 4) * Math.sin(na)); x.stroke();
    x.fillStyle = "#ff6a3d"; x.beginPath(); x.arc(cx, cy, 4, 0, Math.PI * 2); x.fill();
    $("world-list").innerHTML = list.join("") + "<br>the dial is UTC; regular hours only, holidays not shown";
    $("world-now").textContent = String(now.getUTCHours()).padStart(2, "0") + ":" + String(now.getUTCMinutes()).padStart(2, "0") + " UTC";
  }

  // ---------------------------------------------------------------- the clocks and the loops
  function clock() { $("utc").textContent = istNow() + " IST"; }
  setInterval(clock, 1000); clock();
  setInterval(drawFlow, 1000);
  setInterval(drawFeed, 30000);
  setTimeout(drawFeed, 6000);
  (function frame() { drawRing(); drawRadar(); drawSpectrum(); drawRotation(); requestAnimationFrame(frame); })();
  setInterval(drawDepth, 150);
  setInterval(function () { drawCvd(); drawFunding(); drawWorld(); }, 1000);
  setInterval(correlation, 60000);
  correlation(); drawWorld(); connectF();
  drawFeed();
  pick(current);
})();
