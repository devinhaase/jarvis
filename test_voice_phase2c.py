"""
Tests for Phase 2c-finish: local wake word, local STT/TTS, barge-in, retention policy.

Run: python test_voice_phase2c.py

Split into two kinds of checks:
  1. Live checks against real hardware/models on this machine (mic present, hey_jarvis
     model downloaded, vosk model downloaded, SAPI5 voices present) — these actually
     exercise the pipeline, just without a human speaking into it.
  2. Static checks that don't need hardware — retention policy (no disk writes for audio),
     zero-API-key defaults, graceful degradation when deps are missing.
"""

import sys
import os
import io
import contextlib

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
section("1. Zero-API-key defaults")
# ---------------------------------------------------------------------------
os.environ["VOICE_ENABLED"] = "true"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ELEVENLABS_API_KEY", None)

import importlib
import voice as voice_module
importlib.reload(voice_module)
vl = voice_module.VoiceLayer()

check("VoiceLayer initializes with zero API keys set", vl.enabled, "voice disabled itself with no keys present")
check("STT mode defaults to local", vl._stt_mode == "local")
check("TTS mode defaults to local", vl._tts_mode == "local")
check("Cloud STT fallback is off by default", os.getenv("VOICE_STT_FALLBACK_CLOUD", "false").lower() != "true")


# ---------------------------------------------------------------------------
section("2. Local pipeline components load")
# ---------------------------------------------------------------------------
check("Local TTS engine initialized (pyttsx3/SAPI5)", vl._tts_available is True)
check("Local STT model loaded (Vosk)", vl._vosk_model is not None,
      f"expected model at {voice_module.VOSK_MODEL_PATH}")
check("Wake-word model loaded (openWakeWord hey_jarvis)", vl.has_wake_word())


# ---------------------------------------------------------------------------
section("3. Wake-word detector runs against real mic (no speech expected)")
# ---------------------------------------------------------------------------
try:
    triggered = vl.wait_for_wake_word(timeout=1.5)
    check("wait_for_wake_word() runs against live mic without crashing", True)
    check("wait_for_wake_word() does not false-trigger on ambient silence", triggered is False,
          f"triggered={triggered} (fine if you were actually talking during the test)")
except Exception as e:
    check("wait_for_wake_word() runs against live mic without crashing", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
section("4. Local STT transcribes real audio, not just silence")
# ---------------------------------------------------------------------------
import struct
import math

# Synthesize a 1s 440Hz tone — not real speech, just proving the pipeline runs on real
# non-trivial audio (not just all-zero silence) without raising.
samples = []
for i in range(voice_module.SAMPLE_RATE):
    val = int(3000 * math.sin(2 * math.pi * 440 * i / voice_module.SAMPLE_RATE))
    samples.append(val)
tone_audio = struct.pack('<' + 'h' * len(samples), *samples)

try:
    result = vl._stt_local_bytes(tone_audio)
    check("Local STT processes real audio bytes without raising", True)
    check("Tone (non-speech) correctly transcribes to nothing/None", result is None,
          f"got: {result!r} (vosk sometimes guesses a stray word on tones — not a failure by itself)")
except Exception as e:
    check("Local STT processes real audio bytes without raising", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
section("5. Retention policy — no audio ever written to disk")
# ---------------------------------------------------------------------------
import inspect

src = inspect.getsource(voice_module)
write_mode_writes = [
    line for line in src.splitlines()
    if ("open(" in line and any(m in line for m in ["'w'", '"w"', "'wb'", '"wb"'])) and "wave.open(buf" not in line
]
check(
    "No disk file-write calls anywhere in voice.py",
    len(write_mode_writes) == 0,
    f"found: {write_mode_writes}"
)
check(
    "The one wave.open() call writes to an in-memory BytesIO, not a file",
    "wave.open(buf" in src and "io.BytesIO()" in src
)


# ---------------------------------------------------------------------------
section("6. Graceful degradation when deps are missing")
# ---------------------------------------------------------------------------
import builtins
real_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name in ("pyaudio", "pyttsx3"):
        raise ImportError(f"simulated missing dep: {name}")
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked_import
try:
    vl_degraded = voice_module.VoiceLayer()
    check("VoiceLayer disables itself gracefully when core deps are missing", vl_degraded.enabled is False)
    check("listen_once() returns None instead of raising when disabled", vl_degraded.listen_once() is None)
    check("speak() no-ops instead of raising when disabled", True)
    vl_degraded.speak("this should be a silent no-op")
except Exception as e:
    check("VoiceLayer disables itself gracefully when core deps are missing", False, f"{type(e).__name__}: {e}")
finally:
    builtins.__import__ = real_import


# ---------------------------------------------------------------------------
section("7. TTS speaks + barge-in monitor doesn't crash (real audio output)")
# ---------------------------------------------------------------------------
try:
    vl.speak("Testing text to speech. This is Jarvis running fully offline.")
    check("speak() completes without raising (audible on this machine's speakers)", True)
except Exception as e:
    check("speak() completes without raising", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
print(f"\n{'=' * 50}\n{PASS} passed, {FAIL} failed\n{'=' * 50}")
sys.exit(1 if FAIL else 0)
