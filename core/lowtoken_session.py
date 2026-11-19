"""EIDOLON — Low-token voice session (STT → text LLM → local/free TTS).

Drop-in replacement for a Gemini Live session object. It exposes the exact
subset of the Live API that `main.JarvisLive` uses:

    send_realtime_input(audio=Blob | text=str)
    send_client_content(turns=..., turn_complete=bool)
    send_tool_response(function_responses=[FunctionResponse])
    receive()  -> async iterator of Live-shaped responses
    cancel()   -> abort the turn in flight (UI interrupt)

so every existing piece of `main.py` (mic gating, echo guard, playback,
visualiser, tools, memory, proactive messages, briefing) keeps working
unchanged. Only the transport differs:

    mic PCM 16 kHz ─► local VAD ─► utterance WAV ─► Gemini STT (text)
                                                   │
                     text history + tools ◄────────┘
                               │
             streaming text LLM (function calling supported)
                               │  sentence by sentence
                               ▼
               Edge TTS / Kokoro ─► PCM 24 kHz int16 ─► main playback queue

Only text is exchanged with the LLM, so the conversation history stays cheap:
no audio is ever re-sent on later turns.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import time
import wave
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable, Optional

import aiohttp
import numpy as np
from google import genai
from google.genai import types

from memory import config_manager

# ── Audio constants (must match main.py) ─────────────────────────────────────
IN_RATE = 16_000            # mic PCM from main._listen_audio
OUT_RATE = 24_000           # main._play_audio RawOutputStream rate
SLICE = 2400                # 50 ms @ 24 kHz int16 — same slicing as Live path

# ── Utterance detection ─────────────────────────────────────────────────────
SPEECH_RMS = 0.012          # float RMS above this = voice (zeroed chunks are 0)
END_SILENCE_S = 0.65        # silence that closes an utterance
MIN_SPEECH_S = 0.30         # shorter bursts are ignored (clicks, coughs)
MAX_UTTER_S = 30.0          # hard cap, then force-close
STALL_S = 0.9               # no chunk at all for this long while in speech → close

DEFAULT_TEXT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_STT_MODEL = "gemini-3.5-flash-lite"

_SENT_END = re.compile(r"([.!?…:;\n])\s")
_MAX_CLAUSE = 140           # flush on a comma once a clause gets this long


# ── Live-shaped response helpers ────────────────────────────────────────────

def _resp(*, data: bytes | None = None, out_text: str | None = None,
          in_text: str | None = None, turn_complete: bool = False,
          tool_call: Any = None) -> SimpleNamespace:
    sc = None
    if out_text is not None or in_text is not None or turn_complete:
        sc = SimpleNamespace(
            output_transcription=SimpleNamespace(text=out_text) if out_text else None,
            input_transcription=SimpleNamespace(text=in_text) if in_text else None,
            turn_complete=turn_complete,
            interrupted=False,
            model_turn=None,
        )
    return SimpleNamespace(
        data=data,
        server_content=sc,
        tool_call=tool_call,
        session_resumption_update=None,
        go_away=None,
        usage_metadata=None,
    )


def _pcm16_to_wav(pcm: bytes, rate: int = IN_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def _turns_to_text(turns: Any) -> str:
    """Flatten whatever main.py passes to send_client_content into plain text."""
    if turns is None:
        return ""
    if isinstance(turns, (list, tuple)):
        return "\n".join(t for t in (_turns_to_text(x) for x in turns) if t)
    if isinstance(turns, dict):
        parts = turns.get("parts") or []
        return "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("text"))
    parts = getattr(turns, "parts", None)
    if parts:
        return "\n".join(p.text for p in parts if getattr(p, "text", None))
    if isinstance(turns, str):
        return turns
    return ""


def _sanitize_tools(tools: Any) -> list:
    """Live-only fields (behavior=NON_BLOCKING) are not part of generateContent."""
    out = []
    for t in tools or []:
        try:
            tool = t if isinstance(t, types.Tool) else types.Tool.model_validate(t)
        except Exception:
            continue
        decls = []
        for fd in tool.function_declarations or []:
            try:
                decls.append(fd.model_copy(update={"behavior": None}))
            except Exception:
                decls.append(fd)
        if decls:
            out.append(types.Tool(function_declarations=decls))
    return out


def _to_openai_schema(obj: Any) -> Any:
    """Recursively convert Google GenAI uppercase type strings to JSON schema lowercase."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.lower()
            else:
                out[k] = _to_openai_schema(v)
        return out
    elif isinstance(obj, list):
        return [_to_openai_schema(x) for x in obj]
    return obj


