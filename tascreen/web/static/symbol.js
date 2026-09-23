// Symbol page chart: daily candles, volume, SMA 50/200, and the selected detection's
// lines, turning points, breakout and measure-rule target.
// Uses TradingView Lightweight Charts 5.2.1 (vendored; Apache-2.0, see static/vendor/).
(function () {
  "use strict";
  const LC = window.LightweightCharts;
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  const box = document.getElementById("chart");
  if (!LC || !box || !data.candles.length) return;

  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const colors = () => ({
    bg: cssVar("--surface"), ink: cssVar("--ink"), muted: cssVar("--muted"), line: cssVar("--line"),
    accent: cssVar("--accent"), up: cssVar("--up"), down: cssVar("--down"),
    sma50: cssVar("--sma50"), sma200: cssVar("--sma200"),
  });
  const withAlpha = (hex, alpha) => {
    const m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  };

  const chart = LC.createChart(box, {
    autoSize: true,
    layout: { attributionLogo: true, fontFamily: "IBM Plex Mono, Consolas, monospace" },
    localization: { locale: "he-IL", dateFormat: "dd/MM/yyyy" },
    rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: { rightOffset: 6, minBarSpacing: 1 },
    crosshair: { mode: LC.CrosshairMode.Normal },
  });
  const candles = chart.addSeries(LC.CandlestickSeries, { priceLineVisible: false });
  candles.setData(data.candles);
  const volume = chart.addSeries(LC.HistogramSeries, {
    priceScaleId: "volume", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false,
  });
  chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const quiet = { lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
  const sma50 = chart.addSeries(LC.LineSeries, quiet);
  const sma200 = chart.addSeries(LC.LineSeries, quiet);
  sma50.setData(data.sma50);
  sma200.setData(data.sma200);
  const markers = LC.createSeriesMarkers(candles, []);

  const indexOf = new Map(data.candles.map((c, i) => [c.time, i]));

  function paint() {
    const c = colors();
    chart.applyOptions({
      layout: { background: { type: LC.ColorType.Solid, color: c.bg }, textColor: c.muted },
      grid: { vertLines: { color: withAlpha(c.line, 0.0) }, horzLines: { color: c.line } },
      rightPriceScale: { borderColor: c.line },
      timeScale: { borderColor: c.line },
    });
    candles.applyOptions({ upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down,
                           wickUpColor: c.up, wickDownColor: c.down });
    volume.setData(data.volume.map((v) => ({ time: v.time, value: v.value,
                                               color: withAlpha(v.up ? c.up : c.down, 0.35) })));
    sma50.applyOptions({ color: c.sma50 });
    sma200.applyOptions({ color: c.sma200 });
  }

  let overlay = { series: [], priceLines: [] };
  let selected = null;

  function clearOverlay() {
    overlay.series.forEach((s) => chart.removeSeries(s));
    overlay.priceLines.forEach((p) => candles.removePriceLine(p));
    overlay = { series: [], priceLines: [] };
    markers.setMarkers([]);
  }

  function draw(det) {
    clearOverlay();
    if (!det) return;
    const c = colors();
    for (const ln of det.lines) {
      let a = { time: ln.x1, value: ln.y1 }, b = { time: ln.x2, value: ln.y2 };
      if (a.time === b.time) continue;
      if (a.time > b.time) [a, b] = [b, a];
      const s = chart.addSeries(LC.LineSeries, { ...quiet, color: c.accent, lineWidth: 2 });
      s.setData([a, b]);
      overlay.series.push(s);
    }
    const marks = [];
    if (det.family === "candle") {            // one arrow on the pattern's last candle
      const bear = det.direction === "bearish";
      if (indexOf.has(det.end)) {
        marks.push({ time: det.end, position: bear ? "aboveBar" : "belowBar",
                     shape: bear ? "arrowDown" : "arrowUp", color: bear ? c.down : c.up, text: det.name });
      }
    } else {
      for (const p of det.points) {
        const i = indexOf.get(p.date);
        if (i === undefined) continue;
        const bar = data.candles[i];
        const above = p.price >= (bar.high + bar.low) / 2;
        marks.push({ time: p.date, position: above ? "aboveBar" : "belowBar", shape: "circle",
                     color: c.accent, size: 0.6, text: p.label || "" });
      }
    }
    if (det.breakout_date && indexOf.has(det.breakout_date)) {
      const bull = det.direction === "bullish";
      marks.push({ time: det.breakout_date, position: bull ? "belowBar" : "aboveBar",
                   shape: bull ? "arrowUp" : "arrowDown", color: bull ? c.up : c.down, text: "פריצה" });
    }
    marks.sort((x, y) => (x.time < y.time ? -1 : x.time > y.time ? 1 : 0));
    markers.setMarkers(marks);
    if (det.breakout_price != null && det.family === "chart" && det.status !== "forming") {
      overlay.priceLines.push(candles.createPriceLine({
        price: det.breakout_price, color: c.accent, lineWidth: 1, lineStyle: LC.LineStyle.Dashed,
        axisLabelVisible: true, title: "פריצה" }));
    }
    if (det.status === "forming") {        // the level a close must cross next session
      if (det.trigger_up != null) overlay.priceLines.push(candles.createPriceLine({
        price: det.trigger_up, color: c.up, lineWidth: 1, lineStyle: LC.LineStyle.Dashed,
        axisLabelVisible: true, title: "פריצה מעל" }));
      if (det.trigger_down != null) overlay.priceLines.push(candles.createPriceLine({
        price: det.trigger_down, color: c.down, lineWidth: 1, lineStyle: LC.LineStyle.Dashed,
        axisLabelVisible: true, title: "פריצה מתחת" }));
    }
    if (det.target != null) {
      overlay.priceLines.push(candles.createPriceLine({
        price: det.target, color: c.muted, lineWidth: 1, lineStyle: LC.LineStyle.Dotted,
        axisLabelVisible: true, title: "יעד (כלל המדידה)" }));
    }
    const first = indexOf.get(det.start) ?? 0;
    const last = data.candles.length - 1;
    const span = Math.max(40, last - first);
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, first - Math.round(span * 0.35)), to: last + 6 });
  }

  function select(id, { scroll = false } = {}) {
    selected = data.detections.find((d) => d.id === id) || null;
    document.querySelectorAll("#picker button").forEach((b) =>
      b.setAttribute("aria-pressed", String(Number(b.dataset.id) === id)));
    document.querySelectorAll("article.det").forEach((a) =>
      a.classList.toggle("selected", Number(a.dataset.id) === id));
    draw(selected);
    if (scroll) box.closest(".panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  paint();
  document.querySelectorAll("#picker button").forEach((b) =>
    b.addEventListener("click", () => select(Number(b.dataset.id))));
  document.querySelectorAll("article.det .show-on-chart").forEach((b) =>
    b.addEventListener("click", () => select(Number(b.dataset.id), { scroll: true })));
  document.addEventListener("themechange", () => { paint(); draw(selected); });

  // Arriving from the screener with #p-<pattern>: show that pattern, chart in view.
  const target = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
  if (target && target.dataset.id !== undefined) {
    select(Number(target.dataset.id));
    requestAnimationFrame(() => box.closest(".panel").scrollIntoView({ block: "start" }));
  } else if (data.detections.length) {
    select(data.detections.find((d) => d.family === "chart")?.id ?? 0);
  } else {
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.candles.length - 260),
                                               to: data.candles.length + 6 });
  }
})();
