"""A ~1GB batch upload must persist to perkeep without the gateway OOMing (LIT-3382).

The `perkeep-batch` deployment points its api_base at an OpenAI-/v1/files adapter in
front of perkeep (see the e2e config and tests/e2e/perkeep_openai), so the batches
API pushes a real gigabyte to a real blob store and it actually gets stored. The
guard uploads ~1GB via /v1/files and asserts three things: the upload succeeds and
the bytes land in perkeep, the gateway's peak anonymous-memory growth stays within
roughly one copy of the file, and the proxy is still healthy afterwards. LIT-3382
made the upload transform several in-memory copies (~a multiple of the file size),
which exceeded the pod's memory and OOM'd it; one pass-through copy is fine, several
is the regression. Anonymous memory is measured rather than total cgroup memory, so
reclaimable page cache from disk I/O doesn't count.

The upload is random bytes so perkeep's content-addressed dedup can't collapse it -
the blob store grows by ~the file size, proving it really stored.

Gated behind E2E_LARGE_BATCH_UPLOAD=1: it writes a ~1GB temp file and pushes it
through, which is slow and disk-heavy. Run with a generous request timeout, e.g.
E2E_REQUEST_TIMEOUT=600.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from batch_client import BatchFilesClient
from e2e_http import unwrap
from memory import DockerCgroupSampler

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("E2E_LARGE_BATCH_UPLOAD") != "1",
        reason="writes and streams a ~1GB file; enable with E2E_LARGE_BATCH_UPLOAD=1",
    ),
]

TARGET_MODEL = "perkeep-batch"
UPLOAD_BYTES = int(os.getenv("E2E_LARGE_BATCH_UPLOAD_BYTES", str(1_000_000_000)))
MAX_GROWTH_RATIO = float(os.getenv("E2E_LARGE_BATCH_MAX_RATIO", "3.0"))
LITELLM_CONTAINER = os.getenv("LITELLM_CONTAINER", "e2e-litellm-1")
PERKEEP_CONTAINER = os.getenv("PERKEEP_CONTAINER", "e2e-perkeep-1")
PERKEEP_BLOBS = "/home/keepy/var/perkeep/blobs"


def _write_random_file(path: str, size: int) -> None:
    remaining = size
    with open(path, "wb") as handle:
        while remaining > 0:
            block = os.urandom(min(8 * 1024 * 1024, remaining))
            handle.write(block)
            remaining -= len(block)


def _perkeep_blob_bytes() -> int:
    result = subprocess.run(
        ["docker", "exec", PERKEEP_CONTAINER, "du", "-sb", PERKEEP_BLOBS],
        capture_output=True,
        text=True,
        timeout=60,
    )
    field = result.stdout.split("\t", 1)[0].strip()
    return int(field) if field.isdigit() else -1


def test_large_batch_upload_persists_to_perkeep_without_oom(
    client: BatchFilesClient, scoped_key: str, tmp_path
) -> None:
    path = str(tmp_path / "big.bin")
    _write_random_file(path, UPLOAD_BYTES)
    sampler = DockerCgroupSampler(container=LITELLM_CONTAINER)

    blobs_before = _perkeep_blob_bytes()
    result, mem = sampler.measure(
        lambda: client.upload_batch_file_from_path(scoped_key, path, model=TARGET_MODEL)
    )
    blobs_after = _perkeep_blob_bytes()

    uploaded = unwrap(result)
    assert uploaded.bytes == UPLOAD_BYTES
    stored = blobs_after - blobs_before
    assert stored >= 0.8 * UPLOAD_BYTES, (
        f"perkeep blob store grew {stored / 1e6:.0f}MB for a "
        f"{UPLOAD_BYTES / 1e6:.0f}MB upload; the file was not stored"
    )

    assert client.proxy_healthy(), "proxy is not healthy after the ~1GB upload (OOM?)"
    ratio = mem.growth_bytes / UPLOAD_BYTES
    assert ratio < MAX_GROWTH_RATIO, (
        f"proxy anonymous memory grew {mem.growth_bytes / 1e6:.0f}MB for a "
        f"{UPLOAD_BYTES / 1e6:.0f}MB upload (ratio {ratio:.2f} >= {MAX_GROWTH_RATIO}); "
        f"the gateway is making multiple in-memory copies of the upload (LIT-3382 OOM)"
    )
