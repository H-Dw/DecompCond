"""DeepSeek structured batch planning with bounded API and proposal retries."""

from __future__ import annotations
import json
import time
import httpx
from pathlib import Path
from .credentials import read_credential
from .io import TechnicalFailure, InvalidProposal, write_json, file_digest
from .paths import validate_batch
from .sampling_contract import planning_sampling_contract


class DeepSeekPlanner:
    def __init__(self, config):
        self.config = config
        self.guidance = None
        if config.agent.guidance_file:
            path = Path(config.agent.guidance_file)
            if file_digest(path) != config.agent.guidance_sha256:
                raise TechnicalFailure("planner guidance checksum mismatch")
            self.guidance = path.read_text(encoding="utf-8")

    def request(self, messages):
        c = self.config.agent
        key = read_credential(c.credentials)
        body = dict(
            model=c.model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=c.max_tokens,
            thinking=c.thinking,
        )
        if c.temperature is not None:
            body["temperature"] = c.temperature
        for attempt in range(c.api_retries + 1):
            try:
                with httpx.Client(timeout=c.timeout) as client:
                    response = client.post(
                        c.base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + key},
                        json=body,
                    )
                if response.status_code >= 400:
                    if (
                        response.status_code not in (408, 429)
                        and response.status_code < 500
                    ):
                        raise TechnicalFailure(
                            f"DeepSeek HTTP {response.status_code}; response body omitted"
                        )
                    raise httpx.TransportError(f"HTTP {response.status_code}")
                payload = response.json()
                choice = payload["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise InvalidProposal(
                        "API response was truncated; increase configured max_tokens"
                    )
                # Reasoning content is not persisted or used as proposed actions.
                return choice["message"]["content"], {
                    "model": payload.get("model"),
                    "usage": payload.get("usage"),
                    "request_id": payload.get("id"),
                    "finish_reason": choice.get("finish_reason"),
                }
            except (httpx.TransportError, ValueError, KeyError, IndexError):
                if attempt == c.api_retries:
                    raise TechnicalFailure(
                        "DeepSeek transport or response failure; credentials/body omitted"
                    ) from None
                time.sleep(c.retry_delay)

    def propose(self, library, feedback, previous, folder):
        c = self.config
        request = {
            "conditions": library["groups"],
            "atoms": library["atoms"],
            "bonds": library["bonds"],
            "steps": c.sampling.steps,
            "segment_multiple": c.sampling.segment_multiple,
            "path_count": c.search.paths_per_round,
            "sampling_contract": planning_sampling_contract(c),
            "condition_library_hash": library.get("hash"),
            "feedback": feedback,
        }
        if self.guidance is not None:
            # Check again before each proposal: changing a file during a run
            # must not silently change the planner's instructions.
            if file_digest(c.agent.guidance_file) != c.agent.guidance_sha256:
                raise TechnicalFailure("planner guidance changed during run")
            request["planning_guidance"] = dict(
                sha256=c.agent.guidance_sha256, content=self.guidance
            )
        messages = [
            {"role": "system", "content": c.agent.system_prompt},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        ]
        write_json(folder / "agent_input.json", request)
        write_json(folder / "agent_messages.json", messages)
        for repair in range(c.search.proposal_repairs + 1):
            try:
                content, meta = self.request(messages)
                # Save only the structured action response, not hidden reasoning.
                write_json(
                    folder / f"agent_response_{repair}.json",
                    {"content": content, "metadata": meta},
                )
                messages.append({"role": "assistant", "content": content})
                obj = json.loads(content)
                if set(obj) != {"paths"}:
                    raise InvalidProposal("top-level object must contain only paths")
                paths = validate_batch(
                    obj["paths"],
                    library,
                    c.sampling.steps,
                    c.sampling.segment_multiple,
                    c.search.paths_per_round,
                    previous,
                    set(feedback["evidence_ids"]),
                )
                return paths
            except (ValueError, TypeError, KeyError) as error:
                if repair == c.search.proposal_repairs:
                    raise TechnicalFailure(
                        "proposal repair budget exhausted: " + str(error)
                    ) from None
                messages.append(
                    {
                        "role": "user",
                        "content": "Previous proposal rejected: "
                        + str(error)
                        + ". Return a complete corrected JSON batch.",
                    }
                )
        raise TechnicalFailure("no valid proposal batch")
