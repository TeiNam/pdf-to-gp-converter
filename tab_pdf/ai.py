"""AI 백엔드 — JSON 응답 하나만 받아오는 얇은 경계.

설정은 `.env` 에 OpenAI 형식으로 둔다. `OPENAI_BASE_URL` 만 바꿔서 ollama·MLX·
vLLM·LM Studio·OpenRouter·OpenAI 를 전부 쓴다.

Bedrock 은 별도 경로다. Bedrock 의 OpenAI 호환 엔드포인트
(`/openai/v1/chat/completions`) 는 Anthropic 모델을 받지 않는다 — 실측에서
`global.anthropic.claude-sonnet-5` 로 404 `model_not_found` 가 났다. 그래서
베어러 토큰으로 Converse API 를 직접 부른다.

프롬프트·악보 해석은 전혀 모른다. 여기서 하는 일은 문자열을 보내고 JSON 을
받아 파싱하는 것뿐이다.
"""

import functools
import json
import os
import re
from dataclasses import dataclass

BACKEND_OPENAI = "openai"
BACKEND_BEDROCK = "bedrock"
BACKENDS = (BACKEND_OPENAI, BACKEND_BEDROCK)

DEFAULT_MAX_TOKENS = 8192
# 악보 해석은 창작이 아니다 — 같은 입력에 같은 답이 나와야 한다
DEFAULT_TEMPERATURE = 0.0
# JSON 이 아닌 답을 한 번은 되물어본다. 로컬 소형 모델이 앞뒤에 설명을 붙이는 일이 잦다
MAX_ATTEMPTS = 2
# 응답 대기 상한 (초). 실측 한 배치 25초라 넉넉히 잡는다. boto3 기본값 60초로는
# 큰 배치에서 타임아웃 뒤 재시도까지 겹쳐 한 배치가 5분 넘게 걸렸다.
READ_TIMEOUT = 180
CONNECT_TIMEOUT = 10
# 타임아웃 재시도가 겹치면 대기 시간이 곱해진다 — boto3 기본 5회는 너무 많다
MAX_RETRIES = 2
# 응답에서 JSON 객체를 건져낼 최후 수단 — 가장 바깥 중괄호 쌍
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


class AiUnavailable(RuntimeError):
    """AI 백엔드를 쓸 수 없다 — 설정 누락, 의존성 누락, 응답 파싱 실패."""


@dataclass(frozen=True)
class Config:
    backend: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    region: str | None = None
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS

    @property
    def label(self) -> str:
        where = self.base_url or self.region or "?"
        return f"{self.backend}:{self.model} @ {where}"