async def _resolve_lm_studio_model(session: aiohttp.ClientSession, base_url: str,
                                   configured_model: str) -> str:
    """If no model is configured, query LM Studio's /v1/models to pick the loaded one."""
    if configured_model:
        return configured_model
    try:
        async with session.get(f"{base_url}/v1/models",
                               timeout=aiohttp.ClientTimeout(total=2.0)) as r:
            if r.status == 200:
                data = await r.json()
                models = data.get("data") or []
                if models and isinstance(models, list) and "id" in models[0]:
                    return models[0]["id"]
    except Exception:
        pass
    return "local-model"


# ── TTS bridge ──────────────────────────────────────────────────────────────

class _TTS:
    """Turns one sentence into 24 kHz mono int16 PCM. Re-reads config each call
    so a voice picked in Settings applies on the very next sentence."""

    def __init__(self):
        self._kokoro = None
        self._kokoro_key = None

    async def synth(self, text: str) -> bytes:
        cfg = config_manager.load_api_keys()
        engine = str(cfg.get("tts_engine", "edgetts")).lower()
        voice = cfg.get("tts_voice") or ""
        if engine == "kokoro":
            return await asyncio.to_thread(self._kokoro_pcm, text, voice or "ff_siwis",
                                           float(cfg.get("tts_speed", 1.0)))
        return await self._edge_pcm(text, voice or "fr-FR-HenriNeural")

    async def _edge_pcm(self, text: str, voice: str) -> bytes:
        from core.tts import EdgeTTSEngine
        eng = EdgeTTSEngine(voice=voice)
        mp3 = await eng._synth_with_retry(text)
        if not mp3:
            return b""
        return await asyncio.to_thread(_decode_to_pcm24k, mp3)

    def _kokoro_pcm(self, text: str, voice: str, speed: float) -> bytes:
        from core.tts import KokoroTTSEngine, _to_numpy, _compress_silence
        key = (voice, speed)
        if self._kokoro is None or self._kokoro_key != key:
            self._kokoro = KokoroTTSEngine(voice=voice, speed=speed)
            self._kokoro_key = key
        k = self._kokoro
        with k._lock:
            if k._pipeline is None:
                k._init()
        chunks = []
        for _, _, audio in k._pipeline(text, voice=k.voice, speed=k.speed):
            if audio is not None:
                arr = _compress_silence(_to_numpy(audio))
                if arr.size:
                    chunks.append(arr)
        if not chunks:
            return b""
        arr = np.clip(np.concatenate(chunks), -1.0, 1.0)
        return (arr * 32767.0).astype(np.int16).tobytes()


def _decode_to_pcm24k(audio_bytes: bytes) -> bytes:
    import miniaudio
    dec = miniaudio.decode(audio_bytes, output_format=miniaudio.SampleFormat.SIGNED16,
                           nchannels=1, sample_rate=OUT_RATE)
    return bytes(dec.samples.tobytes() if hasattr(dec.samples, "tobytes") else dec.samples)


# ── Session ─────────────────────────────────────────────────────────────────

