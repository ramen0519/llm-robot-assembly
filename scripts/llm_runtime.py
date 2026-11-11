"""Explicit configuration and per-episode accounting for serial model requests."""
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import tomllib

from failure_report import FailureCode, FailureEvent, utc_now


@dataclass(frozen=True)
class LlmConfig:
    enabled: bool = False
    provider: str = 'openai'
    model: str = ''
    api_key_env: str = 'OPENAI_API_KEY'
    max_model_requests: int = 8
    max_input_tokens: int = 12000
    max_output_tokens: int = 2048
    max_total_tokens: int = 60000
    max_cost_usd: str = '0'
    input_usd_per_million: str = '0'
    output_usd_per_million: str = '0'
    pricing_source: str = ''
    pricing_as_of: str = ''
    timeout_seconds: float = 45.
    reasoning_effort: str = ''
    cached_input_usd_per_million: str = ''
    cache_write_30m_usd_per_million: str = ''

    def request_reserve_usd(self):
        rates = (self.input_usd_per_million, self.cached_input_usd_per_million,
                 self.cache_write_30m_usd_per_million)
        highest_input = max(Decimal(rate) for rate in rates if rate)
        return (self.max_input_tokens * highest_input
                + self.max_output_tokens * Decimal(self.output_usd_per_million)) / Decimal(1000000)

    def validate(self, *, live=False):
        if type(self.enabled) is not bool or self.provider != 'openai':
            raise ValueError('Expected an OpenAI configuration and boolean enabled flag.')
        if not isinstance(self.model, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{0,120}', self.model):
            raise ValueError('Invalid model identifier.')
        if not isinstance(self.api_key_env, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,80}', self.api_key_env):
            raise ValueError('Invalid API key environment-variable name.')
        for name, maximum in (('max_model_requests', 100), ('max_input_tokens', 32000),
                              ('max_output_tokens', 16000), ('max_total_tokens', 1000000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('Invalid token/request limit: ' + name)
        timeout_limit = 180 if self.model == 'gpt-6-astra' and self.reasoning_effort == 'high' else 60
        if (type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds)
                or not 1 <= self.timeout_seconds <= timeout_limit):
            raise ValueError(f'Timeout must be between 1 and {timeout_limit} seconds.')
        if self.reasoning_effort not in ('', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Unknown reasoning effort; model support must be checked separately.')
        for name in ('max_cost_usd', 'input_usd_per_million', 'output_usd_per_million',
                     'cached_input_usd_per_million', 'cache_write_30m_usd_per_million'):
            value = getattr(self, name)
            if name in ('cached_input_usd_per_million', 'cache_write_30m_usd_per_million') and value == '':
                continue
            if not isinstance(value, str):
                raise ValueError('Use quoted decimal strings for prices/budget.')
            number = Decimal(value)
            if not number.is_finite() or number < 0:
                raise ValueError('Invalid price/budget: ' + name)
        if live:
            if not self.enabled or not self.model:
                raise ValueError('Live API needs enabled=true and an explicitly selected model.')
            if any(Decimal(getattr(self, key)) <= 0 for key in
                   ('max_cost_usd', 'input_usd_per_million', 'output_usd_per_million')):
                raise ValueError('Live API requires a positive explicit cost cap and verified pricing.')
            if (not isinstance(self.pricing_source, str) or not self.pricing_source.startswith('https://')
                    or not isinstance(self.pricing_as_of, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', self.pricing_as_of)):
                raise ValueError('Record the pricing source and verification date before live requests.')
        return self

    def public(self):
        return asdict(self)  # Environment-variable NAME only; never the credential.


def load_llm_config(path):
    with Path(path).open('rb') as stream:
        data = tomllib.load(stream)
    if set(data) != {'llm'} or not isinstance(data['llm'], dict):
        raise ValueError('Expected only an [llm] section.')
    return LlmConfig(**data['llm']).validate()


def create_openai_client(config):
    config.validate(live=True)
    key = os.environ.get(config.api_key_env)
    if not key or not key.strip():
        raise ValueError('API key is not set in the configured environment variable; no request sent.')
    from openai import OpenAI
    # Ignore a machine-wide alternate base URL. This adapter is for this service only.
    return OpenAI(api_key=key, base_url='https://api.openai.com/v1',
                  max_retries=0, timeout=config.timeout_seconds)


class ApiLedger:
    """One serial episode. Unknown generation usage stays reserved and stops reuse."""
    def __init__(self, config, event_path):
        self.config = config.validate(live=True)
        self.path = Path(event_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Never append a fresh budget to an earlier episode's journal.
        self.path.touch(exist_ok=False)
        self.http_requests = self.count_requests = self.model_requests = 0
        self.input_tokens = self.output_tokens = self.cached_tokens = self.reasoning_tokens = 0
        self.cache_write_tokens = 0
        self.known_cost = Decimal('0')
        self.reserved_cost = Decimal('0')
        self.reserved_tokens = 0
        self.unknown_requests = 0
        self.halted = False

    def record(self, event, **data):
        row = {'timestamp_utc': utc_now(), 'event': event, **data}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def cost(self, inputs, outputs, *, cached=0, written=0):
        config = self.config
        if written and not config.cache_write_30m_usd_per_million:
            raise ValueError('No configured rate for default 30-minute cache writes.')
        ordinary = inputs - cached - written
        read_rate = config.cached_input_usd_per_million or config.input_usd_per_million
        return (ordinary * Decimal(config.input_usd_per_million) + cached * Decimal(read_rate)
                + written * Decimal(config.cache_write_30m_usd_per_million or '0')
                + outputs * Decimal(config.output_usd_per_million)) / Decimal(1000000)

    def ensure_room(self):
        c = self.config
        tokens = c.max_input_tokens + c.max_output_tokens
        reserve = c.request_reserve_usd()
        if (self.halted or self.model_requests >= c.max_model_requests
                or self.count_requests >= c.max_model_requests
                or self.input_tokens + self.output_tokens + self.reserved_tokens + tokens > c.max_total_tokens
                or self.known_cost + self.reserved_cost + reserve > Decimal(c.max_cost_usd)):
            raise FailureEvent(FailureCode.POLICY_BUDGET_EXCEEDED,
                               'No complete request reservation fits the remaining episode budget.', phase='policy/budget')

    def begin_count(self, request_hash):
        self.ensure_room()
        self.record('input_count_started', request_hash=request_hash)
        self.count_requests += 1
        self.http_requests += 1

    def begin_generation(self, input_count, request_hash):
        if type(input_count) is not int or not 0 <= input_count <= self.config.max_input_tokens:
            self.halted = True
            raise FailureEvent(FailureCode.POLICY_BUDGET_EXCEEDED,
                               'Counted input exceeds the per-request limit.', phase='policy/input_count')
        # The count request already consumed one count slot; ensure_room was called
        # before it. Reserve the full input/output caps, not an optimistic estimate.
        self.reserved_tokens += self.config.max_input_tokens + self.config.max_output_tokens
        self.reserved_cost += self.config.request_reserve_usd()
        self.unknown_requests += 1
        self.record('model_request_started', request_hash=request_hash, input_count=input_count,
                    reserved_cost_usd=str(self.reserved_cost), model=self.config.model)
        self.http_requests += 1
        self.model_requests += 1

    def settle_usage(self, usage):
        if not isinstance(usage, dict):
            raise ValueError('Missing usage.')
        inputs, outputs, total = (usage.get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens'))
        if any(type(v) is not int or v < 0 for v in (inputs, outputs, total)) or total != inputs + outputs:
            raise ValueError('Invalid usage totals.')
        details = usage.get('input_tokens_details', {})
        cached = details.get('cached_tokens', 0)
        written = details.get('cache_write_tokens', 0)
        reasoning = usage.get('output_tokens_details', {}).get('reasoning_tokens', 0)
        if (type(cached) is not int or not 0 <= cached <= inputs or type(reasoning) is not int
                or not 0 <= reasoning <= outputs or type(written) is not int or not 0 <= written <= inputs - cached):
            raise ValueError('Invalid or overlapping usage details.')
        # This adapter requests the default cache lifetime, without breakpoint overrides.
        lifetimes = details.get('cache_write_tokens_details')
        if lifetimes is not None:
            if (not isinstance(lifetimes, dict) or set(lifetimes) != {'ttl_30m_tokens', 'ttl_12h_tokens'}
                    or any(type(value) is not int for value in lifetimes.values())
                    or lifetimes['ttl_30m_tokens'] != written or lifetimes['ttl_12h_tokens'] != 0):
                raise ValueError('Unexpected cache lifetime or inconsistent write totals.')
        if inputs > self.config.max_input_tokens or outputs > self.config.max_output_tokens:
            raise ValueError('Reported usage exceeded the reserved caps.')
        charge = self.cost(inputs, outputs, cached=cached, written=written)
        self.input_tokens += inputs
        self.output_tokens += outputs  # Includes reasoning; do not add it twice.
        self.cached_tokens += cached
        self.cache_write_tokens += written
        self.reasoning_tokens += reasoning
        self.known_cost += charge
        self.reserved_tokens -= self.config.max_input_tokens + self.config.max_output_tokens
        self.reserved_cost -= self.config.request_reserve_usd()
        self.unknown_requests -= 1

    def summary(self):
        unknown = self.unknown_requests > 0
        return {'provider': self.config.provider, 'requested_model': self.config.model,
                'api_calls': self.model_requests, 'http_requests': self.http_requests,
                'input_count_requests': self.count_requests,
                'input_tokens': None if unknown else self.input_tokens,
                'output_tokens': None if unknown else self.output_tokens,
                'known_input_tokens': self.input_tokens, 'known_output_tokens': self.output_tokens,
                'cached_input_tokens': self.cached_tokens, 'reasoning_tokens': self.reasoning_tokens,
                'cache_write_30m_tokens': self.cache_write_tokens,
                'api_cost': None if unknown else float(self.known_cost),
                'known_api_cost_estimate_usd': float(self.known_cost),
                'unresolved_cost_reserve_usd': float(self.reserved_cost),
                'unknown_usage_requests': self.unknown_requests,
                'cost_basis': ('configured_input_cache_and_output_rates'
                               if self.config.cached_input_usd_per_million or self.config.cache_write_30m_usd_per_million
                               else 'configured_uncached_input_and_output_rates_no_discounts'),
                'actual_invoice_verified': False, 'automatic_retries': 0, 'halted': self.halted}
