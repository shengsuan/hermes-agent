#!/usr/bin/env python3

import json
import time
import logging
import asyncio
import os
import requests
from typing import Any, Dict, Optional, List

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

SHENGSUANYUN_BASE_URL = "https://api.shengsuanyun.com/modelrouter"
ROUTER_BASE_URL = "https://router.shengsuanyun.com/api/v1"
CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000

def get_cache_path(name: str):
    from pathlib import Path
    state_dir = Path.home() / ".cache" / "shengsuanyun"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{name}.json"

def load_cache(name: str):
    try:
        path = get_cache_path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() * 1000 - data["timestamp"] > CACHE_TTL_MS:
            return None
        return data["data"]
    except Exception:
        return None

def save_cache(name: str, data):
    try:
        path = get_cache_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": int(time.time() * 1000),
            "data": data,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

def try_res(res: requests.Response):
    if res.ok:
        return
    try:
        error_data = res.json()
        error_detail = (
            error_data.get("message")
            or error_data.get("error")
            or json.dumps(error_data, ensure_ascii=False)
        )
    except Exception:
        error_detail = res.text

    if error_detail:
        raise Exception(f"API Error ({res.status_code}): {error_detail}")
    raise Exception(f"HTTP Error: {res.status_code}")

def get_models():
    cache_key = "ssy_modalities"
    cached = load_cache(cache_key)
    if cached:
        return cached
    try:
        res = requests.get(
            f"{SHENGSUANYUN_BASE_URL}/modalities/list?page=1&page_size=200",
            timeout=30,
        )
        try_res(res)
        data = res.json()
        if data.get("code") != 0:
            raise Exception("invalid response")
        infos = data.get("data", {}).get("infos", [])
        ids = [item["id"] for item in infos]
        results = []
        for i in range(0, len(ids), 10):
            batch = ids[i:i + 10]
            for model_id in batch:
                try:
                    r = requests.get(
                        f"{SHENGSUANYUN_BASE_URL}/modalities/info?model_id={model_id}",
                        timeout=60,
                    )
                    if not r.ok:
                        continue
                    j = r.json()
                    if j.get("code") == 0 and j.get("data"):
                        results.append(j["data"])

                except Exception:
                    continue
            if i + 10 < len(ids):
                time.sleep(0.5)
        if results:
            save_cache(cache_key, results)
        return results or (cached or [])
    except Exception:
        return cached or []

def get_api_key() -> Optional[str]:
    return os.getenv("SHENGSUANYUN_API_KEY")

def create_generation_task(model: str, params: Dict[str, Any]) -> str:
    api_key = get_api_key()
    if not api_key:
        raise ValueError("SHENGSUANYUN_API_KEY Environment variable not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://hermes-agent.nousresearch.com/",
        "X-Title": "Hermes Agent",
    }

    payload = {
        "model": model,
        **params
    }

    res = requests.post(
        f"{ROUTER_BASE_URL}/tasks/generations",
        headers=headers,
        json=payload,
        timeout=30
    )
    try_res(res)

    data = res.json()
    if data.get("code") != "success":
        raise Exception(f"API Error: {data.get('message', 'Unknown error')}")

    return data["data"]["request_id"]

def get_generation_result(task_id: str) -> Dict[str, Any]:
    api_key = get_api_key()
    if not api_key:
        raise ValueError("SHENGSUANYUN_API_KEY environment variable not set")

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    res = requests.get(
        f"{ROUTER_BASE_URL}/tasks/generations/{task_id}",
        headers=headers,
        timeout=30
    )
    try_res(res)

    data = res.json()
    if data.get("code") != "success":
        raise Exception(f"API Error: {data.get('message', 'Unknown error')}")

    return data["data"]

