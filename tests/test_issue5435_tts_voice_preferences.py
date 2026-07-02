"""Regression checks for #5435 TTS and voice preference persistence."""

import json
import pathlib
import urllib.error
import urllib.request

from tests._pytest_port import BASE

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PY = (ROOT / "api" / "config.py").read_text(encoding="utf-8")
BOOT_JS = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")

SPEECH_DEFAULTS = {
    "tts_enabled": False,
    "tts_auto_read": False,
    "tts_engine": "browser",
    "tts_voice": "",
    "tts_rate": 1.0,
    "tts_pitch": 1.0,
    "voice_mode_button": False,
    "voice_continuous": False,
    "voice_silence_ms": 1800,
    "raw_audio_mode": False,
}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.loads(response.read()), response.status


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def _reset_speech_settings(extra=None):
    payload = dict(SPEECH_DEFAULTS)
    if extra:
        payload.update(extra)
    post("/api/settings", payload)


def test_settings_api_exposes_tts_voice_and_raw_audio_defaults():
    data, status = get("/api/settings")

    assert status == 200
    for key, value in SPEECH_DEFAULTS.items():
        assert data[key] == value


def test_settings_api_round_trips_speech_preferences():
    payload = {
        "tts_enabled": True,
        "tts_auto_read": True,
        "tts_engine": "voicevox_local",
        "tts_voice": "en-US-AriaNeural",
        "tts_rate": "1.4",
        "tts_pitch": "0",
        "voice_mode_button": True,
        "voice_continuous": True,
        "voice_silence_ms": "2400",
        "raw_audio_mode": True,
    }
    try:
        saved, status = post("/api/settings", payload)
        reloaded, reload_status = get("/api/settings")

        assert status == 200
        assert reload_status == 200
        assert saved["tts_enabled"] is True
        assert saved["tts_auto_read"] is True
        assert saved["tts_engine"] == "voicevox_local"
        assert saved["tts_voice"] == "en-US-AriaNeural"
        assert saved["tts_rate"] == 1.4
        assert saved["tts_pitch"] == 0.0
        assert saved["voice_mode_button"] is True
        assert saved["voice_continuous"] is True
        assert saved["voice_silence_ms"] == 2400
        assert saved["raw_audio_mode"] is True
        for key in payload:
            expected = saved[key]
            assert reloaded[key] == expected
    finally:
        _reset_speech_settings()


def test_invalid_speech_settings_preserve_previous_values_and_unrelated_settings():
    data, status = get("/api/settings")
    original_show_tps = bool(data.get("show_tps"))
    valid = {
        "tts_engine": "edge",
        "tts_voice": "zh-CN-XiaoxiaoNeural",
        "tts_rate": 1.2,
        "tts_pitch": 1.1,
        "voice_silence_ms": 2200,
    }
    try:
        saved, status = post("/api/settings", valid)
        assert status == 200
        assert saved["tts_engine"] == "edge"

        invalid, status = post(
            "/api/settings",
            {
                "tts_engine": "",
                "tts_voice": "x" * 201,
                "tts_rate": "nan",
                "tts_pitch": 3,
                "voice_silence_ms": 199,
                "show_tps": not original_show_tps,
            },
        )

        assert status == 200
        for key, value in valid.items():
            assert invalid[key] == value
        assert invalid["show_tps"] is (not original_show_tps)
    finally:
        _reset_speech_settings({"show_tps": original_show_tps})


def test_backend_schema_contains_typed_speech_validation():
    for key in SPEECH_DEFAULTS:
        assert f'"{key}"' in CONFIG_PY
    assert '"voice_silence_ms": (200, 60000)' in CONFIG_PY
    assert '"tts_rate": (0.5, 2.0)' in CONFIG_PY
    assert '"tts_pitch": (0.0, 2.0)' in CONFIG_PY
    assert "_SETTINGS_TTS_ENGINE_RE" in CONFIG_PY
    assert 'k == "tts_voice"' in CONFIG_PY


def test_boot_mirrors_server_settings_before_tts_apply_and_preserves_failure_fallback():
    mirror_idx = BOOT_JS.index("function _mirrorSpeechSettingsFromServer")
    success_call_idx = BOOT_JS.index("_mirrorSpeechSettingsFromServer(s);", mirror_idx)
    apply_idx = BOOT_JS.index("_applyTtsEnabled(localStorage.getItem('hermes-tts-enabled')==='true')", success_call_idx)
    catch_idx = BOOT_JS.index("}catch(e){", success_call_idx)
    failure_apply_idx = BOOT_JS.index("_applyTtsEnabled(localStorage.getItem('hermes-tts-enabled')==='true')", catch_idx)

    assert success_call_idx < apply_idx
    assert catch_idx < failure_apply_idx
    assert "const defaults={" in BOOT_JS
    assert "cached!==null&&boolValue(server)===boolValue(defaults[settingKey])" in BOOT_JS
    assert "String(server)===String(defaults[settingKey])" in BOOT_JS
    for storage_key in [
        "hermes-tts-enabled",
        "hermes-tts-auto-read",
        "hermes-tts-engine",
        "hermes-tts-voice",
        "hermes-tts-rate",
        "hermes-tts-pitch",
        "hermes-voice-mode-button",
        "hermes-voice-continuous",
        "hermes-voice-silence-ms",
        "hermes-raw-audio-mode",
    ]:
        assert storage_key in BOOT_JS
    assert "window._applyRawAudioModePreference" in BOOT_JS


def test_settings_panel_persists_speech_fields_and_keeps_immediate_cache_writes():
    payload_idx = PANELS_JS.index("function _preferencesPayloadFromUi")
    payload_end = PANELS_JS.index("function _setPreferencesAutosaveStatus", payload_idx)
    payload_block = PANELS_JS[payload_idx:payload_end]
    panel_idx = PANELS_JS.index("TTS settings use /api/settings as the durable source")
    panel_end = PANELS_JS.index("const notifCb=$('settingsNotificationsEnabled')", panel_idx)
    panel_block = PANELS_JS[panel_idx:panel_end]

    for field in SPEECH_DEFAULTS:
        assert f"payload.{field}=" in payload_block
    for storage_key in [
        "hermes-tts-enabled",
        "hermes-tts-auto-read",
        "hermes-tts-engine",
        "hermes-tts-voice",
        "hermes-tts-rate",
        "hermes-tts-pitch",
        "hermes-voice-mode-button",
        "hermes-voice-continuous",
        "hermes-voice-silence-ms",
        "hermes-raw-audio-mode",
    ]:
        assert storage_key in panel_block or storage_key in payload_block
    assert "_speechSetting('tts_engine','hermes-tts-engine','browser')" in panel_block
    assert "savedRate||'1'" not in panel_block
    assert "savedPitch||'1'" not in panel_block
    assert "ttsRateSlider.value=(savedRate===null||savedRate===undefined)?'1':String(savedRate)" in panel_block
    assert "ttsPitchSlider.value=(savedPitch===null||savedPitch===undefined)?'1':String(savedPitch)" in panel_block
    assert "serverBool===fallbackBool&&storedBool!==fallbackBool" in PANELS_JS
    assert "String(server)===String(fallback)&&String(stored)!==String(fallback)" in PANELS_JS
    assert "_schedulePreferencesAutosave()" in panel_block
    assert "_applyVoiceModePref" in panel_block
    assert "_populateTtsVoices" in panel_block
