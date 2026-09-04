"""Provider-neutral structured generation, and the DeepSeek adapter.

The adapter is exercised against an injected httpx transport rather than the
network, so these are ordinary unit tests. The live provider has its own
opt-in smoke test.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from sabermetrics.cedh.cost_ledger import CostLedger, estimate_cost
from sabermetrics.cedh.errors import (
    ModelConfigurationError,
    ModelProviderError,
    ModelValidationError,
)
from sabermetrics.cedh.gateway_fixture import FixtureGateway
from sabermetrics.cedh.model_gateway import (
    ModelGateway,
    ReasoningMode,
    StructuredRequest,
    json_schema_for,
)
from sabermetrics.cedh.responses import IntentClassification
from sabermetrics.cedh.settings import ModelPricing, load_cedh_settings


class Answer(BaseModel):
    verdict: str
    count: int
    optional_note: str | None = None


def _request(**overrides) -> StructuredRequest:
    kwargs = {
        "prompt_version": "v1",
        "system": "system",
        "user": "user",
        "schema": Answer,
        "call_type": "cedh:test",
    }
    kwargs.update(overrides)
    return StructuredRequest(**kwargs)


class TestSchemaTightening:
    def test_every_object_is_closed(self):
        schema = json_schema_for(Answer)
        assert schema["additionalProperties"] is False

    def test_optional_fields_are_still_required_for_strict_mode(self):
        """Strict structured output rejects a schema whose optionals are absent.

        Pydantic leaves defaulted fields out of `required`; a provider in strict
        mode then refuses the schema outright, which surfaces as an opaque 400.
        """
        schema = json_schema_for(Answer)
        assert set(schema["required"]) == {"verdict", "count", "optional_note"}

    def test_nested_objects_are_tightened(self):
        class Outer(BaseModel):
            inner: Answer

        schema = json_schema_for(Outer)
        nested = schema["$defs"]["Answer"]
        assert nested["additionalProperties"] is False


class TestReasoningModes:
    def test_elevated_reasoning_requires_a_stated_ambiguity(self):
        """A mode that costs more has to say what bought it."""
        with pytest.raises(ValueError, match="ambiguity_note"):
            _request(mode=ReasoningMode.ADJUDICATE)

    def test_elevated_reasoning_is_allowed_with_a_note(self):
        request = _request(
            mode=ReasoningMode.ADJUDICATE,
            ambiguity_note="two packs match this commander equally",
        )
        assert request.mode is ReasoningMode.ADJUDICATE

    def test_classify_and_explain_need_no_note(self):
        assert _request(mode=ReasoningMode.CLASSIFY).mode is ReasoningMode.CLASSIFY
        assert _request(mode=ReasoningMode.EXPLAIN).mode is ReasoningMode.EXPLAIN

    def test_modes_are_configured_not_hardcoded(self):
        """The vendor's parameter name for 'think less' must live in config."""
        settings = load_cedh_settings()
        for mode in ReasoningMode:
            assert settings.model.mode_profile(mode).extra_body


class TestCacheKey:
    def test_prompt_version_changes_the_key(self):
        a = _request(prompt_version="v1").cache_key
        b = _request(prompt_version="v2").cache_key
        assert a != b

    def test_evidence_hash_changes_the_key(self):
        a = _request(evidence_hash="aaa").cache_key
        b = _request(evidence_hash="bbb").cache_key
        assert a != b

    def test_identical_requests_share_a_key(self):
        assert _request().cache_key == _request().cache_key


class TestCostEstimation:
    def test_cached_input_is_a_subset_of_input(self):
        pricing = ModelPricing(input=1.0, cached_input=0.1, output=2.0)
        cost = estimate_cost(
            pricing,
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
            reasoning_tokens=0,
        )
        assert cost == pytest.approx(0.1)

    def test_reasoning_defaults_to_the_output_rate(self):
        pricing = ModelPricing(input=0.0, cached_input=0.0, output=3.0)
        assert estimate_cost(
            pricing,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=1_000_000,
        ) == pytest.approx(3.0)

    def test_reasoning_can_be_priced_separately(self):
        pricing = ModelPricing(input=0.0, cached_input=0.0, output=3.0, reasoning=9.0)
        assert estimate_cost(
            pricing,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=1_000_000,
        ) == pytest.approx(9.0)

    def test_a_provider_reporting_disjoint_counts_cannot_go_negative(self):
        pricing = ModelPricing(input=1.0, cached_input=0.1, output=1.0)
        cost = estimate_cost(
            pricing,
            input_tokens=0,
            cached_input_tokens=1_000_000,
            output_tokens=0,
            reasoning_tokens=0,
        )
        assert cost >= 0