async def wait_for_completion(task_id: str, timeout: int = 300, poll_interval: int = 3) -> Dict[str, Any]:
    start_time = time.time()

    while time.time() - start_time < timeout:
        result = get_generation_result(task_id)
        status = result.get("status")

        if status == "SUCCEEDED":
            return result
        elif status == "FAILED":
            fail_reason = result.get("fail_reason", "Unknown error")
            raise Exception(f"Task failed: {fail_reason}")
        elif status in ["IN_PROGRESS", "PENDING"]:
            await asyncio.sleep(poll_interval)
        else:
            raise Exception(f"Unknown status: {status}")

    raise TimeoutError(f"Task {task_id} timed out after {timeout} seconds")

async def shengsuanyun_generation_tool(model_id: str, **params) -> str:
    try:
        logger.info(f"Creating generation task for model {model_id}")
        task_id = create_generation_task(model_id, params)

        logger.info(f"Task created: {task_id}, waiting for completion...")
        result = await wait_for_completion(task_id)

        output_data = result.get("data", {})

        return json.dumps({
            "success": True,
            "task_id": task_id,
            "result": output_data
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Error in generation: {e}", exc_info=True)
        return json.dumps({
            "success": False,
            "error": str(e)
        }, ensure_ascii=False)

def check_api_requirements() -> bool:
    return bool(get_api_key())

def _normalize_tool_name(text: str) -> str:
    return text.replace("/", "_").replace("-", "_").replace(".", "_").lower()


def _resolve_to_data_uri(path_or_url: str, default_mime: str = "image/jpeg") -> str:
    """Convert a local file path to a base64 data URI; HTTP/data/asset URLs pass through."""
    if path_or_url.startswith(("http://", "https://", "data:", "asset://")):
        return path_or_url
    from pathlib import Path
    import base64
    path = Path(path_or_url).expanduser()
    if not path.is_file():
        return path_or_url
    suffix = path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    }
    mime = mime_map.get(suffix, default_mime)
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _detect_schema_type(schema_obj: Dict[str, Any]) -> str:
    """Return the API input-format type of a model schema.

    "content_array" – uses a ``content[]`` field with typed items (Sora-style).
    "media_array"   – uses a ``media[]`` field with ``{type, url}`` objects.
    "direct"        – flat key/value params.
    """
    props = schema_obj.get("properties", {})
    content = props.get("content", {})
    if content.get("type") == "array":
        items = content.get("items", {})
        if items.get("type") == "object" and "type" in items.get("properties", {}):
            return "content_array"
    media = props.get("media", {})
    if media.get("type") == "array":
        if media.get("items", {}).get("type") == "object":
            return "media_array"
    # recurse into anyOf/oneOf variants
    for key in ("anyOf", "oneOf"):
        for sub in schema_obj.get(key, []):
            t = _detect_schema_type(sub)
            if t != "direct":
                return t
    return "direct"


def _content_array_capabilities(schema_obj: Dict[str, Any]) -> Dict[str, bool]:
    """Return which media-item types a content_array schema supports."""
    props = schema_obj.get("properties", {})
    type_enum = (
        props.get("content", {})
        .get("items", {})
        .get("properties", {})
        .get("type", {})
        .get("enum", [])
    )
    return {
        "image": "image_url" in type_enum,
        "video": "video_url" in type_enum,
        "audio": "audio_url" in type_enum,
    }

def _anyof_variant_summary(schema_obj: Dict[str, Any]) -> str:
    """Build a concise English summary of anyOf/oneOf variants for tool descriptions."""
    for key in ("anyOf", "oneOf"):
        variants = schema_obj.get(key, [])
        if not variants:
            continue
        parts = []
        for v in variants:
            title = v.get("title", "")
            req = [r for r in v.get("required", []) if r != "prompt"]
            if req:
                parts.append(f"{title}(+{','.join(req)})" if title else f"+{','.join(req)}")
            else:
                parts.append(title or "prompt-only")
        return "Modes: " + " | ".join(parts)
    return ""


def _build_content_array_payload(args: Dict[str, Any]) -> Dict[str, Any]:
    """Convert flat tool args to the content-array payload expected by the API."""
    payload = dict(args)
    prompt    = payload.pop("prompt",    None)
    image_url = payload.pop("image_url", None)
    video_url = payload.pop("video_url", None)
    audio_url = payload.pop("audio_url", None)

    content: list = []
    if prompt:
        content.append({"type": "text", "text": prompt})
    if image_url:
        content.append({
            "type": "image_url",
            "role": "first_frame",
            "image_url": {"url": _resolve_to_data_uri(image_url)},
        })
    if video_url:
        content.append({
            "type": "video_url",
            "role": "reference_video",
            "video_url": {"url": video_url},
        })
    if audio_url:
        content.append({
            "type": "audio_url",
            "role": "reference_audio",
            "audio_url": {"url": audio_url},
        })
    payload["content"] = content
    return payload


def _build_media_array_payload(args: Dict[str, Any]) -> Dict[str, Any]:
    """Convert flat tool args to the media-array payload expected by the API."""
    payload = dict(args)
    image_url = payload.pop("image_url", None)
    if image_url:
        payload["media"] = [{"type": "first_frame", "url": _resolve_to_data_uri(image_url)}]
    return payload


def _create_handler(mid: str, schema_type: str, capabilities: Dict[str, bool]):
    """Return an async tool handler bound to *mid* and the given schema metadata.

    All parameters are passed by value so each handler has its own independent
    copy — this avoids the closure-over-loop-variable bug.
    """
    async def handler(args, **kw):
        if schema_type == "content_array":
            processed_args = _build_content_array_payload(dict(args))
        elif schema_type == "media_array":
            processed_args = _build_media_array_payload(dict(args))
        else:
            processed_args = dict(args)
            for key, mime in [("image", "image/jpeg"), ("video", "video/mp4"), ("audio", "audio/mpeg")]:
                if key in processed_args and isinstance(processed_args[key], str):
                    processed_args[key] = _resolve_to_data_uri(processed_args[key], mime)
        return await shengsuanyun_generation_tool(mid, **processed_args)
    return handler

def _extract_simple_properties(schema_obj: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    properties = {}
    required = []

    schema_properties = schema_obj.get("properties", {})
    schema_required = schema_obj.get("required", [])

    for prop_name, prop_def in schema_properties.items():
        prop_type = prop_def.get("type", "string")

        if prop_type == "array":
            if prop_name in ["content", "messages"]:
                continue
            if "items" in prop_def and isinstance(prop_def["items"], dict):
                item_type = prop_def["items"].get("type")
                if item_type == "object" and "properties" in prop_def["items"]:
                    continue

        if prop_type == "object" and "properties" in prop_def:
            continue

        prop_schema = {
            "type": prop_type,
            "description": prop_def.get("title", prop_def.get("description", ""))
        }

        if prop_type == "array" and "items" in prop_def:
            prop_schema["items"] = prop_def["items"]

        if "enum" in prop_def:
            prop_schema["enum"] = prop_def["enum"]

        if "default" in prop_def:
            prop_schema["default"] = prop_def["default"]

        if "minimum" in prop_def:
            prop_schema["minimum"] = prop_def["minimum"]

        if "maximum" in prop_def:
            prop_schema["maximum"] = prop_def["maximum"]

        if "minLength" in prop_def:
            prop_schema["minLength"] = prop_def["minLength"]

        if "maxLength" in prop_def:
            prop_schema["maxLength"] = prop_def["maxLength"]

        properties[prop_name] = prop_schema

        if prop_name in schema_required:
            required.append(prop_name)

    return properties, required

def _parse_input_schema(input_schema_str: str) -> tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """Parse a model's input_schema string.

    Returns ``(properties, required, schema_meta)`` where *schema_meta* is::

        {
            "type":         "content_array" | "media_array" | "direct",
            "capabilities": {"image": bool, "video": bool, "audio": bool},
            "variant_summary": str,   # human-readable anyOf summary
        }
    """
    schema_meta: Dict[str, Any] = {
        "type": "direct", "capabilities": {}, "variant_summary": "",
    }
    try:
        if not input_schema_str or not isinstance(input_schema_str, str):
            return {}, [], schema_meta

        schema_obj = json.loads(input_schema_str)
        schema_type = _detect_schema_type(schema_obj)
        schema_meta["type"] = schema_type
        schema_meta["variant_summary"] = _anyof_variant_summary(schema_obj)

        if schema_type == "content_array":
            return _parse_content_array_schema(schema_obj, schema_meta)
        if schema_type == "media_array":
            return _parse_media_array_schema(schema_obj, schema_meta)

        # ---------- direct schema ----------
        properties: Dict[str, Any] = {}
        required: List[str] = []

        if "properties" in schema_obj:
            properties, required = _extract_simple_properties(schema_obj)

        elif "anyOf" in schema_obj or "oneOf" in schema_obj or "allOf" in schema_obj:
            is_all_of = (
                "allOf" in schema_obj
                and "anyOf" not in schema_obj
                and "oneOf" not in schema_obj
            )
            schemas_list = (
                schema_obj.get("anyOf", [])
                or schema_obj.get("oneOf", [])
                or schema_obj.get("allOf", [])
            )
            all_sub_required: List[set] = []
            for sub_schema in schemas_list:
                if "properties" in sub_schema:
                    sub_props, sub_req = _extract_simple_properties(sub_schema)
                    properties.update(sub_props)
                    all_sub_required.append(set(sub_req))
            if all_sub_required:
                if is_all_of:
                    required = list(set.union(*all_sub_required))
                else:
                    required = list(set.intersection(*all_sub_required))

        if "content" in schema_obj.get("properties", {}):
            properties["prompt"] = {
                "type": "string",
                "description": "Text prompt or description for generation"
            }
            if "content" in schema_obj.get("required", []):
                if "prompt" not in required:
                    required.append("prompt")

        return properties, required, schema_meta

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse input_schema: {e}")
        return {}, [], schema_meta
    except Exception as e:
        logger.warning(f"Error extracting schema properties: {e}")
        return {}, [], schema_meta


def _parse_content_array_schema(
    schema_obj: Dict[str, Any], schema_meta: Dict[str, Any]
) -> tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """Build a flat tool schema for content-array (Sora-style) models.

    Exposes ``prompt`` plus optional ``image_url`` / ``video_url`` / ``audio_url``
    parameters instead of the raw ``content[]`` array.
    """
    caps = _content_array_capabilities(schema_obj)
    schema_meta["capabilities"] = caps

    properties: Dict[str, Any] = {
        "prompt": {
            "type": "string",
            "description": "Text description or instruction for generation",
        }
    }
    required = ["prompt"]

    if caps.get("image"):
        properties["image_url"] = {
            "type": "string",
            "description": (
                "Image URL or local file path to use as first-frame reference "
                "(JPEG/PNG/WEBP, ≥ 300 px, ≤ 10 MB)"
            ),
        }
    if caps.get("video"):
        properties["video_url"] = {
            "type": "string",
            "description": "Video URL or local file path to use as reference video",
        }
    if caps.get("audio"):
        properties["audio_url"] = {
            "type": "string",
            "description": "Audio URL or local file path to use as reference audio",
        }

    # Append other simple scalar params (resolution, ratio, duration, …)
    other_props, _ = _extract_simple_properties(schema_obj)
    for k, v in other_props.items():
        if k not in properties:
            properties[k] = v

    return properties, required, schema_meta


def _parse_media_array_schema(
    schema_obj: Dict[str, Any], schema_meta: Dict[str, Any]
) -> tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    """Build a flat tool schema for media-array (first-frame image-to-video) models."""
    schema_meta["capabilities"] = {"image": True}

    properties: Dict[str, Any] = {
        "image_url": {
            "type": "string",
            "description": (
                "First-frame image URL or local file path "
                "(JPEG/PNG/WEBP; ≥ 300 px wide & tall; ratio 1:2.5–2.5:1; ≤ 10 MB)"
            ),
        }
    }
    required = ["image_url"]

    other_props, _ = _extract_simple_properties(schema_obj)
    for k, v in other_props.items():
        if k not in properties:
            properties[k] = v

    return properties, required, schema_meta

def register_shengsuanyun_tools():
    models = get_models()

    for model_info in models:
        try:
            model_name = model_info.get("model_name", model_info.get("name", ""))
            api_name = model_info.get("api_name", "")
            description = model_info.get("desc", model_info.get("description", ""))
            input_schema_str = model_info.get("input_schema", "")
            class_names = model_info.get("class_names", [])

            if not api_name:
                continue

            modality = "unknown"
            if class_names:
                first_class = class_names[0]
                if "图像" in first_class or "图片" in first_class:
                    modality = "image"
                elif "视频" in first_class:
                    modality = "video"
                elif "音频" in first_class or "语音" in first_class:
                    modality = "audio"
                elif "文本" in first_class:
                    modality = "text"

            properties, required, schema_meta = _parse_input_schema(input_schema_str)

            if not properties:
                properties = {
                    "prompt": {
                        "type": "string",
                        "description": "Input prompt or content for generation"
                    }
                }
                required = ["prompt"]

            tool_name = f"shengsuanyun_{_normalize_tool_name(api_name)}"

            full_description = model_name
            if description:
                full_description += f": {description}"
            if class_names:
                full_description += f" ({', '.join(class_names)})"
            variant_summary = schema_meta.get("variant_summary", "")
            if variant_summary:
                full_description += f". {variant_summary}"

            tool_schema = {
                "name": tool_name,
                "description": full_description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }

            emoji_map = {"image": "🎨", "video": "🎬", "audio": "🔊", "text": "📝"}
            emoji = emoji_map.get(modality.lower(), "✨")

            registry.register(
                name=tool_name,
                toolset="shengsuanyun",
                schema=tool_schema,
                handler=_create_handler(
                    api_name,
                    schema_meta["type"],
                    schema_meta.get("capabilities", {}),
                ),
                check_fn=check_api_requirements,
                is_async=True,
                emoji=emoji,
            )

            logger.debug(f"Registered tool: {tool_name} ({modality}, {schema_meta['type']})")

        except Exception as e:
            logger.warning(f"Failed to register model {model_info.get('id')}: {e}")
            continue

def _list_models_handler(args, **kw):
    models = get_models()
    summary = [
        {
            "api_name": m.get("api_name", ""),
            "model_name": m.get("model_name", ""),
            "desc": m.get("desc", m.get("description", ""))[:120],
            "class_names": m.get("class_names", []),
        }
        for m in models
    ]
    return json.dumps({"count": len(summary), "models": summary}, ensure_ascii=False)


# This top-level registry.register() call is required so that
# discover_builtin_tools() AST scan detects this file as a tool module.
registry.register(
    name="shengsuanyun_list_models",
    toolset="shengsuanyun",
    schema={
        "name": "shengsuanyun_list_models",
        "description": (
            "List all available Shengsuanyun AI generation models "
            "(image / video / audio / text). Returns api_name, model_name, "
            "description and category for each model."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    handler=_list_models_handler,
    check_fn=check_api_requirements,
    is_async=False,
    emoji="📋",
)

try:
    register_shengsuanyun_tools()
except Exception as e:
    logger.warning(f"Failed to register shengsuanyun tools: {e}")

# if __name__ == "__main__":
#     models = get_models()
#     print(f"Found {len(models)} models")

#     for i, model in enumerate(models[:10]):
#         print(f"\n{'='*60}")
#         print(f"Model {i+1}: {model.get('model_name', 'N/A')}")
#         print(f"API: {model.get('api_name', 'N/A')}")
#         print(f"Classes: {model.get('class_names', [])}")

#         input_schema_str = model.get("input_schema", "")
#         if input_schema_str:
#             try:
#                 schema_obj = json.loads(input_schema_str)
#                 # with open(f"schema_{model.get('id', 'unknown')}.json", "w", encoding="utf-8") as f:
#                 #     json.dump(schema_obj, f, ensure_ascii=False, indent=2)  
#                 props, req = _extract_simple_properties(schema_obj)
#                 print(f"Properties: {list(props.keys())}")
#                 print(f"Required: {req}")
#             except Exception as e:
#                 print(f"Schema parse error: {e}")
#         else:
#             print("No input schema")