import json
import os
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field
from pypdf import PdfReader
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

load_dotenv()


class ServerConfig(BaseModel):
    resume_dir: Path = Field(default=Path("./resumes"))
    processed_dir: Path = Field(default=Path("./processed_resumes"))
    allowed_extensions: tuple[str, ...] = (".pdf", ".txt", ".md", ".docx")
    max_batch_files: int = Field(default=25, ge=1, le=200)
    max_file_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)


def load_config() -> ServerConfig:
    cfg = ServerConfig(
        resume_dir=Path(os.getenv("RESUME_DIR", "./resumes")),
        processed_dir=Path(os.getenv("PROCESSED_DIR", "./processed_resumes")),
        max_batch_files=int(os.getenv("MAX_BATCH_FILES", "25")),
    )
    cfg.resume_dir.mkdir(parents=True, exist_ok=True)
    cfg.processed_dir.mkdir(parents=True, exist_ok=True)
    return cfg


CONFIG = load_config()
mcp = MCPServer("FilesystemResumeServer")


def resolve_safe_path(base_dir: Path, filename: str) -> Path:
    clean_name = Path(filename).name
    if not clean_name or clean_name != filename:
        raise ValueError(f"Invalid filename '{filename}': path separators are not permitted.")
    base_resolved = base_dir.resolve()
    target = (base_resolved / clean_name).resolve()
    if not target.is_relative_to(base_resolved):
        raise ValueError(f"Access denied: '{filename}' escapes allowed directory.")
    if target.suffix.lower() not in CONFIG.allowed_extensions:
        raise ValueError(
            f"Unsupported file extension '{target.suffix}'. Allowed: {CONFIG.allowed_extensions}"
        )
    return target


def _read_resume_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Resume file not found: {path.name}")
    size = path.stat().st_size
    if size > CONFIG.max_file_bytes:
        raise ValueError(
            f"File '{path.name}' is {size} bytes, which is greater than limit {CONFIG.max_file_bytes}."
        )
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        extracted = "\n".join(pages).strip()
        return extracted or f"[Empty or scanned PDF with no extractable text: {path.name}]"
    return f"[Binary {ext} file: {path.name}, size={size} bytes]"


@mcp.resource("resumes://catalog")
def list_resume_catalog() -> str:
    """List all resume files in the monitored resume directory with size and timestamp."""
    entries: list[dict[str, Any]] = []
    for item in sorted(CONFIG.resume_dir.iterdir()):
        if item.is_file() and item.suffix.lower() in CONFIG.allowed_extensions:
            stat = item.stat()
            entries.append(
                {
                    "filename": item.name,
                    "uri": f"resumes://file/{item.name}",
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
    return json.dumps({"count": len(entries), "resumes": entries}, indent=2)


@mcp.resource("resumes://file/{filename}")
def read_resume_resource(filename: str) -> str:
    """Read a single resume file safely by filename."""
    safe_path = resolve_safe_path(CONFIG.resume_dir, filename)
    return _read_resume_text(safe_path)


@mcp.resource("resumes://config/summary")
def read_config_summary() -> str:
    """Expose non-secret server configuration parameters."""
    return json.dumps(
        {
            "resume_dir": str(CONFIG.resume_dir.resolve()),
            "processed_dir": str(CONFIG.processed_dir.resolve()),
            "allowed_extensions": list(CONFIG.allowed_extensions),
            "max_batch_files": CONFIG.max_batch_files,
            "max_file_bytes": CONFIG.max_file_bytes,
        },
        indent=2,
    )


@mcp.tool()
def watch_directory(since_timestamp: str | None = None) -> dict[str, Any]:
    """Scan the resume directory and return new or modified files since since_timestamp (ISO-8601)."""
    cutoff: datetime | None = None
    if since_timestamp:
        try:
            cutoff = datetime.fromisoformat(since_timestamp)
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError(f"Invalid ISO-8601 timestamp '{since_timestamp}': {exc}") from exc

    matched_files: list[dict[str, Any]] = []
    all_files: list[str] = []

    for item in sorted(CONFIG.resume_dir.iterdir()):
        if item.is_file() and item.suffix.lower() in CONFIG.allowed_extensions:
            all_files.append(item.name)
            modified_dt = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
            if cutoff is None or modified_dt > cutoff:
                matched_files.append(
                    {
                        "filename": item.name,
                        "uri": f"resumes://file/{item.name}",
                        "modified_at": modified_dt.isoformat(),
                        "size_bytes": item.stat().st_size,
                    }
                )

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_resumes": len(all_files),
        "new_or_updated_count": len(matched_files),
        "files": matched_files,
    }


@mcp.tool()
def batch_process(filenames: list[str], archive_processed: bool = False) -> dict[str, Any]:
    """Process multiple resume files in a single batch and return per-file results."""
    if not filenames:
        raise ValueError("filenames list must not be empty.")
    if len(filenames) > CONFIG.max_batch_files:
        raise ValueError(
            f"Requested {len(filenames)} files, which is greater than max_batch_files ({CONFIG.max_batch_files})."
        )

    results: list[dict[str, Any]] = []
    for name in filenames:
        try:
            safe_path = resolve_safe_path(CONFIG.resume_dir, name)
            text = _read_resume_text(safe_path)
            archived_to: str | None = None
            if archive_processed:
                dest = CONFIG.processed_dir / safe_path.name
                safe_path.replace(dest)
                archived_to = str(dest.resolve())
            results.append(
                {
                    "filename": safe_path.name,
                    "status": "ok",
                    "char_count": len(text),
                    "word_count": len(text.split()),
                    "content": text,
                    "archived_to": archived_to,
                }
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            results.append(
                {
                    "filename": name,
                    "status": "error",
                    "error_code": "INVALID_OR_MISSING_FILE",
                    "message": str(exc),
                }
            )

    succeeded = sum(1 for r in results if r["status"] == "ok")
    return {
        "processed_total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")

