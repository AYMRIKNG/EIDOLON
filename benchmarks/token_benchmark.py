"""Benchmark prédictif : Gemini Live (audio natif) vs mode Low token (STT → texte → TTS local).

Sources des tarifs / taux (doc officielle Gemini API, consultée le 2026-10-06) :
  - pricing.md « Gemini 3.1 Flash Live Preview » (tier payant, par 1M tokens) :
      entrée texte 0.75 $, entrée audio 3.00 $ (≈0.005 $/min),
      sortie texte 4.50 $, sortie audio 12.00 $ (≈0.018 $/min)
  - pricing.md « Gemini 3.5 Flash-Lite » : entrée 0.30 $ (texte/audio), sortie 2.50 $
  - media-resolution.md : audio = 25 tokens / seconde (modèles Gemini 3)
  - live-api/best-practices.md : Live refacture à CHAQUE tour tout le contexte
    (historique conservé en tokens audio bruts) + surcoût des transcriptions
    (facturées au tarif sortie texte).

Mesuré en direct : tokens du system prompt + déclarations d'outils de JARVIS
(count_tokens). Tout le reste est un modèle paramétrable (voir HYPOTHÈSES).
Edge TTS est gratuit (service Microsoft) ; Kokoro est local : coût TTS = 0.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Tarifs (USD / token) ────────────────────────────────────────────────────
LIVE_IN_TEXT, LIVE_IN_AUDIO = 0.75e-6, 3.00e-6
LIVE_OUT_TEXT, LIVE_OUT_AUDIO = 4.50e-6, 12.00e-6
LITE_IN, LITE_OUT = 0.30e-6, 2.50e-6
AUDIO_TPS = 25                       # tokens audio / seconde

# ── HYPOTHÈSES (modifiables) ────────────────────────────────────────────────
USER_SPEECH_S = 4.0                  # durée moyenne d'une phrase utilisateur
REPLY_SPEECH_S = 6.0                 # durée moyenne d'une réponse parlée
TEXT_TPS_SPEECH = 3.3                # tokens texte / seconde de parole (FR ≈ 2.5 mots/s × 1.3)
STT_PROMPT_TOKENS = 40               # consigne de transcription
CONTEXT_CAP = 32_000                 # plafond de contexte retenu (compression glissante)
IDLE_STREAM_S_PER_TURN = 20.0        # écoute micro entre deux tours (scénario « flux continu »)


@dataclass
class Result:
    turns: int
    in_tok: int
    out_tok: int
    cost: float


def measure_system_tokens() -> tuple[int, str]:
    """Tokens réels du prompt système + outils d'EIDOLON (count_tokens)."""
    try:
        from google import genai
        from google.genai import types
        import main
        from ui import EidolonUI

        ui = EidolonUI()
        j = main.EidolonCore(ui)
        cfg = j._build_config()
        key = json.loads((ROOT / "config" / "api_keys.json").read_text(encoding="utf-8"))["gemini_api_key"]
        client = genai.Client(api_key=key)
        sys_txt = cfg.system_instruction if isinstance(cfg.system_instruction, str) else str(cfg.system_instruction)
        tools_txt = json.dumps([t.model_dump(exclude_none=True) for t in (cfg.tools or [])], ensure_ascii=False)
        n_sys = client.models.count_tokens(model="gemini-3.5-flash-lite", contents=sys_txt).total_tokens
        n_tools = client.models.count_tokens(model="gemini-3.5-flash-lite", contents=tools_txt).total_tokens
        return n_sys + n_tools, f"mesuré (prompt {n_sys} + outils {n_tools})"
    except Exception as e:
        return 6000, f"estimé (mesure impossible : {e})"


