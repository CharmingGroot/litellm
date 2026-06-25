"""Full file lifecycle through the batches API, plus batch-creation edge cases.

The `perkeep-batch` deployment makes perkeep OpenAI-files-compatible (via the adapter
in tests/e2e/perkeep_openai), so the file surface can be driven end to end: create,
retrieve metadata, read content back, delete, and confirm it's gone. The content
round-trip is the strong assertion - the exact bytes uploaded come back, proving the
gateway really stored and served them, not just echoed an id. (List isn't covered:
litellm serves /v1/files list from its own managed-files table, so model-routed
uploads never appear there.)

Batch *creation* is also exercised: litellm can't create batches for every provider,
and the guard pins that an unsupported provider returns a clear error rather than
crashing.
"""

from __future__ import annotations

import uuid

import pytest

from batch_client import BatchFilesClient, gemini_api_key
from e2e_http import is_ok, unwrap
from lifecycle import ResourceManager

pytestmark = pytest.mark.e2e

PERKEEP_MODEL = "perkeep-batch"
GEMINI_MODEL = "gemini-2.5-flash"


def test_file_lifecycle_create_retrieve_content_delete(
    client: BatchFilesClient, scoped_key: str, resources: ResourceManager
) -> None:
    marker = uuid.uuid4().hex
    content = f"batch-lifecycle-content-{marker}\n".encode()

    uploaded = unwrap(
        client.upload_bytes(
            scoped_key, content, model=PERKEEP_MODEL, filename=f"{marker}.jsonl"
        )
    )
    file_id = uploaded.id
    resources.defer(lambda: client.delete_file(file_id))
    assert uploaded.bytes == len(content)
    assert uploaded.status == "processed"

    retrieved = unwrap(client.retrieve_file(scoped_key, file_id))
    assert retrieved.bytes == len(content)

    fetched = client.file_content(scoped_key, file_id)
    assert fetched.status_code == 200
    assert fetched.body == content.decode()

    deleted = unwrap(client.delete_file_keyed(scoped_key, file_id))
    assert deleted.deleted is True

    assert client.file_content(scoped_key, file_id).status_code == 404


def test_retrieve_unknown_file_is_not_a_success(
    client: BatchFilesClient, scoped_key: str
) -> None:
    marker = uuid.uuid4().hex
    content = f"to-be-deleted-{marker}\n".encode()
    uploaded = unwrap(client.upload_bytes(scoped_key, content, model=PERKEEP_MODEL))
    unwrap(client.delete_file_keyed(scoped_key, uploaded.id))

    assert not is_ok(client.retrieve_file(scoped_key, uploaded.id))


def test_create_batch_for_unsupported_provider_returns_clear_error(
    client: BatchFilesClient, scoped_key: str, resources: ResourceManager
) -> None:
    if gemini_api_key() is None:
        pytest.skip("GEMINI_API_KEY not set")

    content = (
        b'{"custom_id":"r1","method":"POST","url":"/v1/chat/completions",'
        b'"body":{"model":"gemini-2.5-flash","messages":[{"role":"user",'
        b'"content":"hi"}],"max_tokens":1}}\n'
    )
    uploaded = client.upload_batch_file(scoped_key, content, target_model=GEMINI_MODEL)
    resources.defer(lambda: client.delete_file(uploaded.id))

    outcome = client.create_batch(
        scoped_key, input_file_id=uploaded.id, model=GEMINI_MODEL
    )
    assert not outcome.ok
    assert "create_batch" in outcome.body or "support" in outcome.body.lower()
