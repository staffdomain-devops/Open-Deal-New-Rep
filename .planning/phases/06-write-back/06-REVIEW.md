---
phase: 06-write-back
reviewed: 2026-08-27T00:00:00Z
depth: standard
files_reviewed: 1
files_reviewed_list:
  - scripts/write_hubspot.py
findings:
  critical: 3
  warning: 4
  info: 1
  total: 8
status: fixed
---

# Phase 06: Code Review Report

**Reviewed:** 2026-08-27
**Depth:** standard
**Files Reviewed:** 1
**Status:** issues_found

## Summary

`write_hubspot.py` is the final write-back script that reads assembled contact
JSON files, validates them, batch-writes 12 HubSpot contact properties, creates
pinned notes, and routes failures to a dead-letter queue.

The script's core flow is sound, but it contains three blockers: the DLQ utility
it depends on (`utils.write_dlq`) is destructive rather than append-safe,
meaning all but the last failure record are silently lost; `assembled` can be
unbound when referenced in the `except` clause; and note creation is decoupled
from the batch flush in a way that guarantees note/property desynchronisation
under batch errors. Four additional warnings cover robustness gaps.

---

## Critical Issues

### CR-01: `write_dlq` overwrites the DLQ file on every call — all prior records are lost

**File:** `scripts/utils.py:24`

**Issue:** `write_dlq` opens `failed_contacts.json` with mode `"w"`, which
truncates the file before writing. Every subsequent DLQ write replaces the
previous record. A run with 10 failed contacts will exit with a DLQ file
containing only the last failure. The sentinel written at startup (line 198 of
`write_hubspot.py`) is also immediately overwritten by the first real failure.
This is data loss — the pipeline loses its audit trail of which contacts failed
and why.

**Fix:** Open in read-modify-write mode (same pattern `_pin_note` uses
correctly) or open with append and write newline-delimited JSON:

```python
# In utils.py — replace the open() call:
path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "failed_contacts.json")
try:
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        records = [records]  # migrate old single-record files
except (FileNotFoundError, json.JSONDecodeError):
    records = []
records.append(record)
with open(path, "w", encoding="utf-8") as f:
    json.dump(records, f, indent=2)
```

---

### CR-02: `assembled` referenced before assignment in `except` clause — `UnboundLocalError` crash

**File:** `scripts/write_hubspot.py:235`

**Issue:** The `except Exception` block at line 234 calls
`assembled.get("contact_id", str(cid))`. The variable `assembled` is first
assigned inside the `try` block at line 220 (`assembled = json.load(f)`). If
`open()` raises (unlikely but possible — race condition, permission error) or
`json.load()` raises a `json.JSONDecodeError` (malformed file), then `assembled`
is never assigned. Executing the `except` clause then raises `UnboundLocalError:
local variable 'assembled' referenced before assignment`, which propagates
uncaught and aborts the entire loop — all remaining contacts are skipped.

**Fix:** Initialise `assembled` to `None` before the `try` block and guard the
`get` call:

```python
assembled = None
try:
    with open(assembled_path, encoding="utf-8") as f:
        assembled = json.load(f)
    ...
except Exception as exc:
    contact_email = assembled.get("contact_id", str(cid)) if assembled else str(cid)
    write_dlq(cid, contact_email, "write_properties_or_note", str(exc), 0)
    ...
```

---

### CR-03: Note created per-contact before batch flush — properties and note are decoupled

**File:** `scripts/write_hubspot.py:231-232`

**Issue:** Inside the per-contact loop, `_create_note` (line 231) is called
immediately after appending to `pending_batch`, before the batch is flushed to
HubSpot. The batch is only flushed when it reaches `BATCH_SIZE=100` (line
226-229) or at the end of the loop (line 241-242). This means:

1. A contact's pinned note is created in HubSpot **before** its 12 email
   properties are written.
2. If the batch flush subsequently fails (e.g., network error, HubSpot 5xx after
   all retries exhausted), the note exists but the properties do not. The contact
   is in a permanently inconsistent state with no record of which contacts are
   affected, because the failed contacts in `pending_batch` were already
   individually succeeded in the note step.
3. The `except` block wrapping `_create_note` also covers `_write_properties_batch`
   failures (when the batch hits 100), so a batch flush failure mid-loop catches
   under the wrong contact's `cid`.

**Fix:** Create the note only after the properties for that contact are
confirmed written. The simplest approach is to use a batch size of 1 (flush per
contact) or to separate the note creation into a second pass after all batches
are flushed. A second-pass approach:

```python
# First pass: build all batch inputs and flush
for cid in contact_ids:
    ...
    pending_batch.append(input_dict)
    if len(pending_batch) == BATCH_SIZE:
        _write_properties_batch(client, pending_batch)
        written_ids.extend(b["id"] for b in pending_batch)
        pending_batch = []

if pending_batch:
    _write_properties_batch(client, pending_batch)
    written_ids.extend(b["id"] for b in pending_batch)

# Second pass: create and pin notes only for contacts whose properties succeeded
for cid in written_ids:
    assembled = assembled_cache[cid]
    note_id = _create_note(client, str(cid), assembled["pin"]["body"])
    _pin_note(str(cid), note_id, client)
```

---

## Warnings

### WR-01: Batch flush in Step 7 is outside `try/except` — errors are not DLQ'd and crash the process

**File:** `scripts/write_hubspot.py:241-243`

