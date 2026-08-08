"""
voice.py — Voice layer for Jarvis: local wake word, local STT/TTS by default, cloud optional.

ZERO-CONFIG BY DEFAULT. With the voice deps installed (see requirements-voice.txt) and NO
API keys set anywhere, voice mode works fully offline:
  - Wake word: openWakeWord, "hey_jarvis" model — runs locally, always. Raw audio never
    leaves this process, wake-word or not.
  - STT: Vosk local model (data/voice_models/vosk-model-small-en-us/) — no network call.
  - TTS: pyttsx3 via Windows SAPI5 — no network call.
Cloud STT/TTS (OpenAI Whisper API / ElevenLabs) are opt-in only, via VOICE_STT_MODE=cloud
or VOICE_TTS_MODE=cloud in .env, or VOICE_STT_FALLBACK_CLOUD=true for fallback-on-failure.
Jarvis never requires an API key to talk to you.

To enable voice:
1. Install deps:  python -m pip install -r requirements-voice.txt
2. Download the local STT model (one-time, ~40MB):
     curl -o vosk-model.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
     unzip vosk-model.zip -d data/voice_models/
     mv "data/voice_models/vosk-model-small-en-us-0.15" "data/voice_models/vosk-model-small-en-us"
   (The wake-word model downloads itself automatically on first run via openWakeWord.)
3. Set in your .env:
     VOICE_ENABLED=true
     VOICE_WAKE_WORD=hey_jarvis
     VOICE_STT_MODE=local        # local | cloud
     VOICE_TTS_MODE=local        # local | cloud
     VOICE_STT_FALLBACK_CLOUD=false   # only relevant if you also set an OPENAI_API_KEY
     VOICE_AUDIO_LOG=false       # Keep false — raw audio is never written to disk regardless

DATA RETENTION POLICY (enforced in code, not just documented here):
  - Pre-wake-word audio: read in fixed 80ms frames, fed straight into the wake-word model,
    then dropped. No buffer accumulates across frames. Never written to disk.
  - Post-wake-word command audio: held in a RAM list only while actively recording, handed
    to the STT engine (local or cloud) once, then the reference is discarded. Not stored
    after transcription completes, local or cloud, success or failure.
  - TTS output: streamed to speakers directly. Never written to a file.
  - No voice logs, no audio archives, no telemetry, in any mode.
"""

import os
import re
import time
import threading

VOICE_ENABLED = os.getenv("VOICE_ENABLED", "false").lower() == "true"
WAKE_WORD_MODEL = os.getenv("VOICE_WAKE_WORD", "hey_jarvis")
VOSK_MODEL_PATH = os.getenv("VOICE_VOSK_MODEL", os.path.join("data", "voice_models", "vosk-model-small-en-us"))

SAMPLE_RATE = 16000
CHUNK = 1280  # 80ms @ 16kHz — openWakeWord's expected frame size

# RMS thresholds for int16 PCM — mic gain varies by hardware, tune via env if needed.
SILENCE_RMS_THRESHOLD = float(os.getenv("VOICE_SILENCE_THRESHOLD", "500"))
BARGE_IN_RMS_THRESHOLD = float(os.getenv("VOICE_BARGE_IN_THRESHOLD", "800"))
WAKE_WORD_CONFIDENCE = float(os.getenv("VOICE_WAKE_CONFIDENCE", "0.5"))


def _check_core_deps() -> bool:
    """Core deps required for any voice mode at all (mic + local TTS)."""
    try:
        import pyaudio    # noqa
        import pyttsx3    # noqa
        return True
    except ImportError:
        return False


