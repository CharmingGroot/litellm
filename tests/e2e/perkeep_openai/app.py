"""An OpenAI-files-compatible front for perkeep.

litellm's batches/files API speaks the OpenAI files protocol; perkeep is a
content-addressable blob store with its own protocol. This adapter bridges them so
perkeep can be used as a files upstream end to end: create, retrieve, get content,
list, and delete.

Uploads stream to disk (never the whole file in memory, so a gigabyte upload can't
OOM the adapter), get stored via `pk-put file` (content-addressed, chunked), and get
a titled permanode so they show in the perkeep UI. The OpenAI file id is the perkeep
file blobref; content comes back via `pk-get -contents`. A small in-memory registry
holds the OpenAI-facing metadata (size, filename) the protocol needs but perkeep
doesn't track per-file.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

SPOOL_DIR = os.environ.get("SHIM_SPOOL_DIR", "/data")
CHUNK = 1024 * 1024
MAX_PART = 64 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileRecord:
    file_id: str
    filename: str
    bytes: int
    created_at: int
    purpose: str


_FILES: dict[str, FileRecord] = {}  # mutable-ok: adapter-local registry of uploads


def _file_object(record: FileRecord) -> dict[str, object]:
    return {
        "id": record.file_id,
        "object": "file",
        "bytes": record.bytes,
        "created_at": record.created_at,
        "filename": record.filename,
        "purpose": record.purpose,
        "status": "processed",
    }


async def create_file(request: Request) -> JSONResponse:
    size = 0
    filename = "batch.bin"
    purpose = "batch"
    async with request.form(max_part_size=MAX_PART) as form:
        upload = form.get("file")
        raw_purpose = form.get("purpose")
        if isinstance(raw_purpose, str):
            purpose = raw_purpose
        if upload is None or isinstance(upload, str):
            return JSONResponse(
                {"error": {"message": "missing 'file' part"}}, status_code=400
            )
        filename = upload.filename or filename
        with tempfile.NamedTemporaryFile(delete=False, dir=SPOOL_DIR) as spool:
            path = spool.name
            while chunk := await upload.read(CHUNK):
                spool.write(chunk)
                size += len(chunk)

    try:
        put = subprocess.run(
            ["pk-put", "file", path], capture_output=True, text=True, timeout=7200
        )
        file_ref = next(
            (ln.strip() for ln in reversed(put.stdout.splitlines()) if ln.strip()), ""
        )
        if put.returncode != 0 or not file_ref.startswith("sha"):
            return JSONResponse(
                {"error": {"message": f"perkeep ingest failed: {put.stderr[:400]}"}},
                status_code=502,
            )
        permanode = subprocess.run(
            ["pk-put", "permanode"], capture_output=True, text=True, timeout=120
        )
        pref = next(
            (
                ln.strip()
                for ln in reversed(permanode.stdout.splitlines())
                if ln.strip()
            ),
            "",
        )
        if pref.startswith("sha"):
            subprocess.run(
                ["pk-put", "attr", pref, "camliContent", file_ref], timeout=120
            )
            subprocess.run(["pk-put", "attr", pref, "title", filename], timeout=120)
    finally:
        os.unlink(path)

    record = FileRecord(
        file_id=file_ref,
        filename=filename,
        bytes=size,
        created_at=int(time.time()),
        purpose=purpose,
    )
    _FILES[file_ref] = record
    return JSONResponse(_file_object(record))


async def list_files(request: Request) -> JSONResponse:
    return JSONResponse(
        {"object": "list", "data": [_file_object(r) for r in _FILES.values()]}
    )


async def retrieve_file(request: Request) -> JSONResponse:
    record = _FILES.get(request.path_params["file_id"])
    if record is None:
        return JSONResponse({"error": {"message": "file not found"}}, status_code=404)
    return JSONResponse(_file_object(record))


async def file_content(request: Request) -> Response:
    file_id = request.path_params["file_id"]
    if file_id not in _FILES:
        return JSONResponse({"error": {"message": "file not found"}}, status_code=404)
    got = subprocess.run(
        ["pk-get", "-contents", file_id], capture_output=True, timeout=7200
    )
    if got.returncode != 0:
        return JSONResponse(
            {"error": {"message": f"perkeep read failed: {got.stderr[:400]!r}"}},
            status_code=502,
        )
    return Response(content=got.stdout, media_type="application/octet-stream")


async def delete_file(request: Request) -> JSONResponse:
    file_id = request.path_params["file_id"]
    existed = _FILES.pop(file_id, None) is not None
    return JSONResponse({"id": file_id, "object": "file", "deleted": existed})


app = Starlette(
    routes=[
        Route("/v1/files", create_file, methods=["POST"]),
        Route("/files", create_file, methods=["POST"]),
        Route("/v1/files", list_files, methods=["GET"]),
        Route("/files", list_files, methods=["GET"]),
        Route("/v1/files/{file_id}", retrieve_file, methods=["GET"]),
        Route("/files/{file_id}", retrieve_file, methods=["GET"]),
        Route("/v1/files/{file_id}/content", file_content, methods=["GET"]),
        Route("/files/{file_id}/content", file_content, methods=["GET"]),
        Route("/v1/files/{file_id}", delete_file, methods=["DELETE"]),
        Route("/files/{file_id}", delete_file, methods=["DELETE"]),
    ]
)
