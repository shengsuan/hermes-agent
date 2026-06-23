import json
import logging
import urllib.request
from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)
class ShengSuanYunProfile(ProviderProfile):
    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        if not api_key:
            return None
        try:
            req = urllib.request.Request("https://router.shengsuanyun.com/api/v1/models")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            return [
                m["api_name"]
                for m in data.get("data", [])
                if isinstance(m, dict) and "api_name" in m and "/v1/messages" in (m.get("support_apis") or [])
            ]
        except Exception as exc:
            logger.debug("fetch_models(shengsuanyun): %s", exc)
            return None

shengsuanyun = ShengSuanYunProfile(
    name="shengsuanyun",
    aliases=("ssy", "sheng-suan-yun"),
    display_name="胜算云",
    description="胜算云 — 多模型云端 API",
    signup_url="https://shengsuanyun.com/",
    env_vars=("SHENGSUANYUN_API_KEY", "SHENGSUANYUN_BASE_URL"),
    base_url="https://router.shengsuanyun.com/api/v1",
    auth_type="api_key",
    fallback_models=(
        "anthropic/claude-opus-4.7",
        "anthropic/claude-opus-4.5",
        "anthropic/claude-opus-4.6",
        "openai/gpt-5.1",
        "openai/gpt-5.4",
        "openai/gpt-5.3-chat",
        "google/gemini-3-flash",
        "google/gemini-2.5-flash"
    ),
    default_aux_model="google/gemini-2.5-pro",
    api_mode="anthropic_messages",
)

register_provider(shengsuanyun)