class _MicStream:
    """
    Wraps a PyAudio input stream and normalizes whatever the device natively provides
    (channel count, sample rate — mics vary: stereo-only, 44.1kHz/48kHz-only, etc.) into a
    steady stream of 16kHz mono 16-bit PCM frames of exactly `chunk_samples` samples each,
    which is what the wake-word model and STT pipeline expect. Also falls back past a
    missing/unset "system default" input device to the first real microphone found, rather
    than hard-failing — some environments (remote sessions, certain audio driver setups)
    don't register a default input device even though a working mic is present.
    """

    def __init__(self, chunk_samples: int = CHUNK, target_rate: int = SAMPLE_RATE):
        import pyaudio
        self._pa = pyaudio.PyAudio()
        self._chunk_samples = chunk_samples
        self._target_rate = target_rate
        self._resample_state = None
        self._buffer = b""

        try:
            device_index, channels, native_rate = self._pick_device()
            native_chunk = max(64, int(chunk_samples * native_rate / target_rate))
            open_kwargs = dict(
                format=pyaudio.paInt16, channels=channels, rate=int(native_rate),
                input=True, frames_per_buffer=native_chunk,
            )
            if device_index is not None:
                open_kwargs["input_device_index"] = device_index
            self._stream = self._pa.open(**open_kwargs)
        except Exception:
            self._pa.terminate()
            raise

        self._native_channels = channels
        self._native_rate = native_rate
        self._native_chunk = native_chunk

    def _pick_device(self):
        """System default first; else the first real mic (skipping loopback-style devices
        like "Stereo Mix" unless nothing else is available)."""
        try:
            info = self._pa.get_default_input_device_info()
            return info['index'], min(2, max(1, int(info['maxInputChannels']))), info['defaultSampleRate']
        except Exception:
            pass

        candidates = []
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info.get('maxInputChannels', 0) > 0:
                candidates.append((i, info))

        for i, info in candidates:
            if 'stereo mix' not in info.get('name', '').lower():
                return i, min(2, max(1, int(info['maxInputChannels']))), info['defaultSampleRate']
        if candidates:
            i, info = candidates[0]
            return i, min(2, max(1, int(info['maxInputChannels']))), info['defaultSampleRate']

        raise OSError("No input (microphone) device found on this system.")

    def read_chunk(self) -> bytes:
        """Return exactly `chunk_samples` samples of 16kHz mono 16-bit PCM."""
        import audioop
        while len(self._buffer) < self._chunk_samples * 2:
            raw = self._stream.read(self._native_chunk, exception_on_overflow=False)
            if self._native_channels == 2:
                raw = audioop.tomono(raw, 2, 0.5, 0.5)
            if int(self._native_rate) != self._target_rate:
                raw, self._resample_state = audioop.ratecv(
                    raw, 2, 1, int(self._native_rate), self._target_rate, self._resample_state
                )
            self._buffer += raw
        out = self._buffer[:self._chunk_samples * 2]
        self._buffer = self._buffer[self._chunk_samples * 2:]
        return out

    def close(self):
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass


