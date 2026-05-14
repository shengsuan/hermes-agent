#!/usr/bin/env python3

import json
import time
import logging
import asyncio
import os
from typing import Any, Dict, Optional, List
import requests

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

def _parse_input_schema(input_schema_str: str) -> tuple[Dict[str, Any], List[str]]:
    try:
        if not input_schema_str or not isinstance(input_schema_str, str):
            return {}, []

        schema_obj = json.loads(input_schema_str)

        properties = {}
        required = []

        if "properties" in schema_obj:
            properties, required = _extract_simple_properties(schema_obj)

        elif "anyOf" in schema_obj or "oneOf" in schema_obj or "allOf" in schema_obj:
            schemas_list = (
                schema_obj.get("anyOf", [])
                or schema_obj.get("oneOf", [])
                or schema_obj.get("allOf", [])
            )
            for sub_schema in schemas_list:
                if "properties" in sub_schema:
                    sub_props, sub_req = _extract_simple_properties(sub_schema)
                    properties.update(sub_props)
                    required.extend(sub_req)
            required = list(set(required))

        if "content" in schema_obj.get("properties", {}):
            properties["prompt"] = {
                "type": "string",
                "description": "Text prompt or description for generation"
            }
            if "content" in schema_obj.get("required", []):
                if "prompt" not in required:
                    required.append("prompt")

        return properties, required

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse input_schema: {e}")
        return {}, []
    except Exception as e:
        logger.warning(f"Error extracting schema properties: {e}")
        return {}, []

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

            properties, required = _parse_input_schema(input_schema_str)

            if not properties:
                properties = {
                    "prompt": {
                        "type": "string",
                        "description": "Input prompt or content for generation"
                    }
                }
                required = ["prompt"]

            tool_name = f"shengsuanyun_{_normalize_tool_name(api_name)}"

            full_description = f"{model_name}"
            if description:
                full_description += f": {description}"
            if class_names:
                full_description += f" ({', '.join(class_names)})"

            tool_schema = {
                "name": tool_name,
                "description": full_description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }

            def create_handler(mid):
                async def handler(args, **kw):
                    processed_args = dict(args)

                    if "prompt" in processed_args and "prompt" not in properties:
                        prompt_text = processed_args.pop("prompt")
                        processed_args["content"] = [
                            {"type": "text", "text": prompt_text}
                        ]

                    for key in ["image", "video", "audio"]:
                        if key in processed_args and isinstance(processed_args[key], str):
                            url = processed_args[key]
                            if not url.startswith(("http://", "https://", "data:", "asset://")):
                                from pathlib import Path
                                import base64

                                path = Path(url).expanduser()
                                if path.is_file():
                                    mime_type = "image/jpeg" if key == "image" else "video/mp4" if key == "video" else "audio/mpeg"
                                    data = path.read_bytes()
                                    b64 = base64.b64encode(data).decode("ascii")
                                    processed_args[key] = f"data:{mime_type};base64,{b64}"

                    return await shengsuanyun_generation_tool(mid, **processed_args)
                return handler

            emoji_map = {
                "image": "🎨",
                "video": "🎬",
                "audio": "🔊",
                "text": "📝",
            }
            emoji = emoji_map.get(modality.lower(), "✨")

            registry.register(
                name=tool_name,
                toolset="shengsuanyun",
                schema=tool_schema,
                handler=create_handler(api_name),
                check_fn=check_api_requirements,
                is_async=True,
                emoji=emoji
            )

            logger.debug(f"Registered tool: {tool_name} ({modality})")

        except Exception as e:
            logger.warning(f"Failed to register model {model_info.get('id')}: {e}")
            continue

try:
    register_shengsuanyun_tools()
except Exception as e:
    logger.warning(f"Failed to register shengsuanyun tools: {e}")

if __name__ == "__main__":
    models = get_models()
    print(f"Found {len(models)} models")

    for i, model in enumerate(models[:10]):
        print(f"\n{'='*60}")
        print(f"Model {i+1}: {model.get('model_name', 'N/A')}")
        print(f"API: {model.get('api_name', 'N/A')}")
        print(f"Classes: {model.get('class_names', [])}")

        input_schema_str = model.get("input_schema", "")
        if input_schema_str:
            try:
                schema_obj = json.loads(input_schema_str)
                props, req = _extract_simple_properties(schema_obj)
                print(f"Properties: {list(props.keys())}")
                print(f"Required: {req}")
            except Exception as e:
                print(f"Schema parse error: {e}")
