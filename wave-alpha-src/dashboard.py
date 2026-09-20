#!/usr/bin/env python3
"""A local, single-page web dashboard for analyze_binance.py.

Same engine as analyze_binance.py (Binance data + Elliott/Fibonacci engine +
optional live LLM reranking with your --context), but as a form you fill in
a browser instead of a terminal command.

Usage:
    python dashboard.py
    (then open http://127.0.0.1:8811 in your browser — it also opens
    automatically)

Nothing here talks to Binance or Anthropic until you click "Analizar" — the
page itself is fully local and static.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from wave_alpha.data.binance_stack import binance_provider_stack
from wave_alpha.domain import Interval
from wave_alpha.llm.client import AnthropicClient, LLMClient
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.prefs.config import load_config, resolve_api_key

# Timeframe presets for the "Modo" selector. Each is a (high, mid, low)
# triple fed straight into run_analysis(timeframes=...) — see
# wave_alpha.pipeline.run_analysis's docstring for what "high/mid/low"
# means. "swing" is wave-alpha's historical default (unchanged); "scalping"
# runs the exact same engine one-and-a-bit tiers lower.
_TIMEFRAME_PRESETS: dict[str, tuple[Interval, Interval, Interval]] = {
    "swing": (Interval.WEEKLY, Interval.DAILY, Interval.HOURLY_4),
    "scalping": (Interval.HOURLY_4, Interval.HOURLY_1, Interval.MINUTE_30),
}
_DEFAULT_MODE = "swing"

_DEFAULT_DATA_DIR = Path.home() / ".wave_alpha"
_BINANCE_CACHE_PATH = _DEFAULT_DATA_DIR / "binance_cache.sqlite"
# Last successful analysis, so restarting the server (or just reopening the
# page) doesn't lose the last result you already paid an API call for. This
# is a courtesy cache for display only — it is never used as an input to a
# new analysis, so there's no risk of it going stale silently.
_LAST_RESULT_PATH = _DEFAULT_DATA_DIR / "dashboard_last_result.json"

app = FastAPI(title="wave-alpha dashboard")


def _save_last_result(data: dict[str, Any]) -> None:
    """Best-effort: never let a disk hiccup break the actual response."""
    try:
        _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {**data, "generated_at": datetime.now().isoformat(timespec="seconds")}
        _LAST_RESULT_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


@app.get("/api/last")
def api_last() -> JSONResponse:
    """The last saved analysis, if any — free to fetch, no API call involved."""
    if not _LAST_RESULT_PATH.exists():
        return JSONResponse({"exists": False})
    try:
        payload = json.loads(_LAST_RESULT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"exists": False})
    return JSONResponse({"exists": True, "data": payload})


class ImageIn(BaseModel):
    base64: str
    media_type: str = "image/png"


class AnalyzeIn(BaseModel):
    ticker: str
    as_of: str | None = None
    llm_mode: str = "disabled"
    context: str | None = None
    alpha: float | None = None
    # "swing" (default, weekly/daily/4h — wave-alpha's original behavior) or
    # "scalping" (4h/1h/30m). See _TIMEFRAME_PRESETS above.
    mode: str = _DEFAULT_MODE
    # Optional screenshots (heatmap, chart, news — as many as you like) the
    # user attaches or pastes. Read by Claude's vision before the analysis,
    # independent of `llm_mode` — you can be in "Solo motor" for the wave
    # reranking and still attach images; interpreting them always needs the
    # API key since OCR/vision isn't something the deterministic engine
    # can do.
    images: list[ImageIn] = Field(default_factory=list)


class DashboardError(RuntimeError):
    """Raised for problems we want shown to the user as plain text, not a
    stack trace (missing API key, bad ticker, network hiccup)."""


def _describe_images_with_vision(*, images: list[ImageIn], api_key: str, model: str) -> str:
    """Ask Claude to turn one or more screenshots (heatmap, chart, news,
    ...) into a short text description we can feed into the wave-alpha
    context, the same way a --context string is used. Kept as a plain
    function (not routed through AnthropicClient) since that wrapper only
    supports text messages; this needs image content blocks.
    """
    import anthropic

    content: list[dict] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": img.media_type, "data": img.base64},
        }
        for img in images
    ]
    plural = "estas imágenes" if len(images) > 1 else "esta imagen"
    content.append(
        {
            "type": "text",
            "text": (
                f"Eres un asistente de trading de BTC/USDT. Describe en un párrafo breve, "
                f"en español, cualquier dato relevante para operar que veas en {plural}: "
                "niveles de precio, clusters de liquidez o liquidaciones, funding rate, "
                "indicadores (RSI, EMAs, etc.), patrones de velas, sentimiento (Fear & Greed) "
                "o titulares de noticias visibles. Sé concreto con los números que veas. Si hay "
                "varias imágenes, junta lo relevante de todas en una sola descripción coherente. "
                "Si no reconoces nada relevante para trading, dilo en una frase corta."
            ),
        }
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": content}],
    )
    for block in message.content:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""


def _build_runner_or_none(llm_mode: LLMMode) -> LLMRunner | None:
    if llm_mode is LLMMode.DISABLED:
        return None
    cfg = load_config()
    api_key = resolve_api_key(cfg)
    if not api_key:
        raise DashboardError(
            "No hay una clave de API de Anthropic configurada. Corre en tu "
            "terminal: python -m wave_alpha.cli.app config set api_key <tu-clave> "
            "— o usa el modo 'Solo motor (sin IA)' en el desplegable."
        )
    client: LLMClient = AnthropicClient(api_key=api_key, model=cfg.llm.scan_model)
    from wave_alpha.llm.cache import LLMResponseCache

    cache = LLMResponseCache(db_path=_DEFAULT_DATA_DIR / "llm_cache.sqlite")
    return LLMRunner(client=client, cache=cache, mode=llm_mode)


def _dec(x: Decimal | None) -> str | None:
    return None if x is None else str(x)


def run_one_analysis(payload: AnalyzeIn) -> dict[str, Any]:
    """Core work for one dashboard request. Split out from the route so
    tests can call it directly (and monkeypatch the pieces it calls)
    without spinning up an HTTP client."""
    ticker = payload.ticker.strip().upper()
    if not ticker:
        raise DashboardError("Falta el ticker (ej. BTCUSDT).")

    resolved_as_of = date.fromisoformat(payload.as_of) if payload.as_of else date.today()
    try:
        llm_mode = LLMMode(payload.llm_mode)
    except ValueError:
        raise DashboardError(f"Modo de IA desconocido: {payload.llm_mode!r}") from None

    tf_mode = payload.mode if payload.mode in _TIMEFRAME_PRESETS else _DEFAULT_MODE
    timeframes = _TIMEFRAME_PRESETS[tf_mode]

    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    provider = binance_provider_stack(db_path=_BINANCE_CACHE_PATH)
    cfg = load_config()

    merged_context = payload.context
    if payload.images:
        api_key = resolve_api_key(cfg)
        if not api_key:
            raise DashboardError(
                "Para leer una imagen necesito tu clave de API de Anthropic configurada "
                "(aunque uses 'Solo motor' para el resto). Corre en tu terminal: "
                "python -m wave_alpha.cli.app config set api_key <tu-clave>"
            )
        try:
            description = _describe_images_with_vision(
                images=payload.images,
                api_key=api_key,
                model=cfg.llm.scan_model,
            )
        except Exception as exc:
            raise DashboardError(f"No pude leer la(s) imagen(es) adjunta(s): {exc}") from exc
        label = "[De las imágenes adjuntas]" if len(payload.images) > 1 else "[De la imagen adjunta]"
        merged_context = "\n".join(p for p in [payload.context, f"{label} {description}"] if p)

    runner = _build_runner_or_none(llm_mode)
    effective_alpha = payload.alpha if payload.alpha is not None else cfg.llm.alpha

    req = AnalyzeRequest(ticker=ticker, as_of=resolved_as_of)
    result = run_analysis(
        req,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner,
        alpha=effective_alpha,
        enabled_opt_in_templates=cfg.templates.enabled_opt_in_names(),
        external_context=merged_context,
        timeframes=timeframes,
    )

    # Best-effort history snapshot, same as analyze_binance.py. Never lets a
    # snapshot failure break the dashboard response.
    #
    # Scalping runs under a suffixed ticker key ("BTCUSDT::scalping") so its
    # snapshots never land in the same history.sqlite rows as Swing's daily
    # analyses. history.service assumes one snapshot per (ticker, as_of)
    # representing THE daily-tier read for that ticker — `wave-alpha
    # history`/`resolve` and the stability badge all key off that. Mixing a
    # 4h/1h/30m read into the same key under the same calendar date would
    # silently corrupt that history for anyone also using the CLI/Swing on
    # the same ticker.
    history_ticker = ticker if tf_mode == _DEFAULT_MODE else f"{ticker}::{tf_mode}"
    try:
        from wave_alpha.history import service as history_service

        history_service.record_snapshot(
            result, history_ticker, resolved_as_of, db_path=_DEFAULT_DATA_DIR / "history.sqlite"
        )
    except Exception:
        pass

    narratives: dict[str, str] = {}
    if result.llm_scan is not None:
        narratives = {c.id: c.narrative for c in result.llm_scan.candidates}

    candidates = []
    for rc in result.ranked_daily[:6]:
        candidates.append(
            {
                "id": rc.candidate_id,
                "pattern": rc.count.pattern,
                "degree": rc.count.degree,
                "engine_score": rc.engine_score,
                "llm_prob": rc.llm_prob,
                "final_score": rc.final_score,
                "narrative": narratives.get(rc.candidate_id),
            }
        )

    signals = [
        {
            "direction": sig.direction,
            "entry": _dec(sig.entry_price),
            "stop": _dec(sig.stop_price),
            "target": _dec(sig.target_price),
            "rr": sig.rr,
            "pattern": sig.count_pattern,
        }
        for sig in result.signals
    ]

    engine_missed = bool(result.llm_scan and result.llm_scan.engine_missed)
    engine_missed_reason = result.llm_scan.engine_missed_reason if result.llm_scan else None

    return {
        "ticker": result.ticker,
        "as_of": result.as_of.isoformat(),
        "coherence": result.coherence.score if result.coherence else None,
        "candidates": candidates,
        "signals": signals,
        "engine_missed": engine_missed,
        "engine_missed_reason": engine_missed_reason,
        "n_atr_skipped": result.n_atr_skipped,
        "mode": tf_mode,
        # High/mid/low tier labels actually used for this run, so the
        # frontend never has to hardcode which timeframes a mode means.
        "timeframe_labels": [tf.value for tf in timeframes],
    }


@app.post("/api/analyze")
def api_analyze(payload: AnalyzeIn) -> JSONResponse:
    try:
        data = run_one_analysis(payload)
    except DashboardError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # network hiccups, bad ticker, etc.
        return JSONResponse({"error": f"No se pudo completar el análisis: {exc}"}, status_code=502)
    _save_last_result(data)
    return JSONResponse(data)


_PAGE = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wave-alpha · Panel BTC</title>
<style>
  :root {
    --bg: #0b0e14; --panel: #131722; --border: #232838; --text: #e6e8ee;
    --muted: #8a90a2; --accent: #5b8cff; --green: #29c46f; --red: #ff5c5c;
    --amber: #f2b84b;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    padding: 24px 16px 64px;
  }
  .wrap { max-width: 780px; margin: 0 auto; }
  h1 { font-size: 1.4rem; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: .9rem; margin-bottom: 24px; }
  .panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px; margin-bottom: 20px;
  }
  label { display: block; font-size: .85rem; color: var(--muted); margin: 14px 0 6px; }
  label:first-child { margin-top: 0; }
  input[type=text], input[type=date], textarea, select {
    width: 100%; background: #0e1220; color: var(--text);
    border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
    font-size: .95rem; font-family: inherit;
  }
  textarea { min-height: 90px; resize: vertical; }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  button {
    margin-top: 18px; width: 100%; background: var(--accent); color: white;
    border: none; border-radius: 8px; padding: 12px; font-size: 1rem;
    font-weight: 600; cursor: pointer;
  }
  button:disabled { opacity: .6; cursor: default; }
  button:hover:not(:disabled) { filter: brightness(1.08); }
  .status { font-size: .85rem; color: var(--muted); margin-top: 10px; min-height: 1.2em; }
  .error { color: var(--red); }
  .badge {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: .8rem; font-weight: 600;
  }
  .badge.long { background: rgba(41,196,111,.15); color: var(--green); }
  .badge.short { background: rgba(255,92,92,.15); color: var(--red); }
  .cand {
    border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px;
    margin-bottom: 12px;
  }
  .cand-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .cand-head .name { font-weight: 600; }
  .cand-head .score { color: var(--accent); font-weight: 700; }
  .bar { height: 6px; border-radius: 3px; background: #1c2133; margin: 8px 0; overflow: hidden; }
  .bar > div { height: 100%; background: var(--accent); }
  .cand .meta { font-size: .78rem; color: var(--muted); }
  .cand .note { font-size: .87rem; margin-top: 8px; color: #cfd3e0; }
  table { width: 100%; border-collapse: collapse; font-size: .88rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 500; }
  .coh { font-size: .9rem; color: var(--muted); margin-bottom: 14px; }
  .coh b { color: var(--text); }
  .disclaimer { text-align: center; color: var(--muted); font-size: .78rem; margin-top: 28px; }
  .summary-box {
    background: rgba(91,140,255,.10); border: 1px solid rgba(91,140,255,.35);
    border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; font-size: .92rem;
    line-height: 1.5;
  }
  .summary-box b { color: var(--text); }
  .missed {
    background: rgba(242,184,75,.12); border: 1px solid rgba(242,184,75,.35);
    color: var(--amber); border-radius: 8px; padding: 10px 12px; font-size: .85rem;
    margin-bottom: 14px;
  }
  a { color: var(--accent); }
  .thumbs { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
  .thumb { position: relative; width: 84px; height: 84px; }
  .thumb img {
    width: 100%; height: 100%; object-fit: cover; border-radius: 8px;
    border: 1px solid var(--border);
  }
  .thumb .rm {
    position: absolute; top: -7px; right: -7px; background: var(--red);
    color: #fff; border-radius: 50%; width: 20px; height: 20px;
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; line-height: 1; cursor: pointer; user-select: none;
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>wave-alpha · Panel BTC</h1>
  <div class="sub">Elliott Wave + Fibonacci sobre datos en vivo de Binance, con tu contexto (noticias, heatmap, funding) sumado por IA.</div>

  <div class="panel">
    <label for="ticker">Ticker (Binance)</label>
    <input type="text" id="ticker" value="BTCUSDT">

    <div class="row">
      <div>
        <label for="asOf">Fecha (opcional)</label>
        <input type="date" id="asOf">
      </div>
      <div>
        <label for="llmMode">Modo IA</label>
        <select id="llmMode">
          <option value="live">Con IA (usa créditos)</option>
          <option value="cached">Con IA (solo caché, sin gastar)</option>
          <option value="disabled" selected>Solo motor (sin IA, gratis)</option>
        </select>
      </div>
    </div>

    <label for="tfMode">Temporalidad</label>
    <select id="tfMode">
      <option value="swing" selected>Swing — semanal / diario / 4h</option>
      <option value="scalping">Scalping — 4h / 1h / 30m</option>
    </select>
    <div class="meta" id="tfHint" style="margin-top:6px;">Usa: semanal / diario / 4h</div>

    <label for="context">Cuéntame lo que ves (en tu propio lenguaje, como si me hablaras a mí) — puedes pegar una o varias capturas aquí mismo con Ctrl+V</label>
    <textarea id="context" placeholder="Ej: Estoy viendo que en el heatmap hay una pared grande de liquidez arriba en 82 mil, el funding está neutral y hay codicia alta según el Fear and Greed. ¿Tú qué le ves a esto? (Ctrl+V para pegar capturas)"></textarea>

    <label for="imageInput">O adjuntar capturas desde archivo (heatmap, gráfico, noticia...) — puedes elegir varias a la vez</label>
    <input type="file" id="imageInput" accept="image/*" multiple>
    <div id="thumbs" class="thumbs"></div>
    <div class="meta" style="margin-top:6px;">Leer imágenes usa tu clave de API (mismo costo que "Con IA"), sin importar el modo que elijas arriba.</div>

    <button id="go">Analizar</button>
    <div class="status" id="status"></div>
  </div>

  <div id="results"></div>

  <div class="disclaimer">⚠ Informativo únicamente. Confirma tu propia tesis. No es asesoría de inversión.</div>
</div>

<script>
const $ = (id) => document.getElementById(id);

let pendingImages = []; // [{base64, mediaType}, ...] — you can attach several

function renderThumbs() {
  const wrap = $('thumbs');
  wrap.innerHTML = '';
  pendingImages.forEach((img, i) => {
    const div = document.createElement('div');
    div.className = 'thumb';
    div.innerHTML = `<img src="data:${img.mediaType};base64,${img.base64}"><div class="rm" data-i="${i}" title="Quitar">×</div>`;
    wrap.appendChild(div);
  });
  wrap.querySelectorAll('.rm').forEach((el) => {
    el.addEventListener('click', () => {
      pendingImages.splice(Number(el.dataset.i), 1);
      renderThumbs();
    });
  });
}

function loadImageFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = reader.result; // "data:image/png;base64,AAAA..."
    const [meta, b64] = dataUrl.split(',');
    const mediaType = meta.match(/data:(.*);base64/)[1];
    pendingImages.push({ base64: b64, mediaType });
    renderThumbs();
  };
  reader.readAsDataURL(file);
}

// Mirrors _TIMEFRAME_PRESETS on the backend, just for the instant hint text
// under the selector — the actual timeframes used always come back from
// the server in data.timeframe_labels, so this never needs to be the
// source of truth, only a preview before you click "Analizar".
const TF_HINTS = {
  swing: 'Usa: semanal / diario / 4h',
  scalping: 'Usa: 4h / 1h / 30m',
};
$('tfMode').addEventListener('change', () => {
  $('tfHint').textContent = TF_HINTS[$('tfMode').value] || '';
});

$('imageInput').addEventListener('change', () => {
  Array.from($('imageInput').files).forEach(loadImageFile);
  $('imageInput').value = ''; // lets you pick the same file(s) again later if needed
});

// Paste one or more screenshots straight into the context box (Ctrl+V) — no
// need to save them to a file first. Works with whatever the OS put on the
// clipboard (a Snipping Tool capture, a copied image from the browser, etc).
// If you paste several images one after another, each Ctrl+V just adds to
// the pile below instead of replacing the previous one.
$('context').addEventListener('paste', (e) => {
  const items = e.clipboardData ? e.clipboardData.items : null;
  if (!items) return;
  let hadImage = false;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      hadImage = true;
      loadImageFile(item.getAsFile());
    }
  }
  if (hadImage) e.preventDefault();
  // No image on the clipboard (plain text copy) — let the default paste happen.
});

$('go').addEventListener('click', async () => {
  const btn = $('go');
  const status = $('status');
  const results = $('results');
  btn.disabled = true;
  status.className = 'status';
  status.textContent = 'Analizando… (puede tardar unos segundos)';
  results.innerHTML = '';

  const body = {
    ticker: $('ticker').value,
    as_of: $('asOf').value || null,
    llm_mode: $('llmMode').value,
    mode: $('tfMode').value,
    context: $('context').value || null,
    images: pendingImages.map((img) => ({ base64: img.base64, media_type: img.mediaType })),
  };

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      status.className = 'status error';
      status.textContent = data.error || 'Error desconocido.';
      return;
    }
    status.textContent = '';
    render(data);
  } catch (e) {
    status.className = 'status error';
    status.textContent = 'No se pudo conectar (revisa tu internet / que el servidor siga corriendo).';
  } finally {
    btn.disabled = false;
  }
});

function pct(x) { return x === null || x === undefined ? 'n/a' : Math.round(x * 100) + '%'; }

// Friendlier Spanish labels for the pattern names, so the cards don't force
// you to already know Elliott Wave terminology to get the gist.
const PATTERN_LABELS = {
  impulse: 'Impulso (tendencia)',
  zigzag: 'Zigzag (corrección simple)',
  flat: 'Plano (lateral)',
  expanded_flat: 'Plano expandido (lateral amplio)',
  contracting_triangle: 'Triángulo (indecisión)',
  ending_diagonal: 'Diagonal final (posible agotamiento)',
  leading_diagonal: 'Diagonal inicial',
  wxy: 'Corrección compleja (W-X-Y)',
};
function patternLabel(p) { return PATTERN_LABELS[p] || p; }

// A quick, non-technical verdict computed from the numbers themselves (no
// extra API call, so it costs nothing) — meant to be read before the
// detailed cards below, not instead of them.
function summarize(data) {
  if (!data.candidates.length) return null;
  const top = data.candidates[0];

  const dirCounts = {};
  for (const s of data.signals) dirCounts[s.direction] = (dirCounts[s.direction] || 0) + 1;
  const dirs = Object.keys(dirCounts);
  let sesgo = 'sin señales claras de dirección todavía';
  if (dirs.length === 1) {
    sesgo = dirs[0] === 'short'
      ? 'a la <b>baja</b> (las señales derivadas apuntan a SHORT)'
      : 'al <b>alza</b> (las señales derivadas apuntan a LONG)';
  } else if (dirs.length > 1) {
    sesgo = 'mixto (hay señales en ambas direcciones, sin un ganador claro)';
  }

  let confianza = 'baja';
  if (top.llm_prob !== null && top.llm_prob !== undefined) {
    if (top.llm_prob >= 0.5) confianza = 'alta';
    else if (top.llm_prob >= 0.25) confianza = 'media';
  }

  let acuerdo = 'baja';
  if (data.coherence !== null && data.coherence !== undefined) {
    if (data.coherence >= 0.7) acuerdo = 'alta';
    else if (data.coherence >= 0.4) acuerdo = 'media';
  }

  const tfLabel = (data.timeframe_labels && data.timeframe_labels.length)
    ? data.timeframe_labels.join('/')
    : (data.mode === 'scalping' ? '4h/1h/30m' : 'semanal/diario/4h');
  const modeLabel = data.mode === 'scalping' ? 'Scalping' : 'Swing';

  return `Modo <b>${modeLabel}</b> (${tfLabel}). Sesgo general: ${sesgo}. Confianza de la IA en el mejor escenario (${patternLabel(top.pattern)}): <b>${confianza}</b> (${pct(top.llm_prob)}). Acuerdo entre temporalidades: <b>${acuerdo}</b>.`;
}

function render(data, opts) {
  opts = opts || {};
  const results = $('results');
  let html = '';

  if (opts.cached) {
    const when = data.generated_at ? new Date(data.generated_at).toLocaleString('es-MX') : '';
    html += `<div class="status" style="margin-bottom:10px;">📌 Último análisis guardado${when ? ' — ' + when : ''} (no cuesta nada verlo). Dale "Analizar" para uno nuevo con datos de ahora.</div>`;
    // Keep the selector in sync with what was actually analyzed, so it
    // doesn't silently show "Swing" while displaying a cached Scalping run.
    if (data.mode && $('tfMode').value !== data.mode) {
      $('tfMode').value = data.mode;
      $('tfHint').textContent = TF_HINTS[data.mode] || '';
    }
  }

  html += `<div class="panel">`;

  const summary = summarize(data);
  if (summary) {
    html += `<div class="summary-box">${summary}</div>`;
  }

  html += `<div class="coh"><b>${data.ticker}</b> · as-of ${data.as_of}` +
          (data.coherence !== null ? ` · coherencia multi-TF: <b>${data.coherence.toFixed(2)}</b>` : '') +
          `</div>`;

  if (data.engine_missed) {
    html += `<div class="missed">⚠ La IA marcó que podría haber un conteo mejor no detectado por el motor: ${data.engine_missed_reason || ''}</div>`;
  }

  if (data.candidates.length === 0) {
    html += `<div class="status">Sin candidatos (datos insuficientes para este ticker/fecha).</div>`;
  }
  for (const c of data.candidates) {
    html += `<div class="cand">
      <div class="cand-head">
        <span class="name">${c.id} · ${patternLabel(c.pattern)} (grado ${c.degree})</span>
        <span class="score">final ${c.final_score.toFixed(2)}</span>
      </div>
      <div class="bar"><div style="width:${Math.min(100, c.final_score*100)}%"></div></div>
      <div class="meta">motor: ${c.engine_score.toFixed(2)} · IA: ${pct(c.llm_prob)}</div>
      ${c.narrative ? `<div class="note">${c.narrative}</div>` : ''}
    </div>`;
  }

  if (data.signals.length > 0) {
    html += `<label style="margin-top:18px">Señales derivadas</label>`;
    html += `<table><tr><th>Dirección</th><th>Entrada</th><th>Stop</th><th>Target</th><th>R:R</th></tr>`;
    for (const s of data.signals) {
      html += `<tr>
        <td><span class="badge ${s.direction}">${s.direction.toUpperCase()}</span></td>
        <td>${s.entry}</td><td>${s.stop}</td><td>${s.target}</td><td>${s.rr.toFixed(2)}</td>
      </tr>`;
    }
    html += `</table>`;
  }

  html += `</div>`;
  results.innerHTML = html;
}

// On load, show the last saved analysis (if any) for free — no need to pay
// for a fresh API call just to see what you already got last time.
(async function loadLast() {
  try {
    const res = await fetch('/api/last');
    const payload = await res.json();
    if (payload.exists) render(payload.data, { cached: true });
  } catch (e) {
    // Silent — this is a convenience, not a critical path.
  }
})();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # no-store: this page changes whenever we update dashboard.py, and a
    # stale cached copy silently reverting to old behavior (as happened
    # with the single- vs multi-image paste logic) is worse than the tiny
    # cost of never caching a page this cheap to regenerate.
    return HTMLResponse(content=_PAGE, headers={"Cache-Control": "no-store, must-revalidate"})


def _open_browser_when_ready(url: str) -> None:
    import time
    import urllib.request

    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    webbrowser.open(url)


if __name__ == "__main__":
    import uvicorn

    # 8811 chosen to avoid colliding with the user's other local services
    # (they already run something on 8000 via Docker).
    port = 8811
    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    print(f"Abriendo el panel en {url} (Ctrl+C aquí para cerrarlo)")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except OSError as exc:
        print(
            f"\nNo se pudo abrir el puerto {port} ({exc}).\n"
            "Es posible que ya haya otra copia de este panel corriendo — "
            "revisa si tienes otra ventana de terminal abierta con abrir_panel.bat."
        )
        raise