**Issue:** The final flush of `pending_batch` (lines 241-243) is outside the
per-contact `try/except` block. If `_write_properties_batch` raises after all
retries, the exception propagates to the top of `main()` with no DLQ entry, no
error count increment, and no summary line printed. The caller (GitHub Actions)
sees a non-zero exit code but the DLQ has no record of which contacts failed.

**Fix:** Wrap the Step 7 flush in its own error handler:

```python
if pending_batch:
    try:
        _write_properties_batch(client, pending_batch)
        written_count += len(pending_batch)
    except Exception as exc:
        for d in pending_batch:
            write_dlq(d["id"], "", "flush_batch", str(exc), 0)
        error_count += len(pending_batch)
        print(f"ERROR during final batch flush: {exc}", file=sys.stderr)
```

---

### WR-02: `_bracket_guard` raises `KeyError` on malformed assembled JSON — wrong exception type propagates

**File:** `scripts/write_hubspot.py:92`

**Issue:** `_bracket_guard` accesses `assembled[top_key][field]` with no
existence check. If the assembled JSON is missing a top-level key (e.g., `"e1"`
absent) or a nested key (e.g., `"subject"` absent), a `KeyError` is raised
rather than `ValueError`. The outer `except Exception` in `main()` catches it,
but the DLQ record says `"write_properties_or_note"` rather than
`"bracket_guard"`, making diagnosis harder. The same `KeyError` would silently
skip the guard for any key whose value happens to be missing. More critically, if
the assembled file is structurally valid for `_bracket_guard` but missing a key
that `_build_batch_input` accesses, the contact is DLQ'd without explanation.

**Fix:** Add explicit key presence validation before accessing nested values, and
raise `ValueError` with a clear message:

```python
def _bracket_guard(assembled: dict) -> None:
    for prop_name, (top_key, field) in PROPERTY_MAP:
        if prop_name not in EMAIL_PROP_NAMES:
            continue
        section = assembled.get(top_key)
        if section is None:
            raise ValueError(f"Bracket guard: missing top-level key '{top_key}' in assembled JSON")
        value = section.get(field)
        if value is None:
            raise ValueError(f"Bracket guard: missing field '{field}' under '{top_key}'")
        if "[" in value:
            raise ValueError(
                f"Bracket guard: unresolved placeholder in '{prop_name}' for contact "
                f"{assembled.get('contact_id')}. Value starts: {value[:80]!r}"
            )
```

---

### WR-03: `_create_note` association call is not idempotent on retry — orphan notes accumulate

**File:** `scripts/write_hubspot.py:147-153`

**Issue:** `_create_note` is decorated with `@retry`. The function body creates
the note object first (line 142-144), then associates it (line 147-152). If the
`associations_api.create` call fails and tenacity retries the entire function,
a second (and third, etc.) note object is created in HubSpot before another
association attempt. After `stop_after_attempt(6)`, up to 6 orphaned note
objects may exist in HubSpot for a single contact. These accumulate silently with
no cleanup mechanism.

**Fix:** Split the function into two non-retried steps — create then associate —
and retry only the association, or check for an existing note ID before creating:

```python
@retry(**HS_RETRY_KWARGS)
def _create_note_object(client, body: str) -> str:
    note_input = SimplePublicObjectInputForCreate(
        properties={
            "hs_note_body": body,
            "hs_timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    response = client.crm.objects.notes.basic_api.create(
        simple_public_object_input_for_create=note_input
    )
    return str(response.id)

@retry(**HS_RETRY_KWARGS)
def _associate_note(client, note_id: str, contact_id: str) -> None:
    client.crm.objects.notes.associations_api.create(
        note_id=note_id,
        to_object_type="contacts",
        to_object_id=contact_id,
        association_type="note_to_contact",
    )
```

---

### WR-04: `assembled["pin"]["body"]` accessed without guard — `KeyError` bypasses DLQ step label

**File:** `scripts/write_hubspot.py:231`

**Issue:** `assembled["pin"]["body"]` uses bare dict access. If the assembled
JSON for a contact is missing the `"pin"` key or its nested `"body"` key, a
`KeyError` is raised. This is caught by the outer `except`, but (a) the DLQ
step label is `"write_properties_or_note"` which obscures the actual cause, and
(b) the contact's properties may already be queued in `pending_batch` —
meaning the properties batch still fires but the note never does, leaving the
contact with properties but no pinned note and no DLQ indication of why.

**Fix:** Validate the `"pin"` key during `_bracket_guard` or at the start of the
`try` block, before appending to `pending_batch`:

```python
# Validate presence before touching pending_batch
pin_body = assembled.get("pin", {}).get("body")
if not pin_body:
    raise ValueError(f"Missing 'pin.body' in assembled JSON for contact {cid}")

_bracket_guard(assembled)
input_dict = _build_batch_input(str(cid), assembled)
pending_batch.append(input_dict)
...
note_id = _create_note(client, str(cid), pin_body)
```

---

## Info

### IN-01: `lint_passing_ids.json` opened without explicit encoding

**File:** `scripts/write_hubspot.py:194`

**Issue:** `open(ids_path)` at line 194 omits `encoding="utf-8"`. All other
`open()` calls in the file specify `encoding="utf-8"` explicitly. On Windows
runners the default encoding may be `cp1252`, which could silently misinterpret
non-ASCII contact IDs. Inconsistency also makes the omission look accidental.

**Fix:**
```python
with open(ids_path, encoding="utf-8") as f:
    contact_ids = json.load(f)
```

---

_Reviewed: 2026-08-27_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