class TestFixtureGateway:
    def test_satisfies_the_protocol(self):
        assert isinstance(FixtureGateway(), ModelGateway)

    def test_scripted_answers_are_validated_against_the_real_schema(self):
        """A fake that returns anything lets a test pass the provider would fail."""
        from pydantic import ValidationError

        gateway = FixtureGateway({"cedh:test": {"verdict": "ok"}})
        with pytest.raises(ValidationError):
            gateway.generate(_request())

    def test_records_usage(self):
        gateway = FixtureGateway(
            {"cedh:test": {"verdict": "ok", "count": 1, "optional_note": None}}
        )
        result = gateway.generate(_request())
        assert result.value.verdict == "ok"
        assert result.usage.cost_usd > 0
        assert result.usage.prompt_version == "v1"

    def test_missing_script_is_loud(self):
        with pytest.raises(KeyError, match="cedh:test"):
            FixtureGateway().generate(_request())


class _Stub:
    """A recording httpx transport that replays scripted responses."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]


def _ok(content: str, **usage) -> httpx.Response:
    body = {
        "id": "req-1",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, **usage},
    }
    return httpx.Response(200, json=body)


def _gateway(stub: _Stub, monkeypatch, tmp_path, **model_overrides):
    monkeypatch.setenv("HF_TOKEN", "test-token")
    from sabermetrics.cedh.provider_deepseek import DeepSeekGateway

    settings = load_cedh_settings()
    if model_overrides:
        settings = settings.model_copy(
            update={"model": settings.model.model_copy(update=model_overrides)}
        )
    client = httpx.Client(
        base_url="https://example.invalid/v1",
        transport=httpx.MockTransport(stub),
    )
    return DeepSeekGateway(settings, db_path=str(tmp_path / "absent.db"), client=client)


class TestDeepSeekAdapter:
    def test_valid_response_is_returned_with_usage(self, monkeypatch, tmp_path):
        stub = _Stub([_ok('{"verdict": "yes", "count": 3, "optional_note": null}')])
        gateway = _gateway(stub, monkeypatch, tmp_path)
        result = gateway.generate(_request())
        assert result.value.count == 3
        assert result.usage.input_tokens == 100
        assert result.usage.provider == "deepinfra"
        assert result.usage.cost_usd > 0

    def test_structured_output_is_demanded_on_the_wire(self, monkeypatch, tmp_path):
        stub = _Stub([_ok('{"verdict": "y", "count": 1, "optional_note": null}')])
        _gateway(stub, monkeypatch, tmp_path).generate(_request())
        body = stub.requests[0]
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True

    def test_reasoning_mode_reaches_the_provider_body(self, monkeypatch, tmp_path):
        stub = _Stub([_ok('{"verdict": "y", "count": 1, "optional_note": null}')])
        _gateway(stub, monkeypatch, tmp_path).generate(
            _request(mode=ReasoningMode.CLASSIFY)
        )
        assert stub.requests[0]["reasoning_effort"] == "none"

    def test_reasoning_tokens_are_split_out_not_double_counted(
        self, monkeypatch, tmp_path
    ):
        """completion_tokens includes reasoning tokens in the OpenAI schema."""
        stub = _Stub(
            [
                _ok(
                    '{"verdict": "y", "count": 1, "optional_note": null}',
                    completion_tokens_details={"reasoning_tokens": 30},
                )
            ]
        )
        usage = _gateway(stub, monkeypatch, tmp_path).generate(_request()).usage
        assert usage.reasoning_tokens == 30
        assert usage.output_tokens == 20

    def test_cached_prompt_tokens_are_recorded(self, monkeypatch, tmp_path):
        stub = _Stub(
            [
                _ok(
                    '{"verdict": "y", "count": 1, "optional_note": null}',
                    prompt_tokens_details={"cached_tokens": 60},
                )
            ]
        )
        usage = _gateway(stub, monkeypatch, tmp_path).generate(_request()).usage
        assert usage.cached_input_tokens == 60

    def test_a_code_fence_is_tolerated(self, monkeypatch, tmp_path):
        stub = _Stub(
            [_ok('```json\n{"verdict": "y", "count": 1, "optional_note": null}\n```')]
        )
        result = _gateway(stub, monkeypatch, tmp_path).generate(_request())
        assert result.value.verdict == "y"

    def test_invalid_output_is_repaired_once_and_counted(self, monkeypatch, tmp_path):
        stub = _Stub(
            [
                _ok("not json at all"),
                _ok('{"verdict": "y", "count": 2, "optional_note": null}'),
            ]
        )
        result = _gateway(stub, monkeypatch, tmp_path).generate(_request())
        assert result.value.count == 2
        assert result.usage.validation_failures == 1
        # The repair turn carries the original answer and the errors.
        assert len(stub.requests[1]["messages"]) == 4

    def test_output_that_never_validates_raises_and_is_not_repaired(
        self, monkeypatch, tmp_path
    ):
        """A malformed machine-used response is never silently accepted."""
        stub = _Stub([_ok("still not json")])
        with pytest.raises(ModelValidationError, match="never validated"):
            _gateway(stub, monkeypatch, tmp_path).generate(_request())

    def test_transient_status_is_retried(self, monkeypatch, tmp_path):
        stub = _Stub(
            [
                httpx.Response(503, text="upstream busy"),
                _ok('{"verdict": "y", "count": 1, "optional_note": null}'),
            ]
        )
        monkeypatch.setattr("time.sleep", lambda _s: None)
        result = _gateway(stub, monkeypatch, tmp_path).generate(_request())
        assert result.usage.retries == 1

    def test_permanent_status_is_not_retried(self, monkeypatch, tmp_path):
        stub = _Stub([httpx.Response(400, text="bad request")])
        with pytest.raises(ModelProviderError, match="400"):
            _gateway(stub, monkeypatch, tmp_path).generate(_request())
        assert len(stub.requests) == 1

    def test_missing_credential_is_a_configuration_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        from sabermetrics.cedh.provider_deepseek import DeepSeekGateway

        with pytest.raises(ModelConfigurationError, match="HF_TOKEN"):
            DeepSeekGateway(load_cedh_settings())

    @pytest.mark.parametrize(
        "model_id",
        ["deepseek-ai/DeepSeek-V4-Flash:cheapest", "deepseek-ai/DeepSeek-V4-Flash"],
    )
    def test_unpinned_provider_is_refused(self, monkeypatch, tmp_path, model_id):
        """A floating route makes the recorded model id a guess."""
        stub = _Stub([])
        with pytest.raises(ModelConfigurationError):
            _gateway(stub, monkeypatch, tmp_path, model_id=model_id)

    def test_the_shipped_default_is_pinned(self):
        assert load_cedh_settings().model.model_id.endswith(":deepinfra")


class TestCostCeiling:
    def test_the_ceiling_is_shared_with_the_legacy_path(self, tmp_path):
        """One ceiling across providers, or it is two soft limits."""
        from sabermetrics.config import settings as legacy

        assert (
            load_cedh_settings().monthly_cost_ceiling_usd
            == legacy.llm.monthly_cost_ceiling_usd
        )

    def test_gateway_refuses_once_the_ceiling_is_reached(self, monkeypatch, tmp_path):
        import sqlite3
        import sys

        sys.path.insert(0, ".")
        from sabermetrics.errors import LLMCostCeilingExceeded
        from scripts.setup_db import setup_database

        db_path = tmp_path / "spend.db"
        setup_database(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO cost_log (call_type, model, cost_usd) VALUES (?,?,?)",
            ("cedh:test", "m", 10_000.0),
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("HF_TOKEN", "t")
        from sabermetrics.cedh.provider_deepseek import DeepSeekGateway

        stub = _Stub([_ok('{"verdict":"y","count":1,"optional_note":null}')])
        gateway = DeepSeekGateway(
            load_cedh_settings(),
            db_path=str(db_path),
            client=httpx.Client(
                base_url="https://example.invalid/v1",
                transport=httpx.MockTransport(stub),
            ),
        )
        with pytest.raises(LLMCostCeilingExceeded):
            gateway.generate(_request())
        assert stub.requests == []

    def test_usage_rows_land_in_the_shared_cost_log(self, tmp_path, monkeypatch):
        import sqlite3
        import sys

        sys.path.insert(0, ".")
        from scripts.setup_db import setup_database

        db_path = tmp_path / "ledger.db"
        setup_database(db_path)
        monkeypatch.setenv("HF_TOKEN", "t")
        from sabermetrics.cedh.provider_deepseek import DeepSeekGateway

        stub = _Stub([_ok('{"verdict":"y","count":1,"optional_note":null}')])
        DeepSeekGateway(
            load_cedh_settings(),
            db_path=str(db_path),
            client=httpx.Client(
                base_url="https://example.invalid/v1",
                transport=httpx.MockTransport(stub),
            ),
            user_id="u1",
        ).generate(_request())

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT call_type, model, user_id, metadata FROM cost_log"
        ).fetchone()
        conn.close()
        assert row[0] == "cedh:test"
        assert row[2] == "u1"
        metadata = json.loads(row[3])
        assert metadata["provider"] == "deepinfra"
        assert metadata["prompt_version"] == "v1"
        assert "latency_ms" in metadata

    def test_a_missing_database_never_fails_a_build(self, tmp_path):
        """Accounting must not convert a bookkeeping problem into an outage."""
        from sabermetrics.cedh.model_gateway import UsageRecord

        ledger = CostLedger(tmp_path / "nope.db")
        ledger.record(
            UsageRecord(
                model_id="m",
                provider="p",
                prompt_version="v",
                call_type="c",
                mode="explain",
            )
        )
        assert ledger.monthly_spend() == 0.0


def test_intent_response_forbids_extra_fields():
    """The model cannot smuggle a field the caller does not know about."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IntentClassification.model_validate(
            {"pack_id": "x", "cards_to_add": ["Sol Ring"]}
        )
