"""generate_campaign.py — Claude API generation for Lane A re-engagement pipeline.

Usage:
    python scripts/generate_campaign.py
"""

import json
import os
import sys
import time

import anthropic
from tenacity import retry

from config.system_prompt import get_system_prompt
from utils import ANTHROPIC_RETRY_KWARGS, write_dlq

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": get_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]
EMAIL_KEYS = ["e1", "e2", "e3", "e4", "e5"]
CALL_KEYS = ["call1", "call2", "pin"]
ALL_KEYS = EMAIL_KEYS + CALL_KEYS


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class MaxTokensError(Exception):
    """Raised when model stops at max_tokens limit."""


class OutputParseError(Exception):
    """Raised when 8-key JSON cannot be parsed from model response."""


# ---------------------------------------------------------------------------
# Parse and write helpers
# ---------------------------------------------------------------------------


def parse_output(response: anthropic.types.Message, contact_id: str) -> dict:
    if response.stop_reason == "max_tokens":
        raise MaxTokensError(f"max_tokens reached for contact {contact_id}")

    raw_text = response.content[0].text

    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raw_path = os.path.join(RUNNER_TEMP, f"raw_response_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(f"JSON decode failed for {contact_id}: {exc}") from exc

    missing = [k for k in ALL_KEYS if k not in parsed]
    if missing:
        raw_path = os.path.join(RUNNER_TEMP, f"raw_response_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(f"Missing top-level keys for {contact_id}: {missing}")

    for key in EMAIL_KEYS:
        for sub in ("subject", "body"):
            if sub not in parsed[key]:
                raise OutputParseError(f"Missing sub-key '{sub}' in {key} for {contact_id}")

    for key in CALL_KEYS:
        if "body" not in parsed[key]:
            raise OutputParseError(f"Missing sub-key 'body' in {key} for {contact_id}")

    return parsed


def write_generated(contact_id: str, parsed: dict, brief: dict) -> None:
    payload = {
        "contact_id": contact_id,
        "e1": parsed["e1"],
        "e2": parsed["e2"],
        "e3": parsed["e3"],
        "e4": parsed["e4"],
        "e5": parsed["e5"],
        "call1": parsed["call1"],
        "call2": parsed["call2"],
        "pin": parsed["pin"],
        "case_study": brief.get("case_study"),
        "close_option": brief.get("close_option"),
        "close_text": brief.get("close_text"),
    }
    out_path = os.path.join(RUNNER_TEMP, f"generated_{contact_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


# ---------------------------------------------------------------------------
# Realtime API call
# ---------------------------------------------------------------------------


@retry(**ANTHROPIC_RETRY_KWARGS)
def _call_realtime(brief_text: str) -> anthropic.types.Message:
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=3000,
        system=SYSTEM_MESSAGE,
        messages=[{"role": "user", "content": brief_text}],
    )


# ---------------------------------------------------------------------------
# Batch API functions
# ---------------------------------------------------------------------------


def submit_batch(contact_ids: list, briefs: dict) -> str:
    requests_list = [
        {
            "custom_id": cid,
            "params": {
                "model": "claude-sonnet-5",
                "max_tokens": 3000,
                "system": SYSTEM_MESSAGE,
                "messages": [{"role": "user", "content": briefs[cid]["brief_text"]}],
            },
        }
        for cid in contact_ids
    ]
    batch = client.messages.batches.create(requests=requests_list)
    print(f"Batch submitted: {batch.id} ({len(requests_list)} requests)")
    return batch.id


def poll_batch(batch_id: str) -> object:
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        print(
            f"Batch status: {batch.processing_status} | "
            f"processing={batch.request_counts.processing} "
            f"succeeded={batch.request_counts.succeeded} "
            f"errored={batch.request_counts.errored}"
        )
        time.sleep(60)
    print(
        f"Batch complete: succeeded={batch.request_counts.succeeded}, "
        f"errored={batch.request_counts.errored}"
    )
    return batch


def process_batch_results(batch_id: str, briefs: dict) -> None:
    for item in client.messages.batches.results(batch_id):
        cid = item.custom_id
        if item.result.type == "succeeded":
            try:
                parsed = parse_output(item.result.message, cid)
                write_generated(cid, parsed, briefs[cid])
            except (MaxTokensError, OutputParseError) as exc:
                write_dlq(cid, "", "parse_output", str(exc), 0)
                print(f"ERROR parse_output {cid}: {exc}", file=sys.stderr)
        else:
            write_dlq(cid, "", "batch_api_error", f"result.type={item.result.type}", 0)
            print(f"ERROR batch result {cid}: result.type={item.result.type}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ids_path = os.path.join(RUNNER_TEMP, "passing_ids.json")
    with open(ids_path) as f:
        contact_ids = json.load(f)

    write_dlq("batch", "", "startup", "sentinel", 0)

    use_batch = os.environ.get("INPUT_USE_BATCH_API", "").lower() == "true"

    if use_batch:
        briefs = {}
        for cid in contact_ids:
            brief_path = os.path.join(RUNNER_TEMP, f"brief_{cid}.json")
            if not os.path.exists(brief_path):
                continue
            with open(brief_path, encoding="utf-8") as f:
                briefs[cid] = json.load(f)
        passing_with_briefs = list(briefs.keys())
        batch_id = submit_batch(passing_with_briefs, briefs)
        poll_batch(batch_id)
        process_batch_results(batch_id, briefs)
    else:
        first_contact = True
        for cid in contact_ids:
            brief_path = os.path.join(RUNNER_TEMP, f"brief_{cid}.json")
            if not os.path.exists(brief_path):
                continue
            with open(brief_path, encoding="utf-8") as f:
                brief = json.load(f)
            try:
                response = _call_realtime(brief["brief_text"])
                parsed = parse_output(response, cid)
                write_generated(cid, parsed, brief)
                if first_contact:
                    usage = response.usage
                    print(
                        f"Cache usage (first contact): "
                        f"cache_creation={usage.cache_creation_input_tokens} "
                        f"cache_read={usage.cache_read_input_tokens}"
                    )
                    first_contact = False
            except (MaxTokensError, OutputParseError) as exc:
                write_dlq(cid, "", "parse_output", str(exc), 0)
                print(f"ERROR parse_output {cid}: {exc}", file=sys.stderr)
            except Exception as exc:
                write_dlq(cid, "", "generate_campaign", str(exc), 0)
                print(f"ERROR generate_campaign {cid}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