class LowTokenSession:
    def __init__(self, client: genai.Client, live_config: Any,
                 text_model: str, stt_model: str,
                 llm_source: str = "API keys",
                 lm_studio_server: str = "http://localhost",
                 lm_studio_port: int = 1234,
                 lm_studio_model: str = "",
                 logger: Callable[[str], None] = print):
        self._client = client
        self._log = logger
        self._text_model = text_model
        self._stt_model = stt_model
        self._llm_source = llm_source
        self._lm_studio_server = lm_studio_server
        self._lm_studio_port = lm_studio_port
        self._lm_studio_model = lm_studio_model
        self._sys = getattr(live_config, "system_instruction", None)
        self._tools = _sanitize_tools(getattr(live_config, "tools", None))
        self._history: list[types.Content] = []
        self._out: asyncio.Queue = asyncio.Queue()
        self._tool_future: Optional[asyncio.Future] = None
        self._turn_task: Optional[asyncio.Task] = None
        self._turn_lock = asyncio.Lock()
        self._tts = _TTS()

        # utterance state
        self._buf = bytearray()
        self._in_speech = False
        self._speech_s = 0.0
        self._silence_s = 0.0
        self._last_chunk = time.monotonic()
        self._watchdog = asyncio.get_event_loop().create_task(self._stall_watch())

    # ── Live API surface ────────────────────────────────────────────────────
    async def send_realtime_input(self, *, audio: Any = None, text: str | None = None, **_):
        if text:
            await self._start_turn(text, typed=True)
            return
        if audio is None:
            return
        pcm = getattr(audio, "data", None) or b""
        if pcm:
            await self._feed(pcm)

    async def send_client_content(self, *, turns: Any = None, turn_complete: bool = True, **_):
        txt = _turns_to_text(turns).strip()
        if not txt:
            return
        if turn_complete:
            await self._start_turn(txt, typed=True)
        else:
            self._history.append(types.Content(role="user", parts=[types.Part(text=txt)]))

    async def send_tool_response(self, *, function_responses: Any = None, **_):
        fut = self._tool_future
        if fut and not fut.done():
            fut.set_result(list(function_responses or []))

    async def receive(self) -> AsyncIterator[SimpleNamespace]:
        while True:
            yield await self._out.get()

    def cancel(self) -> None:
        t = self._turn_task
        if t and not t.done():
            t.cancel()          # _run_turn emits turn_complete on cancellation
        else:
            # main.interrupt() waits for a turn_complete to clear its flag.
            self._out.put_nowait(_resp(turn_complete=True))

    async def close(self):
        self.cancel()
        if self._watchdog:
            self._watchdog.cancel()

    # ── Utterance detection ─────────────────────────────────────────────────
    async def _feed(self, pcm: bytes):
        self._last_chunk = time.monotonic()
        dur = len(pcm) / 2 / IN_RATE
        voiced = _rms(pcm) >= SPEECH_RMS
        if voiced:
            if not self._in_speech:
                self._in_speech = True
                self._speech_s = 0.0
                self._buf.clear()
            self._speech_s += dur
            self._silence_s = 0.0
            self._buf.extend(pcm)
        elif self._in_speech:
            self._buf.extend(pcm)
            self._silence_s += dur
            if self._silence_s >= END_SILENCE_S:
                await self._close_utterance()
                return
        if self._in_speech and len(self._buf) / 2 / IN_RATE >= MAX_UTTER_S:
            await self._close_utterance()

    async def _stall_watch(self):
        while True:
            await asyncio.sleep(0.2)
            if self._in_speech and time.monotonic() - self._last_chunk > STALL_S:
                await self._close_utterance()

    async def _close_utterance(self):
        pcm, speech = bytes(self._buf), self._speech_s
        self._buf.clear()
        self._in_speech = False
        self._silence_s = 0.0
        self._speech_s = 0.0
        if speech < MIN_SPEECH_S:
            return
        asyncio.get_event_loop().create_task(self._transcribe_and_answer(pcm))

    async def _transcribe_and_answer(self, pcm: bytes):
        t0 = time.perf_counter()
        try:
            r = await self._client.aio.models.generate_content(
                model=self._stt_model,
                contents=[
                    types.Part.from_bytes(data=_pcm16_to_wav(pcm), mime_type="audio/wav"),
                    "Transcris mot pour mot ce que dit la personne, dans sa langue. "
                    "Ne réponds rien d'autre. Si aucune parole intelligible, renvoie une chaîne vide.",
                ],
                config=types.GenerateContentConfig(temperature=0.0),
            )
            text = (r.text or "").strip().strip('"').strip()
        except Exception as e:
            self._log(f"[LowToken] STT error: {e}")
            return
        self._log(f"[LowToken] STT {time.perf_counter() - t0:.2f}s: {text!r}")
        if text:
            await self._start_turn(text, typed=False)

    # ── Turn generation ─────────────────────────────────────────────────────
    async def _start_turn(self, user_text: str, typed: bool):
        # A new utterance supersedes whatever was being said.
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except BaseException:
                pass
        self._turn_task = asyncio.get_event_loop().create_task(self._run_turn(user_text, typed))

    async def _run_turn(self, user_text: str, typed: bool):
        sentences: asyncio.Queue = asyncio.Queue()
        tts_task = asyncio.get_event_loop().create_task(self._tts_worker(sentences))
        try:
            if not typed:
                await self._out.put(_resp(in_text=user_text))
            self._history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
            if self._llm_source in ("LM Studio", "Local"):
                await self._generate_lm_studio(sentences)
            else:
                await self._generate(sentences)
        except asyncio.CancelledError:
            tts_task.cancel()
            await self._out.put(_resp(turn_complete=True))
            raise
        except Exception as e:
            self._log(f"[LowToken] LLM error: {e}")
            await sentences.put("Désolé, une erreur est survenue.")
        await sentences.put(None)
        try:
            await tts_task
        except asyncio.CancelledError:
            pass
        await self._out.put(_resp(turn_complete=True))

    def _build_openai_messages(self) -> list[dict]:
        messages: list[dict] = []
        sys_txt = _turns_to_text(self._sys).strip()
        if sys_txt:
            messages.append({"role": "system", "content": sys_txt})

        for item in self._history:
            if item.role == "user":
                txt = ""
                tool_resps = []
                for p in item.parts or []:
                    if getattr(p, "text", None):
                        txt += p.text
                    elif getattr(p, "function_response", None):
                        fr = p.function_response
                        resp = getattr(fr, "response", {})
                        tool_resps.append({
                            "role": "tool",
                            "tool_call_id": getattr(fr, "id", None) or f"call_{getattr(fr, 'name', '')}",
                            "name": getattr(fr, "name", ""),
                            "content": json.dumps(resp if isinstance(resp, dict) else {"result": resp}),
                        })
                if txt:
                    messages.append({"role": "user", "content": txt})
                messages.extend(tool_resps)
            elif item.role == "model":
                txt = ""
                tool_calls = []
                for p in item.parts or []:
                    if getattr(p, "text", None):
                        txt += p.text
                    elif getattr(p, "function_call", None):
                        fc = p.function_call
                        tool_calls.append({
                            "id": getattr(fc, "id", None) or f"call_{getattr(fc, 'name', '')}",
                            "type": "function",
                            "function": {
                                "name": getattr(fc, "name", ""),
                                "arguments": json.dumps(dict(getattr(fc, "args", {}) or {})),
                            },
                        })
                msg: dict[str, Any] = {"role": "assistant"}
                if txt:
                    msg["content"] = txt
                else:
                    msg["content"] = ""
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                messages.append(msg)
        return messages

    def _build_openai_tools(self) -> list[dict]:
        out = []
        for tool in self._tools or []:
            for fd in getattr(tool, "function_declarations", []) or []:
                try:
                    params = fd.parameters.model_dump(exclude_none=True, mode="json") if fd.parameters else {"type": "object", "properties": {}}
                    params = _to_openai_schema(params)
                except Exception:
                    params = {"type": "object", "properties": {}}
                out.append({
                    "type": "function",
                    "function": {
                        "name": fd.name,
                        "description": fd.description or "",
                        "parameters": params,
                    },
                })
        return out

    async def _generate_lm_studio(self, sentences: asyncio.Queue, depth: int = 0):
        base_url = f"{self._lm_studio_server.rstrip('/')}:{self._lm_studio_port}"
        url = f"{base_url}/v1/chat/completions"
        timeout = aiohttp.ClientTimeout(total=90, connect=5)

        async with aiohttp.ClientSession(timeout=timeout) as http:
            model_name = await _resolve_lm_studio_model(http, base_url, self._lm_studio_model)
            messages = self._build_openai_messages()
            payload: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "stream": True,
                "temperature": 0.7,
            }
            tools = self._build_openai_tools()
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            t0 = time.perf_counter()
            first = True
            pending = ""
            full_text = ""
            tool_calls_map: dict[int, dict[str, Any]] = {}

            try:
                async with http.post(url, json=payload) as resp:
                    if resp.status != 200:
                        err_body = await resp.text()
                        self._log(f"[LowToken/LMStudio] HTTP {resp.status}: {err_body}")
                        if resp.status == 400 and "tools" in payload:
                            self._log("[LowToken/LMStudio] Modèle sans support tools, réessai sans tools...")
                            payload.pop("tools", None)
                            payload.pop("tool_choice", None)
                            async with http.post(url, json=payload) as resp2:
                                if resp2.status != 200:
                                    raise RuntimeError(f"HTTP {resp2.status}")
                                async for line in resp2.content:
                                    line_str = line.decode("utf-8", errors="replace").strip()
                                    if not line_str.startswith("data:"):
                                        continue
                                    chunk_data = line_str[5:].strip()
                                    if chunk_data == "[DONE]":
                                        break
                                    try:
                                        item = json.loads(chunk_data)
                                    except Exception:
                                        continue
                                    delta = (item.get("choices") or [{}])[0].get("delta") or {}
                                    txt = delta.get("content")
                                    if txt:
                                        if first:
                                            self._log(f"[LowToken/LMStudio] Premier token en {time.perf_counter() - t0:.2f}s ({model_name})")
                                            first = False
                                        full_text += txt
                                        pending += txt
                                        pending = await self._flush_sentences(pending, sentences)
                        else:
                            raise RuntimeError(f"HTTP {resp.status}")
                    else:
                        async for line in resp.content:
                            line_str = line.decode("utf-8", errors="replace").strip()
                            if not line_str.startswith("data:"):
                                continue
                            chunk_data = line_str[5:].strip()
                            if chunk_data == "[DONE]":
                                break
                            try:
                                item = json.loads(chunk_data)
                            except Exception:
                                continue
                            choices = item.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}

                            txt = delta.get("content")
                            if txt:
                                if first:
                                    self._log(f"[LowToken/LMStudio] Premier token en {time.perf_counter() - t0:.2f}s ({model_name})")
                                    first = False
                                full_text += txt
                                pending += txt
                                pending = await self._flush_sentences(pending, sentences)

                            tcs = delta.get("tool_calls") or []
                            for tc in tcs:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_map:
                                    tool_calls_map[idx] = {
                                        "id": tc.get("id") or f"call_{idx}",
                                        "name": (tc.get("function") or {}).get("name") or "",
                                        "arguments": "",
                                    }
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    tool_calls_map[idx]["name"] = fn["name"]
                                if fn.get("arguments"):
                                    tool_calls_map[idx]["arguments"] += fn["arguments"]

            except aiohttp.ClientConnectorError:
                self._log(f"[LowToken/LMStudio] Connexion refusée sur {base_url}")
                await sentences.put(f"Connexion à L M Studio impossible. Vérifiez que le serveur local est actif sur le port {self._lm_studio_port}.")
                return
            except Exception as e:
                self._log(f"[LowToken/LMStudio] Erreur stream: {e}")
                await sentences.put("Erreur de communication avec le modèle local.")
                return

            if pending.strip():
                await sentences.put(pending.strip())

            parts: list[types.Part] = []
            if full_text:
                parts.append(types.Part(text=full_text))

            calls_to_execute = []
            if tool_calls_map:
                for idx in sorted(tool_calls_map.keys()):
                    cinfo = tool_calls_map[idx]
                    try:
                        args = json.loads(cinfo["arguments"]) if cinfo["arguments"] else {}
                    except Exception:
                        args = {}
                    parts.append(types.Part(function_call=types.FunctionCall(
                        id=cinfo["id"],
                        name=cinfo["name"],
                        args=args,
                    )))
                    calls_to_execute.append(SimpleNamespace(id=cinfo["id"], name=cinfo["name"], args=args))

            if parts:
                self._history.append(types.Content(role="model", parts=parts))

            if calls_to_execute and depth < 6:
                fc_obj = SimpleNamespace(function_calls=calls_to_execute)
                self._tool_future = asyncio.get_event_loop().create_future()
                await self._out.put(_resp(tool_call=fc_obj))
                responses = await self._tool_future
                self._tool_future = None
                resp_parts = []
                for fr in responses:
                    resp_parts.append(types.Part(function_response=types.FunctionResponse(
                        id=getattr(fr, "id", None),
                        name=getattr(fr, "name", None),
                        response=getattr(fr, "response", None) or {},
                    )))
                if resp_parts:
                    self._history.append(types.Content(role="user", parts=resp_parts))
                await self._generate_lm_studio(sentences, depth + 1)

    async def _generate(self, sentences: asyncio.Queue, depth: int = 0):
        cfg = types.GenerateContentConfig(
            system_instruction=self._sys,
            tools=self._tools or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        model_parts: list[types.Part] = []
        calls: list[types.FunctionCall] = []
        pending = ""
        t0 = time.perf_counter()
        first = True

        stream = await self._client.aio.models.generate_content_stream(
            model=self._text_model, contents=self._history, config=cfg)
        async for chunk in stream:
            for cand in chunk.candidates or []:
                for p in (cand.content.parts if cand.content else None) or []:
                    model_parts.append(p)
                    if p.function_call:
                        calls.append(p.function_call)
                    elif p.text and not getattr(p, "thought", False):
                        if first:
                            self._log(f"[LowToken] LLM first token {time.perf_counter() - t0:.2f}s")
                            first = False
                        pending += p.text
                        pending = await self._flush_sentences(pending, sentences)
        if pending.strip():
            await sentences.put(pending.strip())

        if model_parts:
            self._history.append(types.Content(role="model", parts=model_parts))

        if calls and depth < 6:
            fc_obj = SimpleNamespace(function_calls=[
                SimpleNamespace(id=c.id, name=c.name, args=dict(c.args or {})) for c in calls
            ])
            self._tool_future = asyncio.get_event_loop().create_future()
            await self._out.put(_resp(tool_call=fc_obj))
            responses = await self._tool_future
            self._tool_future = None
            parts = []
            for fr in responses:
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=getattr(fr, "id", None), name=getattr(fr, "name", None),
                    response=getattr(fr, "response", None) or {},
                )))
            if parts:
                self._history.append(types.Content(role="user", parts=parts))
            await self._generate(sentences, depth + 1)

    async def _flush_sentences(self, pending: str, sentences: asyncio.Queue) -> str:
        while True:
            m = _SENT_END.search(pending)
            if m:
                cut = m.end()
            elif len(pending) > _MAX_CLAUSE and ", " in pending:
                cut = pending.rfind(", ") + 2
            else:
                return pending
            s = pending[:cut].strip()
            pending = pending[cut:]
            if s:
                await sentences.put(s)

    async def _tts_worker(self, sentences: asyncio.Queue):
        while True:
            s = await sentences.get()
            if s is None:
                return
            clean = re.sub(r"[*_`#>]+", "", s).strip()
            if not clean:
                continue
            t0 = time.perf_counter()
            try:
                pcm = await self._tts.synth(clean)
            except Exception as e:
                self._log(f"[LowToken] TTS error: {e}")
                pcm = b""
            self._log(f"[LowToken] TTS {time.perf_counter() - t0:.2f}s ({len(clean)} chars)")
            await self._out.put(_resp(out_text=s + " "))
            for i in range(0, len(pcm), SLICE):
                await self._out.put(_resp(data=pcm[i:i + SLICE]))


def is_low_token_mode() -> bool:
    cfg = config_manager.load_api_keys()
    eng = str(cfg.get("tts_engine", "edgetts")).lower()
    llm_src = str(cfg.get("llm_source", "API keys"))
    if llm_src in ("LM Studio", "Local"):
        return True
    return eng in ("edgetts", "kokoro")


@asynccontextmanager
async def connect(api_key: str, live_config: Any, logger: Callable[[str], None] = print):
    cfg = config_manager.load_api_keys()
    sess = LowTokenSession(
        client=genai.Client(api_key=api_key),
        live_config=live_config,
        text_model=cfg.get("lowtoken_text_model") or DEFAULT_TEXT_MODEL,
        stt_model=cfg.get("lowtoken_stt_model") or DEFAULT_STT_MODEL,
        llm_source=cfg.get("llm_source", "API keys"),
        lm_studio_server=cfg.get("llm_host_server", "http://localhost"),
        lm_studio_port=int(cfg.get("llm_host_port", 1234)),
        lm_studio_model=cfg.get("llm_host_name", ""),
        logger=logger,
    )
    try:
        yield sess
    finally:
        await sess.close()
