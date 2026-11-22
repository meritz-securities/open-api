"""Trusted in-process transport binding; never constructed from public arguments."""
import copy
from dataclasses import dataclass, field
import math
import re
from typing import Callable

from .dsl import validate_definition
from .storage import canonical


def metadata(value):
    valid = isinstance(value, dict)
    provider = value.get('provider') if valid else None
    fields = {'provider', 'model', 'timeoutSeconds'}
    fields |= {'connectionId', 'maxOutputTokens'} if provider == 'claude-api' else {'maxBudgetUsd'}
    valid = valid and provider in ('claude-api', 'claude-cli') and set(value) == fields
    if valid:
        valid = isinstance(value['model'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', value['model']) is not None
        seconds = value['timeoutSeconds']
        valid = valid and type(seconds) in (int, float) and math.isfinite(seconds) and 0 < seconds <= 300
        if provider == 'claude-api':
            valid = valid and isinstance(value['connectionId'], str) and re.fullmatch('[0-9a-f]{32}', value['connectionId']) is not None
            valid = valid and type(value['maxOutputTokens']) is int and 1 <= value['maxOutputTokens'] <= 8192
        else:
            budget = value['maxBudgetUsd']
            valid = valid and type(budget) in (int, float) and math.isfinite(budget) and 0 < budget <= 10
    if not valid:
        raise ValueError('AI_GENERATION_METADATA_INVALID')
    return copy.deepcopy(value)


@dataclass(frozen=True)
class GenerationBinding:
    metadata: dict
    send: Callable = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, 'metadata', metadata(self.metadata))
        if not callable(self.send):
            raise ValueError('AI_GENERATION_METADATA_INVALID')


def candidates(value):
    valid = None
    try:
        if (isinstance(value, list) and 1 <= len(value) <= 3 and
                len(canonical(value).encode('utf-8')) <= 240_000 and
                all(isinstance(v, dict) and v.get('kind') == 'dsl' for v in value)):
            valid = [validate_definition(v) for v in value]
            if len(canonical(valid).encode('utf-8')) > 240_000:
                valid = None
    except Exception:
        pass
    if valid is None:
        raise ValueError('PROVIDER_INVALID_OUTPUT')
    return valid


def safe_http_improvement(method):
    """Discard storage/transport exception text and context at the HTTP boundary."""
    from functools import wraps
    allowed = {'AI_GENERATION_PROVIDER_CONFLICT', 'AI_GENERATION_CONSENT_MISMATCH',
               'AI_GENERATION_INTEGRITY_ERROR', 'AI_GENERATION_PREVIEW_REQUIRED',
               'AI_GENERATION_METADATA_INVALID', 'AI_CONNECTION_REQUIRED',
               'AI_CONNECTION_CHANGED', 'AI_CONNECTION_BUSY', 'PROVIDER_PLATFORM_UNSUPPORTED',
               'PROVIDER_INVALID_PROMPT', 'CANCEL_STATE_UNAVAILABLE',
               'Another AI generation is active', 'Another local run is active',
               'DSL 기준 실행만 지원합니다.'}
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except TypeError:
            code = 'AI_GENERATION_ARGUMENT_INVALID'
        except Exception as error:
            message = str(error)
            code = message if message in allowed else 'AI_GENERATION_FAILED'
        raise ValueError(code)
    return call
