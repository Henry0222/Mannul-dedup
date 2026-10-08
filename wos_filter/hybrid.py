from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable

from .cache import JsonlAuditLog, ResultCache
from .deepseek import DeepSeekClient, classify_with_cache as classify_with_deepseek_cache
from .jev import (
    CONSERVATIVE_INCLUDE_NOTE,
    JevClient,
    classify_with_cache as classify_with_jev_cache,
)
from .models import Classification, WosRecord


DEFAULT_DEEPSEEK_REVIEW_CONCURRENCY = 8


@dataclass(slots=True)
class HybridClient:
    jev: JevClient
    deepseek: DeepSeekClient
    deepseek_max_concurrency: int = DEFAULT_DEEPSEEK_REVIEW_CONCURRENCY
    _deepseek_slots: threading.BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.deepseek_max_concurrency < 1:
            raise ValueError("DeepSeek 复核并发上限必须至少为 1")
        self._deepseek_slots = threading.BoundedSemaphore(self.deepseek_max_concurrency)

    @property
    def model(self) -> str:
        return f"{self.jev.model} -> {self.deepseek.model}"


def needs_deepseek_review(result: Classification) -> bool:
    """只把 Jev 的真正风险边界交给 DeepSeek，避免重复调用全量记录。"""
    return (
        result.decision == "uncertain"
        or bool(result.error)
        or CONSERVATIVE_INCLUDE_NOTE in result.missing_information
    )


def classify_with_cache(
    record: WosRecord,
    query: str,
    client: HybridClient,
    cache: ResultCache,
    audit_log: JsonlAuditLog,
    on_attempt: Callable[[str], None] | None = None,
) -> Classification:
    jev_origin = ""
    deepseek_origin = ""

    def remember_jev(origin: str) -> None:
        nonlocal jev_origin
        jev_origin = origin

    def remember_deepseek(origin: str) -> None:
        nonlocal deepseek_origin
        deepseek_origin = origin

    jev_result = classify_with_jev_cache(
        record, query, client.jev, cache, audit_log, on_attempt=remember_jev
    )
    review_required = needs_deepseek_review(jev_result)
    deepseek_result: Classification | None = None

    if review_required:
        with client._deepseek_slots:
            deepseek_result = classify_with_deepseek_cache(
                record, query, client.deepseek, cache, audit_log, on_attempt=remember_deepseek
            )
        if not deepseek_result.error:
            result = Classification.from_dict(deepseek_result.to_dict())
            result.record_id = record.record_id
            result.cached = jev_result.cached and deepseek_result.cached
            result.reason = (
                f"混合模式：Jev 结果触发 DeepSeek 复核。"
                f"Jev：{jev_result.reason} DeepSeek：{deepseek_result.reason}"
            )
            result.missing_information = _unique_text(
                jev_result.missing_information + deepseek_result.missing_information
            )
            if jev_result.error:
                result.missing_information = _unique_text(
                    result.missing_information + ["Jev 调用失败，已由 DeepSeek 完成复核"]
                )
            result.error = ""
        else:
            result = Classification.from_dict(jev_result.to_dict())
            result.record_id = record.record_id
            result.cached = False
            result.reason = (
                f"{jev_result.reason} DeepSeek 复核失败，已保留 Jev 的高召回安全结果；"
                f"失败信息：{deepseek_result.error}"
            )
            result.missing_information = _unique_text(
                jev_result.missing_information + ["DeepSeek 复核失败，未覆盖 Jev 结果"]
            )
            errors = []
            if jev_result.error:
                errors.append(f"Jev：{jev_result.error}")
            errors.append(f"DeepSeek：{deepseek_result.error}")
            result.error = "；".join(errors)
    else:
        result = Classification.from_dict(jev_result.to_dict())
        result.record_id = record.record_id

    if on_attempt:
        all_cached = jev_origin == "cache" and (
            not review_required or deepseek_origin == "cache"
        )
        on_attempt("cache" if all_cached else "api")

    audit_log.append({
        "event": "hybrid_resolution",
        "provider": "Jev+DeepSeek",
        "record_id": record.record_id,
        "jev_model": client.jev.model,
        "jev_decision": jev_result.decision,
        "jev_confidence": jev_result.confidence,
        "jev_error": jev_result.error,
        "deepseek_called": review_required,
        "deepseek_model": client.deepseek.model if review_required else "",
        "deepseek_max_concurrency": client.deepseek_max_concurrency,
        "deepseek_decision": deepseek_result.decision if deepseek_result else "",
        "deepseek_confidence": deepseek_result.confidence if deepseek_result else None,
        "deepseek_error": deepseek_result.error if deepseek_result else "",
        "final_ai_decision": result.decision,
        "cache": {
            "jev": jev_origin,
            "deepseek": deepseek_origin,
        },
    })
    return result


def _unique_text(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))
