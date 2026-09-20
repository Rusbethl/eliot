(function (global) {
  // Locked classic candle colors. Do NOT retint with brand palette.
  // (See docs/superpowers/specs/2026-05-09-web-ui-redesign-design.md §3.)
  var CANDLE_UP = "#26A69A";
  var CANDLE_DOWN = "#EF5350";

  // Wave-label hue map (matches CSS .wlabel.* and the spec's wave color map).
  var WAVE_HUE = {
    "1": "#22D3EE",  "2": "#FB923C",  "3": "#F472B6",
    "4": "#A3E635",  "5": "#A855F7",
    "A": "#2DD4BF",  "B": "#FACC15",  "C": "#F43F5E",
  };

  function pivotColor(label) {
    if (!label) return "#60A5FA"; // neutral blue fallback
    return WAVE_HUE[String(label).toUpperCase()] || "#60A5FA";
  }

  function renderChart(containerId, payload) {
    var el = document.getElementById(containerId);
    if (!el || !global.LightweightCharts) {
      return;
    }
    el.innerHTML = "";

    var chart = global.LightweightCharts.createChart(el, {
      layout: {
        background: { color: "#000000" },     // matches --bg-base
        textColor: "#C7D2DE",                  // matches --fg-secondary
      },
      grid: {
        vertLines: { color: "rgba(34,211,238,0.06)" }, // --border-subtle hue
        horzLines: { color: "rgba(34,211,238,0.06)" },
      },
      timeScale: { timeVisible: false, borderColor: "rgba(34,211,238,0.14)" },
      rightPriceScale: { borderColor: "rgba(34,211,238,0.14)" },
      crosshair: {
        vertLine: { color: "rgba(34,211,238,0.35)", width: 1 },
        horzLine: { color: "rgba(34,211,238,0.35)", width: 1 },
      },
    });

    var series = chart.addSeries(global.LightweightCharts.CandlestickSeries, {
      upColor: CANDLE_UP,
      downColor: CANDLE_DOWN,
      borderUpColor: CANDLE_UP,
      borderDownColor: CANDLE_DOWN,
      wickUpColor: CANDLE_UP,
      wickDownColor: CANDLE_DOWN,
    });
    series.setData(payload.bars || []);

    // Pivot markers — colored by wave label if the payload exposes one.
    if (payload.markers && payload.markers.length) {
      var coloredMarkers = payload.markers.map(function (m) {
        return Object.assign({}, m, {
          color: m.color || pivotColor(m.text || m.label || ""),
        });
      });
      global.LightweightCharts.createSeriesMarkers(series, coloredMarkers);
    }

    // Optional reference lines (entry/stop/target/fib) if the payload provides them.
    var lineDefs = [
      { key: "entry",  color: "#7C8BA3", title: "entry" },
      { key: "stop",   color: "#FB7185", title: "stop"  },
      { key: "target", color: "#4ADE80", title: "target"},
      { key: "fib",    color: "#818CF8", title: "fib"   },
    ];
    lineDefs.forEach(function (def) {
      var v = payload[def.key];
      if (typeof v === "number") {
        series.createPriceLine({
          price: v,
          color: def.color,
          lineWidth: 1,
          lineStyle: global.LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: def.title,
        });
      }
    });
  }

  global.WaveAlpha = global.WaveAlpha || {};
  global.WaveAlpha.renderChart = renderChart;
  if (global._waveAlphaPending) {
    renderChart("chart-canvas", global._waveAlphaPending);
    global._waveAlphaPending = null;
  }
})(window);