class VoiceLayer:
    """
    Thin wrapper around wake-word detection, STT, and TTS.
    All public methods are safe to call even when voice is disabled or deps are missing —
    they return None/False gracefully instead of crashing. Text input/output always works
    regardless of voice state.
    """

    def __init__(self):
        self.enabled = VOICE_ENABLED
        self._deps_ok = _check_core_deps() if self.enabled else False
        self._tts_available = False
        self._active_engine = None  # the live per-call TTS engine, while speaking
        self._stt_mode = os.getenv("VOICE_STT_MODE", "local").lower()
        self._tts_mode = os.getenv("VOICE_TTS_MODE", "local").lower()
        self._wake_model = None
        self._vosk_model = None

        if self.enabled and not self._deps_ok:
            print(
                "[Voice] VOICE_ENABLED=true but core dependencies are missing.\n"
                "Run: python -m pip install -r requirements-voice.txt\n"
                "Continuing in text-only mode."
            )
            self.enabled = False

        if self.enabled:
            self._init_tts()
            self._init_local_stt()
            self._init_wake_word()

    # -----------------------------------------------------------------
    # Init
    # -----------------------------------------------------------------

    def _init_tts(self):
        if self._tts_mode == "local":
            try:
                # Just probe that a local engine can be created (SAPI5 available on this
                # machine) — the engine actually used to speak is created fresh per call,
                # on the thread that speaks. SAPI5's COM objects are apartment-threaded;
                # reusing one engine instance across threads (init thread vs. speak thread)
                # hangs runAndWait() indefinitely rather than erroring, which is exactly
                # what barge-in's background-thread playback would hit otherwise.
                import pyttsx3
                probe = pyttsx3.init()
                probe.stop()
                del probe
                self._tts_available = True
            except Exception as e:
                print(f"[Voice] Local TTS init failed: {e}")
                self.enabled = False
        # cloud TTS needs no local engine — _speak_cloud() handles it, with a local fallback
        # path initialized lazily if it ever needs one.

    def _init_local_stt(self):
        if self._stt_mode not in ("local", "auto"):
            return
        try:
            import vosk
            if not os.path.isdir(VOSK_MODEL_PATH):
                print(
                    f"[Voice] Local STT model not found at '{VOSK_MODEL_PATH}'.\n"
                    "Download: https://alphacephei.com/vosk/models "
                    f"(vosk-model-small-en-us-0.15), unzip into {VOSK_MODEL_PATH}.\n"
                    "Voice input will be unavailable until this is present "
                    "(no cloud STT will run in its place unless you explicitly set "
                    "VOICE_STT_MODE=cloud or VOICE_STT_FALLBACK_CLOUD=true)."
                )
                return
            vosk.SetLogLevel(-1)  # silence Vosk's verbose stderr model-loading logs
            self._vosk_model = vosk.Model(VOSK_MODEL_PATH)
        except ImportError:
            pass  # vosk not installed — local STT unavailable, handled at call sites
        except Exception as e:
            print(f"[Voice] Local STT init failed: {e}")

    def _init_wake_word(self):
        try:
            import openwakeword
            self._wake_model = openwakeword.Model(
                wakeword_models=[WAKE_WORD_MODEL], inference_framework="onnx"
            )
        except ImportError:
            pass  # openwakeword not installed — falls back to push-to-talk, see listen_once()
        except Exception as e:
            print(f"[Voice] Wake-word init failed: {e}. Falling back to push-to-talk (no passive listening).")
            self._wake_model = None

    def has_wake_word(self) -> bool:
        """True if passive wake-word listening is available (model loaded, deps present)."""
        return self.enabled and self._wake_model is not None

    def is_active(self) -> bool:
        return self.enabled

    # -----------------------------------------------------------------
    # Indicators — audio cue + console banner, always both when possible
    # -----------------------------------------------------------------

    def _cue(self, kind: str):
        """Distinct audio cue per state. Silently no-ops off-Windows or with no sound device —
        the console banner printed alongside every cue is the indicator of record in that case."""
        try:
            import winsound
            tones = {"wake": (880, 120), "stop": (440, 120), "interrupt": (220, 90)}
            freq, dur = tones.get(kind, (440, 100))
            winsound.Beep(freq, dur)
        except Exception:
            pass

    # -----------------------------------------------------------------
    # Wake word — passive listening loop
    # -----------------------------------------------------------------

    def wait_for_wake_word(self, timeout: float = None) -> bool:
        """
        Block until the wake word is heard (or `timeout` seconds elapse, if given — used by
        tests; interactive callers leave it None to wait indefinitely). Streams the mic in
        fixed 80ms frames straight into the wake-word model; each frame is discarded
        immediately after scoring — nothing accumulates, nothing is written to disk.
        Returns False if wake-word support isn't available (caller should fall back to
        push-to-talk via listen_once()) or if `timeout` elapsed without a trigger.
        """
        if not self.has_wake_word():
            return False

        import numpy as np

        try:
            mic = _MicStream()
        except Exception as e:
            print(f"[Voice] Microphone unavailable: {e}")
            return False

        print("\U0001F399️  Waiting for wake word... (say \"Jarvis\")")
        start = time.monotonic()
        try:
            self._wake_model.reset()
            while timeout is None or (time.monotonic() - start) < timeout:
                try:
                    frame = mic.read_chunk()
                except Exception as e:
                    print(f"[Voice] Wake-word listening stopped (mic error: {e}).")
                    return False
                audio = np.frombuffer(frame, dtype=np.int16)
                prediction = self._wake_model.predict(audio)
                score = prediction.get(WAKE_WORD_MODEL, 0.0)
                if score > WAKE_WORD_CONFIDENCE:
                    self._cue("wake")
                    return True
            return False
        finally:
            mic.close()

    # -----------------------------------------------------------------
    # Command capture (after wake word, or push-to-talk)
    # -----------------------------------------------------------------

    def listen_command(self, max_seconds: float = 12.0, silence_seconds: float = 1.2) -> str | None:
        """
        Record a spoken command until silence (or max_seconds), then transcribe it.
        Audio lives in a RAM list only for the duration of this call — discarded right
        after being handed to the STT engine, win or lose.
        """
        if not self.enabled or not self._deps_ok:
            return None

        import numpy as np

        try:
            mic = _MicStream()
        except Exception as e:
            print(f"[Voice] Microphone unavailable: {e}")
            return None

        frames = []
        heard_speech = False
        silence_chunks = 0
        max_silence_chunks = max(1, int(silence_seconds * SAMPLE_RATE / CHUNK))
        max_chunks = max(1, int(max_seconds * SAMPLE_RATE / CHUNK))

        print("\U0001F534 LISTENING...")
        mic_error = False
        try:
            for _ in range(max_chunks):
                try:
                    data = mic.read_chunk()
                except Exception as e:
                    print(f"[Voice] Listening stopped (mic error: {e}).")
                    mic_error = True
                    break
                frames.append(data)
                audio = np.frombuffer(data, dtype=np.int16)
                rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if len(audio) else 0.0
                if rms > SILENCE_RMS_THRESHOLD:
                    heard_speech = True
                    silence_chunks = 0
                else:
                    silence_chunks += 1
                if heard_speech and silence_chunks >= max_silence_chunks:
                    break
        finally:
            mic.close()
            self._cue("stop")
            print("⏹️  Processing...")

        if mic_error or not heard_speech:
            return None

        raw_audio = b"".join(frames)
        frames = None  # drop reference — nothing after this point holds the audio in memory

        return self._transcribe(raw_audio)

    def listen_once(self, timeout: float = 5.0, max_seconds: float = 12.0) -> str | None:
        """
        Legacy push-to-talk entry point (no wake word): opens the mic immediately and waits
        up to `timeout` seconds for speech to start, then records until silence. Used when
        wake-word support isn't available, or by callers that want an explicit "listen now".
        """
        if not self.enabled or not self._deps_ok:
            return None

        import numpy as np

        try:
            mic = _MicStream()
        except Exception as e:
            print(f"[Voice] Microphone unavailable: {e}")
            return None

        frames = []
        heard_speech = False
        silence_chunks = 0
        max_silence_chunks = max(1, int(1.2 * SAMPLE_RATE / CHUNK))
        wait_chunks = max(1, int(timeout * SAMPLE_RATE / CHUNK))
        max_chunks = max(1, int(max_seconds * SAMPLE_RATE / CHUNK))

        print("\U0001F534 LISTENING...")
        mic_error = False
        try:
            waited = 0
            total = 0
            while total < max_chunks:
                try:
                    data = mic.read_chunk()
                except Exception as e:
                    print(f"[Voice] Listening stopped (mic error: {e}).")
                    mic_error = True
                    break
                total += 1
                audio = np.frombuffer(data, dtype=np.int16)
                rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if len(audio) else 0.0

                if not heard_speech:
                    if rms > SILENCE_RMS_THRESHOLD:
                        heard_speech = True
                        frames.append(data)
                    else:
                        waited += 1
                        if waited >= wait_chunks:
                            break  # timed out waiting for speech to start
                    continue

                frames.append(data)
                if rms > SILENCE_RMS_THRESHOLD:
                    silence_chunks = 0
                else:
                    silence_chunks += 1
                    if silence_chunks >= max_silence_chunks:
                        break
        finally:
            mic.close()
            self._cue("stop")
            print("⏹️  Processing...")

        if mic_error or not heard_speech:
            return None

        raw_audio = b"".join(frames)
        frames = None
        return self._transcribe(raw_audio)

    # Back-compat alias — older call sites used voice.listen()
    listen = listen_once

    # -----------------------------------------------------------------
    # STT
    # -----------------------------------------------------------------

    def _transcribe(self, raw_audio: bytes) -> str | None:
        if self._stt_mode == "cloud":
            return self._stt_cloud_bytes(raw_audio) or self._stt_local_bytes(raw_audio)

        text = self._stt_local_bytes(raw_audio)
        if text:
            return text

        # Local STT unavailable or produced nothing — only touch the network if the user
        # explicitly opted in, and only if they've actually set an API key.
        if os.getenv("VOICE_STT_FALLBACK_CLOUD", "false").lower() == "true" and os.getenv("OPENAI_API_KEY"):
            return self._stt_cloud_bytes(raw_audio)
        return None

    def _stt_local_bytes(self, raw_audio: bytes) -> str | None:
        if not self._vosk_model:
            return None
        try:
            import vosk
            import json
            rec = vosk.KaldiRecognizer(self._vosk_model, SAMPLE_RATE)
            rec.AcceptWaveform(raw_audio)
            result = json.loads(rec.FinalResult())
            text = result.get("text", "").strip()
            return text or None
        except Exception as e:
            print(f"[Voice] Local STT error: {e}")
            return None

    def _stt_cloud_bytes(self, raw_audio: bytes) -> str | None:
        """Whisper API STT. Only ever called when cloud mode/fallback is explicitly opted into
        AND an OPENAI_API_KEY is set — never a silent default. The audio clip is sent to
        OpenAI for this one request and not retained by Jarvis afterward."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            import io
            import wave
            from openai import OpenAI
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(raw_audio)
            buf.seek(0)
            buf.name = "command.wav"
            client = OpenAI(api_key=api_key)
            resp = client.audio.transcriptions.create(model="whisper-1", file=buf)
            return (resp.text or "").strip() or None
        except Exception as e:
            print(f"[Voice] Cloud STT error: {e}")
            return None

    # -----------------------------------------------------------------
    # TTS — with barge-in support
    # -----------------------------------------------------------------

    def speak(self, text: str):
        """Speak text aloud. No-op if voice is disabled. Interruptible mid-sentence if the
        user starts talking (barge-in) — see _monitor_for_barge_in."""
        if not self.enabled:
            return

        clean_text = re.sub(r'\[REMEMBER:.*?\]', '', text).strip()
        if not clean_text:
            return

        if self._tts_mode == "cloud":
            self._speak_cloud(clean_text)
        else:
            self._speak_local_interruptible(clean_text)

    def _speak_local_interruptible(self, text: str):
        if not self._tts_available:
            return

        self._active_engine = None
        engine_ready = threading.Event()

        def _run():
            try:
                import pyttsx3
                # Fresh engine, created and used entirely within this thread — see the
                # comment in _init_tts() for why a shared/reused engine hangs here.
                engine = pyttsx3.init()
                engine.setProperty('rate', 165)
                self._active_engine = engine
                engine_ready.set()
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                engine_ready.set()
                print(f"[Voice] TTS error: {e}")

        speak_thread = threading.Thread(target=_run, daemon=True)
        speak_thread.start()
        engine_ready.wait(timeout=2)

        if self._deps_ok:
            self._monitor_for_barge_in(speak_thread)
        else:
            speak_thread.join()

    def _monitor_for_barge_in(self, speak_thread: threading.Thread):
        """While Jarvis is talking, watch the mic; if the user starts speaking, cut playback."""
        import numpy as np
        try:
            mic = _MicStream()
        except Exception:
            speak_thread.join()
            return

        try:
            while speak_thread.is_alive():
                try:
                    data = mic.read_chunk()
                except Exception as e:
                    # A dropped/busy mic mid-monitor shouldn't kill the response that's
                    # already playing — lose barge-in for this turn, not the whole reply.
                    print(f"[Voice] Barge-in monitor stopped (mic error: {e}). Letting response finish normally.")
                    break
                audio = np.frombuffer(data, dtype=np.int16)
                rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if len(audio) else 0.0
                if rms > BARGE_IN_RMS_THRESHOLD:
                    try:
                        if self._active_engine:
                            self._active_engine.stop()
                    except Exception:
                        pass
                    self._cue("interrupt")
                    print("⚡ Interrupted.")
                    break
        finally:
            mic.close()
        speak_thread.join(timeout=2)

    def _speak_cloud(self, text: str):
        """ElevenLabs TTS — requires ELEVENLABS_API_KEY, only used if explicitly configured."""
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            print("[Voice] ELEVENLABS_API_KEY not set. Falling back to local TTS.")
            self._speak_local_interruptible(text)
            return
        try:
            import requests
            response = requests.post(
                "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM/stream",
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_monolingual_v1"},
                timeout=15
            )
            response.raise_for_status()
            # Streaming playback wiring (e.g. via simpleaudio/pyaudio) is a follow-up —
            # falling back to local TTS keeps voice mode working without an extra dependency.
            self._speak_local_interruptible(text)
        except Exception as e:
            print(f"[Voice] Cloud TTS error: {e}")
            self._speak_local_interruptible(text)


# Singleton — import this in main.py
voice = VoiceLayer()
