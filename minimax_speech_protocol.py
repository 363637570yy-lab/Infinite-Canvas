"""MiniMax Token Plan speech protocol (T2A + get_voice).

Plan A: one short sample clip locks a character timbre. Video models (H3)
then speak new lines against that sample. This module does not call Hailuo /
H3 video APIs and does not synthesize per-shot dialogue.

T2A `model` values come from the official T2A OpenAPI enum. They are not
guessed from /v1/models names. /v1/models entries are classified only from
upstream capability fields; missing capabilities stay unknown.
"""

import base64
import binascii
import urllib.parse


MINIMAX_SPEECH_PROTOCOL = "minimax-speech"
MINIMAX_GET_VOICE_PATH = "/v1/get_voice"
MINIMAX_T2A_PATH = "/v1/t2a_v2"

# Official T2A HTTP enum (platform.minimaxi.com / platform.minimax.io).
# These are T2A request parameters, not a /v1/models catalog.
MINIMAX_T2A_MODELS = (
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
)
MINIMAX_DEFAULT_T2A_MODEL = "speech-2.8-hd"
MINIMAX_DEFAULT_SAMPLE_TEXT = "这是用于视频角色音色参考的样音，请保持声线稳定、吐字清晰。"
MINIMAX_SAMPLE_TEXT_MAX = 200
MINIMAX_T2A_TEXT_MAX = 10000

# H3 reference audio is documented as 2–15 seconds; warn above this, still save.
MINIMAX_SAMPLE_WARN_MS = 15000


def minimax_api_root(base_url=""):
    value = str(base_url or "").strip().rstrip("/")
    if value.endswith("/v1") or value.endswith("/v2"):
        value = value.rsplit("/", 1)[0]
    return value


def minimax_get_voice_url(base_url=""):
    return f"{minimax_api_root(base_url)}{MINIMAX_GET_VOICE_PATH}"


def minimax_t2a_url(base_url=""):
    return f"{minimax_api_root(base_url)}{MINIMAX_T2A_PATH}"


def minimax_auth_headers(api_key, json_body=True, group_id=""):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {str(api_key or '').strip()}",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    group = str(group_id or "").strip()
    if group:
        headers["X-Group-Id"] = group
    return headers


def minimax_url_with_group(url, group_id=""):
    group = str(group_id or "").strip()
    if not group:
        return url
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["GroupId"] = group
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _capability_values(item):
    values = []
    if not isinstance(item, dict):
        return values
    for key in ("type", "model_type", "modelType", "task", "category", "kind"):
        value = item.get(key)
        if isinstance(value, (str, int, float)):
            values.append(str(value).strip().lower())
        elif isinstance(value, list):
            values.extend(str(part).strip().lower() for part in value)
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        values.extend(str(key).strip().lower() for key, enabled in capabilities.items() if enabled)
    elif isinstance(capabilities, list):
        values.extend(str(part).strip().lower() for part in capabilities)
    types = item.get("supported_endpoint_types")
    if isinstance(types, list):
        values.extend(str(part).strip().lower() for part in types)
    return values


def classify_minimax_speech_model_entry(item, model_id=""):
    """Classify /v1/models rows from capability fields only. Never guess from the id."""
    del model_id
    values = _capability_values(item)
    if any("video" in value or value in {"t2v", "i2v", "s2v"} for value in values):
        return "video"
    if any("image" in value for value in values):
        return "image"
    if any(
        "audio" in value or "speech" in value or "t2a" in value or value in {"tts", "voice"}
        for value in values
    ):
        return "audio"
    if any("chat" in value or value in {"llm", "text", "completion", "completions"} for value in values):
        return "chat"
    return "unknown"


def build_get_voice_request(voice_type="system"):
    wanted = str(voice_type or "system").strip().lower() or "system"
    if wanted not in {"system", "voice_cloning", "voice_generation", "all"}:
        wanted = "system"
    return {"voice_type": wanted}


