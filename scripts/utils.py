import json
import os
from datetime import datetime, timezone

from tenacity import (
    RetryCallState,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
)


def write_dlq(contact_id, contact_email, failed_step, error_message, retry_count):
    record = {
        "contact_id": str(contact_id),
        "contact_email": str(contact_email),
        "failed_step": str(failed_step),
        "error_message": str(error_message)[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retry_count": retry_count,
    }
    path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "failed_contacts.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)


def _is_hubspot_transient(exc):
    try:
        from hubspot.crm.contacts import ApiException
        if isinstance(exc, ApiException):
            return exc.status == 429 or exc.status >= 500
    except ImportError:
        pass
    return False


def _is_requests_transient(exc):
    try:
        import requests as _r
        if isinstance(exc, _r.exceptions.HTTPError):
            code = exc.response.status_code
            return code == 429 or code >= 500
    except ImportError:
        pass
    return False


def _is_anthropic_transient(exc):
    try:
        import anthropic as _a
        if isinstance(exc, _a.APIStatusError):
            return exc.status_code == 429 or exc.status_code >= 500
    except ImportError:
        pass
    return False


class RetryAfterWait:
    """Interprets Retry-After as seconds (Anthropic / standard HTTP)."""
    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception()
        header_val = None
        if hasattr(exc, "response") and hasattr(exc.response, "headers"):
            header_val = exc.response.headers.get("Retry-After")
        try:
            return max(float(header_val), 0.0) if header_val else 0.0
        except (TypeError, ValueError):
            return 0.0


class HubSpotRetryAfterWait:
    """Interprets Retry-After as milliseconds (HubSpot sends ms, not seconds)."""
    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception()
        header_val = None
        if hasattr(exc, "headers") and exc.headers:
            header_val = exc.headers.get("Retry-After")
        elif hasattr(exc, "response") and hasattr(exc.response, "headers"):
            header_val = exc.response.headers.get("Retry-After")
        try:
            return max(float(header_val) / 1000.0, 0.0) if header_val else 0.0
        except (TypeError, ValueError):
            return 0.0


def _hs_combined_wait(retry_state):
    return max(
        wait_random_exponential(multiplier=1, min=1, max=60)(retry_state),
        HubSpotRetryAfterWait()(retry_state),
    )


def _anthropic_combined_wait(retry_state):
    return max(
        wait_random_exponential(multiplier=1, min=1, max=60)(retry_state),
        RetryAfterWait()(retry_state),
    )


HS_RETRY_KWARGS = dict(
    wait=_hs_combined_wait,
    stop=stop_after_attempt(6) | stop_after_delay(60),
    reraise=True,
)

REQ_RETRY_KWARGS = dict(
    wait=_hs_combined_wait,
    stop=stop_after_attempt(6) | stop_after_delay(60),
    reraise=True,
)

ANTHROPIC_RETRY_KWARGS = dict(
    wait=_anthropic_combined_wait,
    stop=stop_after_attempt(6) | stop_after_delay(60),
    reraise=True,
)


def safe_truncate(text: str, max_chars: int) -> str:
    if not isinstance(text, str):
        return text
    return text[:max_chars] if len(text) > max_chars else text
