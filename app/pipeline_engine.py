"""Dynamic pipeline engine.

Reads the pipeline configuration from the service registry and executes
each step sequentially, resolving ``$sensor.*`` and ``$<service>.*``
input-map references to actual data from previous steps.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

from models.manifest import ServiceManifest
from models.registry import PipelineStep
from pipeline_executor import ServiceCallError, call_service
from registry import ServiceRegistry

logger = logging.getLogger(__name__)

AUDIO_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))) / "audio"


class PipelineContext:
    """Carries data between pipeline steps."""

    def __init__(
        self,
        sensor_data: list[dict[str, float]],
        sensor_timestamp: datetime,
    ) -> None:
        self.sensor_data = sensor_data
        self.sensor_timestamp = sensor_timestamp
        self.results: dict[str, dict[str, Any]] = {}
        self.errors: dict[str, str] = {}
        self.skipped: list[str] = []
        self.halted: bool = False

    def resolve_ref(self, ref: str) -> Any:
        """Resolve a ``$source.key`` reference against the context.

        Supported reference forms:
          - ``$sensor.data``       → raw sensor data list
          - ``$sensor.timestamp``  → ISO-formatted sensor timestamp
          - ``$<service>.<key>``   → output from a previous pipeline step
        """
        if not ref.startswith("$"):
            return ref

        parts = ref.lstrip("$").split(".", 1)
        if len(parts) != 2:
            return None

        source, key = parts

        if source == "sensor":
            if key == "data":
                return self.sensor_data
            if key == "timestamp":
                return self.sensor_timestamp.isoformat()
            return None

        step_result = self.results.get(source)
        if step_result is None:
            return None
        return step_result.get(key)


class PipelineEngine:
    """Executes a dynamic pipeline defined in the service registry."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        sensor_data: list[dict[str, float]],
        sensor_timestamp: datetime,
    ) -> PipelineContext:
        """Run all enabled pipeline steps sequentially.

        Returns the populated context with results, errors, and skipped steps.
        """
        ctx = PipelineContext(sensor_data, sensor_timestamp)

        for step in self._registry.get_pipeline():
            if not step.enabled:
                ctx.skipped.append(step.service)
                continue
            if ctx.halted:
                ctx.skipped.append(step.service)
                continue

            await self._execute_step(step, ctx)

        return ctx

    async def execute_streaming(
        self,
        sensor_data: list[dict[str, float]],
        sensor_timestamp: datetime,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run all enabled pipeline steps, yielding SSE-ready event dicts.

        Events yielded per step:
          ``step_start``    — ``{"service": name}``
          ``step_complete`` — ``{"service": name, "result": ...}``
          ``step_error``    — ``{"service": name, "error": ...}``
          ``step_skip``     — ``{"service": name, "reason": ...}``

        Also emits legacy event names for known services to maintain
        backward compatibility with the kiosk UI:
          ``classify``, ``text``, ``audio``

        Final event:
          ``pipeline_complete`` — full result summary
        """
        ctx = PipelineContext(sensor_data, sensor_timestamp)

        for step in self._registry.get_pipeline():
            if not step.enabled:
                ctx.skipped.append(step.service)
                yield {"event": "step_skip", "data": {"service": step.service, "reason": "disabled"}}
                continue
            if ctx.halted:
                ctx.skipped.append(step.service)
                yield {"event": "step_skip", "data": {"service": step.service, "reason": "halted"}}
                continue

            yield {"event": "step_start", "data": {"service": step.service}}
            yield {"event": "status", "data": {"message": f"Running {step.service}…"}}

            error = await self._execute_step(step, ctx)

            if error:
                yield {"event": "step_error", "data": {"service": step.service, "error": error}}
            else:
                step_result = ctx.results.get(step.service, {})
                yield {"event": "step_complete", "data": {"service": step.service, "result": step_result}}

                # Emit legacy events for backward compatibility
                for legacy_event in self._legacy_events(step.service, step_result, ctx):
                    yield legacy_event

        # Final summary event
        yield {"event": "pipeline_complete", "data": self._build_summary(ctx)}

    # ── Private helpers ──────────────────────────────────────────

    async def _execute_step(
        self,
        step: PipelineStep,
        ctx: PipelineContext,
    ) -> str | None:
        """Execute a single step, handling retries and failure policies.

        Returns None on success, or an error message string on failure.
        """
        reg = self._registry.get(step.service)
        if not reg or not reg.manifest:
            reason = f"Service '{step.service}' not registered or has no manifest"
            self._apply_failure(step, ctx, reason)
            return reason

        if not reg.enabled:
            ctx.skipped.append(step.service)
            return None

        # Resolve input_map to actual values
        payload = self._resolve_inputs(step, ctx)
        if payload is None:
            reason = f"Missing required inputs for '{step.service}'"
            self._apply_failure(step, ctx, reason)
            return reason

        manifest = reg.manifest
        ep = manifest.endpoints.execute
        max_attempts = step.retry_count if step.on_failure == "retry" else 1

        timeout_kwargs = {"timeout": step.timeout} if step.timeout is not None else {}

        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                result = await call_service(
                    endpoint=reg.endpoint,
                    method=ep.method,
                    path=ep.path,
                    payload=payload,
                    **timeout_kwargs,
                )

                # Handle binary responses (e.g. TTS audio)
                if isinstance(result, bytes):
                    result = self._handle_binary(step.service, result, manifest)

                ctx.results[step.service] = result
                logger.info("Step '%s' completed (attempt %d)", step.service, attempt)
                return None

            except ServiceCallError as exc:
                last_error = exc.message
                logger.warning(
                    "Step '%s' attempt %d/%d failed: %s",
                    step.service, attempt, max_attempts, exc.message,
                )

        # All attempts exhausted
        self._apply_failure(step, ctx, last_error)
        return last_error

    def _resolve_inputs(
        self,
        step: PipelineStep,
        ctx: PipelineContext,
    ) -> dict[str, Any] | None:
        """Resolve all input_map references for a step.

        Returns the payload dict or None if a required input is missing.
        """
        reg = self._registry.get(step.service)
        if not reg or not reg.manifest:
            return None

        payload: dict[str, Any] = {}
        required_names = {inp.name for inp in reg.manifest.inputs if inp.required}

        for input_name, source_ref in step.input_map.items():
            value = ctx.resolve_ref(source_ref)
            if value is not None:
                payload[input_name] = value
            elif input_name in required_names:
                logger.warning(
                    "Step '%s': required input '%s' (ref '%s') resolved to None",
                    step.service, input_name, source_ref,
                )
                return None

        return payload

    def _apply_failure(
        self,
        step: PipelineStep,
        ctx: PipelineContext,
        error: str,
    ) -> None:
        """Apply the step's failure policy."""
        ctx.errors[step.service] = error
        if step.on_failure == "halt":
            ctx.halted = True
            logger.error("Step '%s' failed with halt policy — stopping pipeline: %s",
                         step.service, error)
        else:
            ctx.skipped.append(step.service)
            logger.warning("Step '%s' failed with skip policy — continuing: %s",
                           step.service, error)

    def _handle_binary(
        self,
        service_name: str,
        data: bytes,
        manifest: ServiceManifest,
    ) -> dict[str, Any]:
        """Handle binary response (save to disk if audio, return file URL)."""
        # Check if manifest declares a 'binary' output
        has_audio = any(o.type == "binary" and "audio" in o.name for o in manifest.outputs)
        if has_audio and data:
            self._cleanup_audio()
            audio_id = uuid.uuid4().hex[:12]
            audio_path = AUDIO_DIR / f"{audio_id}.wav"
            AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(data)
            return {"audio": data, "audio_url": f"/audio/{audio_id}.wav"}
        return {"data": data}

    @staticmethod
    def _cleanup_audio() -> None:
        """Remove old WAV files."""
        try:
            for wav in AUDIO_DIR.glob("*.wav"):
                try:
                    wav.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _legacy_events(
        self,
        service_name: str,
        result: dict[str, Any],
        ctx: PipelineContext,
    ) -> list[dict[str, Any]]:
        """Emit legacy SSE events for known services (backward compat)."""
        events: list[dict[str, Any]] = []
        if service_name == "classifier" and "label" in result:
            events.append({
                "event": "classify",
                "data": {"label": result["label"], "confidence": result.get("confidence")},
            })
        elif service_name in ("llm", "llm-ollama") and "response" in result:
            events.append({"event": "text", "data": {"text": result["response"]}})
        elif service_name == "tts" and "audio_url" in result:
            events.append({"event": "audio", "data": {"audio_url": result["audio_url"]}})
        return events

    def _build_summary(self, ctx: PipelineContext) -> dict[str, Any]:
        """Build the final pipeline result summary (backward-compatible)."""
        summary: dict[str, Any] = {
            "steps_completed": list(ctx.results.keys()),
            "steps_skipped": ctx.skipped,
            "sensor_samples": len(ctx.sensor_data),
            "label": None,
            "confidence": None,
            "text": None,
            "audio_url": None,
            "error": None,
        }

        # Pull out well-known values from step results
        classifier = ctx.results.get("classifier", {})
        summary["label"] = classifier.get("label")
        summary["confidence"] = classifier.get("confidence")

        for name in ("llm", "llm-ollama"):
            llm = ctx.results.get(name, {})
            if llm.get("response"):
                summary["text"] = llm["response"]
                break

        tts = ctx.results.get("tts", {})
        summary["audio_url"] = tts.get("audio_url")

        # First error encountered
        if ctx.errors:
            summary["error"] = next(iter(ctx.errors.values()))

        return summary