def parse_voice_entries(raw, source_key):
    entries = []
    if not isinstance(raw, dict):
        return entries
    rows = raw.get(source_key)
    if not isinstance(rows, list):
        return entries
    for item in rows:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("voice_id") or "").strip()
        if not voice_id:
            continue
        name = str(item.get("voice_name") or voice_id).strip() or voice_id
        description = item.get("description")
        if isinstance(description, list):
            description = " ".join(str(part).strip() for part in description if str(part).strip())
        else:
            description = str(description or "").strip()
        entries.append({
            "voice_id": voice_id,
            "voice_name": name,
            "description": description,
            "source": source_key,
        })
    return entries


def parse_voice_list(raw):
    voices = []
    seen = set()
    for key in ("system_voice", "voice_cloning", "voice_generation"):
        for item in parse_voice_entries(raw if isinstance(raw, dict) else {}, key):
            if item["voice_id"] in seen:
                continue
            seen.add(item["voice_id"])
            voices.append(item)
    return voices


def t2a_model(requested=""):
    value = str(requested or "").strip()
    if value in MINIMAX_T2A_MODELS:
        return value
    return MINIMAX_DEFAULT_T2A_MODEL


def t2a_text(value, default=MINIMAX_DEFAULT_SAMPLE_TEXT, limit=MINIMAX_SAMPLE_TEXT_MAX):
    text = str(value or "").strip() or str(default or "").strip()
    cap = int(limit or MINIMAX_SAMPLE_TEXT_MAX)
    if cap > 0:
        text = text[:cap]
    return text


def build_t2a_request(text, voice_id, model="", sample=True):
    """Non-streaming T2A body. Sample clips stay short; per-shot dialogue is out of scope."""
    voice = str(voice_id or "").strip()
    if not voice:
        raise ValueError("MiniMax T2A 需要 voice_id")
    limit = MINIMAX_SAMPLE_TEXT_MAX if sample else MINIMAX_T2A_TEXT_MAX
    body = {
        "model": t2a_model(model),
        "text": t2a_text(text, limit=limit),
        "stream": False,
        "voice_setting": {
            "voice_id": voice,
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    if not body["text"]:
        raise ValueError("MiniMax T2A 需要合成文本")
    return body


def base_resp_ok(raw):
    if not isinstance(raw, dict):
        return False
    resp = raw.get("base_resp")
    if not isinstance(resp, dict):
        return True
    try:
        return int(resp.get("status_code") or 0) == 0
    except (TypeError, ValueError):
        return False


def error_text(raw, fallback=""):
    if isinstance(raw, dict):
        resp = raw.get("base_resp")
        if isinstance(resp, dict):
            message = str(resp.get("status_msg") or "").strip()
            code = resp.get("status_code")
            if message and code not in (None, 0, "0"):
                return message
            if message and message.lower() not in {"success", "ok"}:
                return message
        for key in ("detail", "error", "message"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = str(value.get("message") or value.get("msg") or "").strip()
                if nested:
                    return nested
    text = str(fallback or "").strip()
    return text[:500] if text else "MiniMax 语音接口失败"


def _looks_like_hex(value):
    text = str(value or "").strip()
    if len(text) < 8 or len(text) % 2:
        return False
    try:
        int(text[:16], 16)
    except ValueError:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text[:64])


def decode_t2a_audio_bytes(raw):
    """Official T2A returns hex in data.audio. Some proxies wrap base64 as audio_file."""
    if not isinstance(raw, dict):
        raise ValueError("MiniMax T2A 返回了无法识别的响应")
    if not base_resp_ok(raw):
        raise ValueError(error_text(raw, "MiniMax T2A 合成失败"))
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    hex_audio = str(data.get("audio") or raw.get("audio") or "").strip()
    if hex_audio and _looks_like_hex(hex_audio):
        try:
            return binascii.unhexlify(hex_audio)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("MiniMax T2A 音频 hex 无法解码") from exc
    b64_audio = str(
        raw.get("audio_file")
        or data.get("audio_file")
        or (hex_audio if hex_audio and not _looks_like_hex(hex_audio) else "")
    ).strip()
    if b64_audio:
        try:
            return base64.b64decode(b64_audio)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("MiniMax T2A 音频 base64 无法解码") from exc
    raise ValueError("MiniMax T2A 没有返回音频数据")


def extra_audio_length_ms(raw):
    if not isinstance(raw, dict):
        return None
    info = raw.get("extra_info")
    if not isinstance(info, dict):
        return None
    value = info.get("audio_length")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number > 0 else None
