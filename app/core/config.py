"""
Central settings loaded from environment variables (Pydantic Settings).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration; defaults match Windows EC2 C: drive layout."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    analysis_backend: Literal["inhouse", "openstaad"] = Field(default="inhouse", validation_alias="ANALYSIS_BACKEND")

    # Paths (also set by bootstrap from same env names)
    temp_dir: Path = Field(default=Path(r"C:\Temp"), validation_alias="TEMP_DIR")
    staad_jobs_dir: Path = Field(default=Path(r"C:\STAADJobs"), validation_alias="STAAD_JOBS_DIR")
    logs_dir: Path = Field(default=Path(r"C:\Logs"), validation_alias="LOGS_DIR")

    # Job lifecycle
    keep_job_folder_on_error: bool = Field(default=False, validation_alias="KEEP_JOB_FOLDER_ON_ERROR")
    keep_anl_file: bool = Field(default=False, validation_alias="KEEP_ANL_FILE")
    job_retention_hours: float = Field(default=6.0, validation_alias="JOB_RETENTION_HOURS")
    completed_job_ttl_seconds: int = Field(default=3600, validation_alias="COMPLETED_JOB_TTL_SECONDS")
    cleanup_retry_seconds: float = Field(default=8.0, validation_alias="CLEANUP_RETRY_SECONDS")
    cleanup_retry_interval_seconds: float = Field(default=0.5, validation_alias="CLEANUP_RETRY_INTERVAL_SECONDS")
    enable_staad_preflight: bool = Field(default=True, validation_alias="ENABLE_STAAD_PREFLIGHT")
    staad_preflight_timeout_seconds: int = Field(default=45, validation_alias="STAAD_PREFLIGHT_TIMEOUT_SECONDS")

    # STAAD / extractor
    staad_timeout_seconds: int = Field(default=180, validation_alias="STAAD_TIMEOUT_SECONDS")
    allow_parallel_staad: bool = Field(default=False, validation_alias="ALLOW_PARALLEL_STAAD")
    staad_worker_count: int = Field(default=1, validation_alias="STAAD_WORKER_COUNT", ge=1)
    staad_launch_wait_seconds: float = Field(default=20.0, validation_alias="STAAD_LAUNCH_WAIT_SECONDS")
    staad_startup_timeout_seconds: int = Field(default=120, validation_alias="STAAD_STARTUP_TIMEOUT_SECONDS")
    staad_attach_timeout_seconds: int = Field(default=90, validation_alias="STAAD_ATTACH_TIMEOUT_SECONDS")
    staad_lock_timeout_seconds: int = Field(default=600, validation_alias="STAAD_LOCK_TIMEOUT_SECONDS")
    keep_staad_warm: bool = Field(default=False, validation_alias="KEEP_STAAD_WARM")
    extractor_timeout_cushion_seconds: int = Field(default=180, validation_alias="EXTRACTOR_TIMEOUT_CUSHION_SECONDS")
    extractor_poll_interval_seconds: float = Field(default=2.0, validation_alias="EXTRACTOR_POLL_INTERVAL_SECONDS")
    extractor_include_combinations: bool = Field(default=True, validation_alias="EXTRACTOR_INCLUDE_COMBINATIONS")

    # Process hygiene (comma-separated override via env)
    staad_process_names: str = Field(
        default="Bentley.Staad.exe,STAADPro.exe,SProStaad.exe",
        validation_alias="STAAD_PROCESS_NAMES",
    )

    # API defaults
    default_timeout_seconds: int = Field(default=180, validation_alias="STAAD_API_TIMEOUT_SECONDS")
    default_max_retries: int = Field(default=2, validation_alias="STAAD_API_MAX_RETRIES")
    default_retry_delay_seconds: int = Field(default=20, validation_alias="STAAD_API_RETRY_DELAY_SECONDS")
    max_upload_size_bytes: int = Field(
        default=50 * 1024 * 1024,
        validation_alias="STAAD_API_MAX_UPLOAD_BYTES",
    )
    max_stderr_excerpt_chars: int = Field(default=2000, validation_alias="STAAD_API_MAX_STDERR_CHARS")

    @field_validator("temp_dir", "staad_jobs_dir", "logs_dir", mode="before")
    @classmethod
    def _coerce_path(cls, value: Path | str) -> Path:
        return Path(value) if not isinstance(value, Path) else value

    def staad_process_name_list(self) -> List[str]:
        """Normalized list of process base names to monitor or terminate."""
        parts = [p.strip() for p in self.staad_process_names.split(",")]
        return [p for p in parts if p]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
