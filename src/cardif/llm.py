"""Local model client for the headers and filenames the rules could not resolve.

Scope is deliberately narrow. The model never sees rows in bulk, never computes a
number, and never decides what a value *is*. It answers two questions only:

* "which canonical field does this header name mean?"
* "which month does this filename refer to?"

and every answer it gives is cached to ``aliases.yaml``, so a given question is asked at
most once in the lifetime of the project.

Privacy. By default the model receives the header text and an *anonymised type profile*
of the column ("38 non-empty values, 97% distinct, all parse as dates") rather than the
values themselves, so client names and contract numbers never leave the dataframe — not
even to localhost. Sending samples is an explicit opt-in. The client also refuses any
non-loopback endpoint, which turns the offline guarantee into something checkable rather
than a promise.

Only the standard library is used for HTTP, so no dependency can quietly introduce
telemetry.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import Config, LLMSettings
from .mapping import ColumnProfile

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class OfflineViolation(RuntimeError):
    """Raised when the configured endpoint is not on this machine.

    The whole point of the local model is that insurance data never leaves the machine.
    A misconfigured base_url would silently break that, so it is a hard failure rather
    than a warning.
    """


def assert_loopback(base_url: str) -> None:
    """Refuse any endpoint that is not on the local machine."""
    host = urlparse(base_url).hostname
    if host is None:
        raise OfflineViolation(f"cannot determine host from base_url {base_url!r}")
    if host not in _LOOPBACK_HOSTS:
        raise OfflineViolation(
            f"refusing to send data to non-local host {host!r}. This tool is offline by "
            "design; set llm.require_loopback: false in settings.yaml only if you have "
            "deliberately accepted the data-protection consequences."
        )


@dataclass
class ModelAnswer:
    value: str | None
    confidence: float
    reason: str


class LocalModel:
    """Thin OpenAI-compatible client aimed at LM Studio / llama.cpp."""

    def __init__(self, settings: LLMSettings):
        self.settings = settings
        if settings.require_loopback:
            assert_loopback(settings.base_url)
        self.calls = 0
        self.failures = 0

    # -- transport ---------------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        url = self.settings.base_url.rstrip("/") + path
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        # No proxy handler: a local endpoint must never be routed through one.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=self.settings.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def available(self) -> bool:
        """Whether the endpoint is reachable, used to degrade gracefully in the UI."""
        try:
            url = self.settings.base_url.rstrip("/") + "/models"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=5):
                return True
        except Exception:
            return False

    def _complete(self, system: str, user: str, schema: dict) -> dict | None:
        """Ask for one strict-JSON answer, retrying once on unparseable output."""
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            # LM Studio and recent llama.cpp builds honour this; servers that do not
            # simply ignore it and we fall back to parsing the text.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True, "schema": schema},
            },
        }
        for attempt in range(2):
            try:
                self.calls += 1
                response = self._post("/chat/completions", payload)
                content = response["choices"][0]["message"]["content"]
                parsed = _extract_json(content)
                if parsed is not None:
                    return parsed
            except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError):
                self.failures += 1
                return None
            # Second attempt drops the schema, for servers that reject the field.
            payload.pop("response_format", None)
        self.failures += 1
        return None

    # -- the two questions -------------------------------------------------------

    def resolve_header(
        self, header: str, profile: ColumnProfile, candidates: list[str],
        field_descriptions: dict[str, str] | None = None,
    ) -> ModelAnswer:
        """Ask which canonical field a header names.

        The prompt carries the header, the anonymised column profile, and the list of
        allowed answers. The model may answer ``none``; a wrong guess is far more
        expensive than an admission of ignorance, so the prompt says so explicitly.
        """
        descriptions = field_descriptions or {}
        catalogue = "\n".join(
            f"- {name}: {descriptions.get(name, '')}".rstrip(": ")
            for name in candidates
        )
        profile_text = profile.describe(
            include_samples=self.settings.send_sample_values
        )

        system = (
            "You map column headers from French bank spreadsheets to a fixed catalogue "
            "of life-insurance fields. Answer only with one of the given field names, "
            "or \"none\" when no field clearly fits. A wrong mapping corrupts a "
            "financial database, so answer \"none\" whenever you are unsure. Reply with "
            "JSON only."
        )
        user = (
            f"Column header: {header!r}\n"
            f"Column contents: {profile_text}\n\n"
            f"Candidate fields:\n{catalogue}\n\n"
            'Reply as {"field": "<name or none>", "confidence": <0..1>, '
            '"reason": "<short justification>"}'
        )
        schema = {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["field", "confidence", "reason"],
            "additionalProperties": False,
        }

        result = self._complete(system, user, schema)
        if not result:
            return ModelAnswer(None, 0.0, "model unavailable or gave no usable answer")

        value = str(result.get("field", "")).strip()
        confidence = _clamp(result.get("confidence", 0.0))
        reason = str(result.get("reason", ""))[:200]
        # The model must not invent a field name outside the catalogue.
        if value in {"", "none", "null", "None"} or value not in candidates:
            return ModelAnswer(None, confidence, reason or "model found no matching field")
        return ModelAnswer(value, confidence, reason)

    def resolve_period(self, filename: str) -> ModelAnswer:
        """Ask which month a filename refers to, when the patterns all missed.

        Only the filename string is sent — never the file's contents.
        """
        system = (
            "You extract the reporting month from French bank report filenames. "
            "Answer \"none\" if the filename does not clearly name a single month. "
            "Reply with JSON only."
        )
        user = (
            f"Filename: {filename!r}\n\n"
            'Reply as {"year": <YYYY or null>, "month": <1-12 or null>, '
            '"confidence": <0..1>, "reason": "<short justification>"}'
        )
        schema = {
            "type": "object",
            "properties": {
                "year": {"type": ["integer", "null"]},
                "month": {"type": ["integer", "null"]},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["year", "month", "confidence", "reason"],
            "additionalProperties": False,
        }

        result = self._complete(system, user, schema)
        if not result:
            return ModelAnswer(None, 0.0, "model unavailable or gave no usable answer")

        year, month = result.get("year"), result.get("month")
        confidence = _clamp(result.get("confidence", 0.0))
        reason = str(result.get("reason", ""))[:200]
        if not isinstance(year, int) or not isinstance(month, int):
            return ModelAnswer(None, confidence, reason or "model could not read a period")
        if not (1 <= month <= 12) or not (2000 <= year <= 2099):
            return ModelAnswer(None, 0.0, f"model returned an implausible period {year}-{month}")
        return ModelAnswer(f"{year:04d}-{month:02d}", confidence, reason)


def _clamp(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _extract_json(content: str) -> dict | None:
    """Pull a JSON object out of a model reply that may be wrapped in prose or fences."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def make_resolver(config: Config, model: LocalModel | None = None):
    """Build the callable :class:`cardif.mapping.Mapper` uses as its third tier.

    Returns ``None`` when the model is disabled, so the pipeline runs fully
    deterministically without any special-casing at the call site.
    """
    if not config.settings.llm.enabled:
        return None
    model = model or LocalModel(config.settings.llm)
    descriptions = {
        name: field.description for name, field in config.schema_.fields.items()
    }

    def resolve(header: str, profile: ColumnProfile, candidates: list[str]):
        answer = model.resolve_header(header, profile, candidates, descriptions)
        return answer.value, answer.confidence, f"model: {answer.reason}"

    return resolve
