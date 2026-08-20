"""The model tier, and the privacy guarantees around it.

These tests are the reason the offline claim is checkable rather than a promise.
"""

import pytest

from cardif.config import LLMSettings
from cardif.llm import LocalModel, OfflineViolation, _extract_json, assert_loopback
from cardif.mapping import ColumnProfile


PROFILE = ColumnProfile(
    total=60, non_blank=60, numeric_ratio=1.0, date_ratio=0.0, unique_ratio=0.98,
    min_value=112.5, max_value=4210.75, mean_length=7,
    samples=["Mohamed Ben Ali", "BNA-202502-0001", "1 234,56"],
)


class Spy(LocalModel):
    """Captures the payload instead of sending it."""

    reply = '{"field": "prime_totale", "confidence": 0.91, "reason": "montant global"}'

    def __init__(self, settings):
        super().__init__(settings)
        self.payload = None

    def _post(self, path, payload):
        self.payload = payload
        return {"choices": [{"message": {"content": self.reply}}]}

    @property
    def prompt(self) -> str:
        return self.payload["messages"][1]["content"]


class TestOfflineGuard:
    @pytest.mark.parametrize("url", ["http://localhost:1234/v1", "http://127.0.0.1:8080/v1"])
    def test_loopback_allowed(self, url):
        assert_loopback(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "http://192.168.1.50:1234/v1",   # LAN is not this machine
            "http://evil.example.com/v1",
        ],
    )
    def test_non_loopback_refused(self, url):
        with pytest.raises(OfflineViolation):
            assert_loopback(url)

    def test_constructor_enforces_the_guard(self):
        with pytest.raises(OfflineViolation):
            LocalModel(LLMSettings(base_url="https://api.anthropic.com/v1"))


class TestWhatLeavesTheMachine:
    def test_cell_values_are_not_sent_by_default(self):
        """Client names and contract numbers must not reach the model, even locally."""
        model = Spy(LLMSettings())
        model.resolve_header("Mtt Glob", PROFILE, ["prime_totale", "frais"], {})
        for sample in PROFILE.samples:
            assert sample not in model.prompt

    def test_an_anonymised_profile_is_sent_instead(self):
        model = Spy(LLMSettings())
        model.resolve_header("Mtt Glob", PROFILE, ["prime_totale"], {})
        assert "Mtt Glob" in model.prompt
        assert "parse as numbers" in model.prompt

    def test_samples_only_when_explicitly_enabled(self):
        model = Spy(LLMSettings(send_sample_values=True))
        model.resolve_header("Mtt Glob", PROFILE, ["prime_totale"], {})
        assert "Mohamed Ben Ali" in model.prompt

    def test_period_prompt_carries_only_the_filename(self):
        model = Spy(LLMSettings())
        model.reply = '{"year": 2025, "month": 3, "confidence": 0.9, "reason": "mars"}'
        answer = model.resolve_period("etat T1 mars.xlsx")
        assert answer.value == "2025-03"
        assert "etat T1 mars.xlsx" in model.prompt


class TestAnswerHandling:
    def test_field_outside_the_catalogue_is_rejected(self):
        """A model that invents a field name must not be believed."""
        model = Spy(LLMSettings())
        model.reply = '{"field": "montant_bidon", "confidence": 0.99, "reason": "x"}'
        assert model.resolve_header("X", PROFILE, ["prime_totale"], {}).value is None

    def test_implausible_period_is_rejected(self):
        model = Spy(LLMSettings())
        model.reply = '{"year": 1755, "month": 17, "confidence": 0.9, "reason": "x"}'
        assert model.resolve_period("x.xlsx").value is None

    def test_none_answer_is_respected(self):
        model = Spy(LLMSettings())
        model.reply = '{"field": "none", "confidence": 0.2, "reason": "unclear"}'
        assert model.resolve_header("Zzz", PROFILE, ["prime_totale"], {}).value is None

    def test_unreachable_endpoint_degrades_rather_than_raises(self):
        model = LocalModel(LLMSettings(base_url="http://127.0.0.1:9/v1", timeout_seconds=1))
        assert model.resolve_header("X", PROFILE, ["prime_totale"], {}).value is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"field": "frais", "confidence": 0.8, "reason": "r"}',
        '```json\n{"field": "frais", "confidence": 0.8, "reason": "r"}\n```',
        'Sure: {"field": "frais", "confidence": 0.8, "reason": "r"} hope that helps',
    ],
)
def test_json_survives_fences_and_chatter(raw):
    assert _extract_json(raw)["field"] == "frais"


def test_non_json_reply_yields_nothing():
    assert _extract_json("I cannot answer that.") is None