def _load_dotenv() -> None:
    """`.env` 를 읽어 환경변수에 채운다. 이미 있는 값은 덮지 않는다.

    python-dotenv 가 없으면 조용히 넘긴다 — 환경변수를 직접 export 한 경우다.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(override=False)


def _detect_backend(env) -> str | None:
    if env.get("OPENAI_API_KEY") or env.get("OPENAI_BASE_URL"):
        return BACKEND_OPENAI
    if env.get("AWS_BEARER_TOKEN_BEDROCK"):
        return BACKEND_BEDROCK
    return None


def load_config(env: dict | None = None) -> Config:
    """`.env`·환경변수에서 설정을 읽는다. 못 쓰는 상태면 AiUnavailable."""
    if env is None:
        _load_dotenv()
        env = dict(os.environ)

    backend = (env.get("AI_BACKEND") or "").strip().lower() or _detect_backend(env)
    if backend is None:
        raise AiUnavailable(
            "AI 설정이 없습니다. .env.example 을 .env 로 복사해 채우세요 "
            "(OPENAI_API_KEY 또는 AWS_BEARER_TOKEN_BEDROCK)")
    if backend not in BACKENDS:
        raise AiUnavailable(
            f"AI_BACKEND={backend!r} 를 모릅니다. {' | '.join(BACKENDS)} 중 하나여야 합니다")

    model = (env.get("OPENAI_MODEL") or env.get("AI_MODEL") or "").strip()
    if not model:
        raise AiUnavailable("OPENAI_MODEL 이 비어 있습니다 — 쓸 모델 id 를 지정하세요")

    common = {
        "backend": backend,
        "model": model,
        "temperature": _float_env(env, "AI_TEMPERATURE", DEFAULT_TEMPERATURE),
        "max_tokens": _int_env(env, "AI_MAX_TOKENS", DEFAULT_MAX_TOKENS),
    }
    if backend == BACKEND_BEDROCK:
        region = (env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "").strip()
        if not region:
            raise AiUnavailable("bedrock 백엔드에는 AWS_REGION 이 필요합니다")
        if not env.get("AWS_BEARER_TOKEN_BEDROCK"):
            raise AiUnavailable("bedrock 백엔드에는 AWS_BEARER_TOKEN_BEDROCK 이 필요합니다")
        return Config(region=region, **common)

    # ollama·MLX 는 키를 검사하지 않지만 OpenAI SDK 는 빈 키를 거부한다
    return Config(base_url=(env.get("OPENAI_BASE_URL") or "").strip() or None,
                  api_key=(env.get("OPENAI_API_KEY") or "").strip() or "not-needed",
                  **common)


def _float_env(env, name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise AiUnavailable(f"{name}={raw!r} 는 숫자가 아닙니다") from exc


def _int_env(env, name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise AiUnavailable(f"{name}={raw!r} 는 정수가 아닙니다") from exc


def _rejects_temperature(exc: Exception) -> bool:
    """서버가 temperature 를 안 받는다고 답했는지.

    최신 Claude 모델은 `temperature is deprecated for this model` 로 요청 자체를
    거부한다 (실측: bedrock Converse + claude-sonnet-5). 모델별 지원 여부를 미리
    알 방법이 없어 거부를 보고 빼는 수밖에 없다.
    """
    return "temperature" in str(exc).lower()


@functools.lru_cache(maxsize=4)
def _openai_client(base_url: str | None, api_key: str):
    """클라이언트를 재사용한다 — 배치마다 새로 만들면 연결을 매번 다시 맺는다."""
    import openai

    return openai.OpenAI(base_url=base_url, api_key=api_key,
                         timeout=READ_TIMEOUT, max_retries=MAX_RETRIES)


@functools.lru_cache(maxsize=4)
def _bedrock_client(region: str):
    import boto3
    import botocore.config

    return boto3.client(
        "bedrock-runtime", region_name=region,
        config=botocore.config.Config(
            connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT,
            retries={"max_attempts": MAX_RETRIES, "mode": "standard"}),
    )


def _complete_openai(config: Config, system: str, user: str) -> str:
    try:
        import openai
    except ImportError as exc:
        raise AiUnavailable("openai 패키지가 없습니다 — uv add openai") from exc

    client = _openai_client(config.base_url, config.api_key)
    base = {
        "model": config.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_completion_tokens": config.max_tokens,
    }
    # json_object 를 모르는 서버(구버전 ollama·일부 vLLM)와 temperature 를 거부하는
    # 모델이 따로 있어, 거부당한 옵션만 떼고 다시 보낸다.
    attempts = (
        {**base, "temperature": config.temperature,
         "response_format": {"type": "json_object"}},
        {**base, "response_format": {"type": "json_object"}},
        {**base, "temperature": config.temperature},
        base,
    )
    failure = None
    for index, kwargs in enumerate(attempts):
        try:
            response = client.chat.completions.create(**kwargs)
        except openai.BadRequestError as exc:
            failure = exc
            continue
        except openai.OpenAIError as exc:
            raise AiUnavailable(f"{config.label} 호출 실패: {exc}") from exc
        return response.choices[0].message.content or ""
    raise AiUnavailable(f"{config.label} 이 요청을 거부했습니다: {failure}") from failure


def _complete_bedrock(config: Config, system: str, user: str) -> str:
    try:
        import botocore.exceptions
    except ImportError as exc:
        raise AiUnavailable("boto3 가 없습니다 — uv add boto3") from exc

    client = _bedrock_client(config.region)
    request = {
        "modelId": config.model,
        "system": [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": user}]}],
    }
    for inference in ({"maxTokens": config.max_tokens,
                       "temperature": config.temperature},
                      {"maxTokens": config.max_tokens}):
        try:
            response = client.converse(inferenceConfig=inference, **request)
        except botocore.exceptions.ClientError as exc:
            if "temperature" in inference and _rejects_temperature(exc):
                continue
            raise AiUnavailable(f"{config.label} 호출 거부: {exc}") from exc
        except botocore.exceptions.BotoCoreError as exc:
            raise AiUnavailable(f"{config.label} 호출 실패: {exc}") from exc
        blocks = response["output"]["message"]["content"]
        return "".join(block.get("text", "") for block in blocks)
    raise AiUnavailable(f"{config.label} 이 요청을 거부했습니다")


_BACKEND_FUNCTIONS = {
    BACKEND_OPENAI: _complete_openai,
    BACKEND_BEDROCK: _complete_bedrock,
}


def parse_json_object(text: str) -> dict:
    """응답 문자열에서 JSON 객체를 꺼낸다. 실패하면 AiUnavailable.

    모델이 ```json 펜스나 앞말을 붙이는 경우가 잦아 가장 바깥 중괄호를 건져낸다.
    """
    for candidate in (text.strip(), _first_object(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AiUnavailable(f"JSON 객체를 못 찾았습니다: {text[:200]!r}")


def _first_object(text: str) -> str | None:
    match = _JSON_OBJECT.search(text)
    return match.group(0) if match else None


def ask_json(config: Config, system: str, user: str) -> dict:
    """JSON 객체 하나를 받아온다. 형식이 틀리면 한 번 되묻는다."""
    complete = _BACKEND_FUNCTIONS[config.backend]
    prompt, last_error = user, None
    for _ in range(MAX_ATTEMPTS):
        text = complete(config, system, prompt)
        try:
            return parse_json_object(text)
        except AiUnavailable as exc:
            last_error = exc
            prompt = (f"{user}\n\n---\n"
                      "직전 응답이 JSON 객체가 아니었습니다. 설명·머리말·코드펜스 없이 "
                      "여는 중괄호로 시작하는 JSON 객체만 출력하세요.")
    raise last_error