def simulate_live(turns: int, sys_tok: int, idle: bool) -> Result:
    u_a = USER_SPEECH_S * AUDIO_TPS
    r_a = REPLY_SPEECH_S * AUDIO_TPS
    idle_a = IDLE_STREAM_S_PER_TURN * AUDIO_TPS if idle else 0
    u_t = USER_SPEECH_S * TEXT_TPS_SPEECH
    r_t = REPLY_SPEECH_S * TEXT_TPS_SPEECH
    hist = 0.0
    tin = tout = cost = 0.0
    for _ in range(turns):
        audio_ctx = min(hist, max(0, CONTEXT_CAP - sys_tok))
        new_in = u_a + idle_a
        tin += sys_tok + audio_ctx + new_in
        cost += sys_tok * LIVE_IN_TEXT + (audio_ctx + new_in) * LIVE_IN_AUDIO
        tout += r_a + u_t + r_t
        cost += r_a * LIVE_OUT_AUDIO + (u_t + r_t) * LIVE_OUT_TEXT   # transcriptions entrée+sortie
        hist += new_in + r_a                                        # historique gardé en audio brut
    return Result(turns, int(tin), int(tout), cost)


def simulate_low(turns: int, sys_tok: int) -> Result:
    u_a = USER_SPEECH_S * AUDIO_TPS
    u_t = USER_SPEECH_S * TEXT_TPS_SPEECH
    r_t = REPLY_SPEECH_S * TEXT_TPS_SPEECH
    hist = 0.0
    tin = tout = cost = 0.0
    for _ in range(turns):
        # 1) STT : audio de la phrase seulement, une seule fois
        tin += u_a + STT_PROMPT_TOKENS
        tout += u_t
        cost += (u_a + STT_PROMPT_TOKENS) * LITE_IN + u_t * LITE_OUT
        # 2) LLM texte : prompt + historique TEXTE + phrase
        ctx = min(hist, max(0, CONTEXT_CAP - sys_tok))
        tin += sys_tok + ctx + u_t
        tout += r_t
        cost += (sys_tok + ctx + u_t) * LITE_IN + r_t * LITE_OUT
        hist += u_t + r_t
    return Result(turns, int(tin), int(tout), cost)


def fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def main_bench():
    sys_tok, how = measure_system_tokens()
    lines = [
        "# Benchmark prédictif — Gemini Live vs Low token",
        "",
        f"- Prompt système + outils JARVIS : **{fmt(sys_tok)} tokens** ({how})",
        f"- Phrase utilisateur {USER_SPEECH_S:.0f} s, réponse {REPLY_SPEECH_S:.0f} s, "
        f"audio {AUDIO_TPS} tok/s, plafond de contexte {fmt(CONTEXT_CAP)} tokens",
        "",
        "| Session | Live (entrée / sortie tokens) | Live coût | Live + micro continu | Low token (entrée / sortie) | Low token coût | Économie |",
        "|---|---|---|---|---|---|---|",
    ]
    for n in (10, 30, 100):
        lv = simulate_live(n, sys_tok, idle=False)
        li = simulate_live(n, sys_tok, idle=True)
        lo = simulate_low(n, sys_tok)
        save = (1 - lo.cost / lv.cost) * 100 if lv.cost else 0
        lines.append(
            f"| {n} tours | {fmt(lv.in_tok)} / {fmt(lv.out_tok)} | {lv.cost:.3f} $ | {li.cost:.3f} $ "
            f"| {fmt(lo.in_tok)} / {fmt(lo.out_tok)} | {lo.cost:.4f} $ | **-{save:.0f} %** |"
        )
    # Projection mensuelle : 30 tours / jour, 30 jours
    lv = simulate_live(30, sys_tok, idle=False).cost * 30
    li = simulate_live(30, sys_tok, idle=True).cost * 30
    lo = simulate_low(30, sys_tok).cost * 30
    lines += [
        "",
        f"**Projection mensuelle** (1 session de 30 tours / jour) : Live ≈ **{lv:.2f} $**, "
        f"Live micro continu ≈ **{li:.2f} $**, Low token ≈ **{lo:.2f} $**.",
    ]
    out = "\n".join(lines)
    print(out)
    (ROOT / "benchmarks" / "token_benchmark_result.md").write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main_bench()
