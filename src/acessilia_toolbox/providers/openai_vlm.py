"""Vision-language model adapter for OpenAI-compatible chat servers.

Serves the Dr.DocBench VLM capabilities from any server that implements
``POST /v1/chat/completions`` with image inputs (llama.cpp's ``llama-server``,
vLLM, ...):

- ``document.ocr.page`` — whole-page Markdown transcription;
- ``document.adjudicate.page`` — pairwise judge between two transcriptions;
- ``document.adjudicate.region`` — exact transcription of a cropped region;
- ``table.recognize`` — table re-generation from a crop (greedy plus seeded
  samples) for the table specialist's self-consistency selection.

The prompts and sampling are the ones validated in the Dr.DocBench experiments
(``scripts/drbench/experiments`` in the core repository: ``vlm_page_ocr.py``
prompt v1, ``vlm_page_judge.py``, ``vlm_adjudicate.py``, ``vlm_tables.py``). The adapter returns
model evidence -- transcriptions and judge probabilities. Deciding what a
document does with it (thresholds, select/replace/drop) is the agentic core's
job.

Server-specific settings stay in the provider's ``config``: ``model`` (the id
the server serves), ``api_key``, ``max_concurrency`` (the server's parallel
slots), token and character limits, and ``page_sampling`` for sampling keys a
given server understands (``repeat_penalty`` for llama-server,
``repetition_penalty`` for vLLM).
"""

from __future__ import annotations

import base64
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from docstruct.fusion.judge import debias, p2_from_logprobs

from acessilia_toolbox.core.errors import (
    ConfigurationError,
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

CHAT_PATH = "/v1/chat/completions"

PAGE_OCR = "document.ocr.page"
JUDGE_PAGE = "document.adjudicate.page"
REGION = "document.adjudicate.region"
TABLE = "table.recognize"
CAPABILITIES = (PAGE_OCR, JUDGE_PAGE, REGION, TABLE)

DEFAULTS: dict[str, Any] = {
    "max_concurrency": 4,
    "page_max_tokens": 4096,
    # Each candidate is cut to this many characters. The experiments used 14000
    # with a 32k context; a smaller context needs a smaller cut.
    "judge_max_chars": 14000,
    "region_max_tokens": 1024,
    "region_max_chars": 1800,
    "top_logprobs": 20,
    "page_sampling": {},
    "table_max_tokens": 6000,
    "table_hint_chars": 6000,
    "table_max_samples": 8,
}

# vlm_page_ocr.py, prompt v1. Prompt v2 ("do not transcribe text inside
# pictures") was rejected in the experiments: the model ignores it (LOOP #18).
PAGE_PROMPT = """Transcribe this document page into Markdown, exactly as printed, in natural reading order.
Rules:
- Separate every block (paragraph, heading, list item, caption, footnote, header/footer line, page number) by a blank line.
- Headings start with '#' ('#' for the main title, '##' for section headings).
- Tables: output as HTML <table> with <tr>/<td> (colspan/rowspan where cells span). No Markdown pipe tables.
- Mathematical formulas in LaTeX: inline as $...$, display formulas as $$...$$ on their own lines.
- Use *italic* and **bold** where the text is italic or bold.
- Keep line-break hyphenation exactly as printed (e.g. "con-sidered" if the word is split across lines), but join the lines of a paragraph into one line.
- Include running headers, footers and page numbers as separate blocks at the position where they appear.
- Do not describe images or figures; transcribe only text that appears in them if it is a caption.
- Output only the Markdown, no commentary."""

# vlm_page_judge.py with its prompt variants (LOOP #46).
_JUDGE_BASE = """The image is a document page. Below are two candidate transcriptions of it (Markdown/HTML), produced by different OCR systems.

Decide which candidate is the more faithful transcription of the page, judging:
1. Text accuracy: no missing text, no extra/hallucinated/repeated text, no garbled characters or words.
2. Reading order: blocks in the natural order a human reads the page (columns top to bottom, left column first). Running headers, footers and page numbers are expected at the END.
3. Text that is part of a picture (photo, illustration, diagram, chart, map, logo) should NOT be transcribed; captions outside pictures should be.
Ignore Markdown/HTML formatting differences (heading marks, bold, line breaks).

=== Candidate 1 ===
{c1}

=== Candidate 2 ===
{c2}

Which candidate is more faithful? Answer with exactly one character: 1 or 2."""
JUDGE_PROMPTS = {
    "base": _JUDGE_BASE,
    "para": _JUDGE_BASE.replace(
        "Ignore Markdown/HTML formatting",
        "4. Block structure: each paragraph, verse stanza, list or table kept whole as ONE block "
        "(not split line by line, not several paragraphs glued together); blank lines separate "
        "blocks.\nIgnore other Markdown/HTML formatting",
    ),
    "err": _JUDGE_BASE.replace(
        "Decide which candidate is the more faithful transcription of the page, judging:",
        "Compare both candidates against the page image, word by word, and count their errors. "
        "Consider:",
    ).replace(
        "Which candidate is more faithful?", "Which candidate has FEWER errors overall?"
    ),
}

# vlm_adjudicate.py.
REGION_PROMPT = """You are a precise OCR transcriber.
The image is a region cropped from a document page. OCR hypotheses for this region (may contain errors):

{cands}

Transcribe EXACTLY the text visible in the image.
- Do not paraphrase, translate, summarise or correct grammar.
- Preserve capitalisation, punctuation, numbers and symbols exactly as printed.
- Plain text only: no markdown syntax, no code fences, no commentary.
- If the region contains no readable text, answer exactly: <EMPTY>
Return ONLY the transcription."""
REGION_EMPTY = "<EMPTY>"

# vlm_tables.py. The header-cell rule and the hypotheses block are switchable so
# the experiment's choices can be tested: the original asks for <th> headers and
# always shows the providers' tables.
_TABLE_HEADER_RULES = {
    "th": "- Output a single <table> using only <tr>, <td> (use <th> only for header cells), "
    "with colspan/rowspan where cells span.",
    "td": "- Output a single <table> using only <tr> and <td> (header cells too), "
    "with colspan/rowspan where cells span.",
}
TABLE_PROMPT_HINTED = """You are a precise table recognition system.
The image is a table cropped from a document page. Two OCR systems produced these HTML hypotheses (may contain
errors in structure or text):

{cands}

Reproduce the table in the image as HTML.
{header_rule}
- One <tr> per visual row; every visual cell becomes one cell, empty cells as <td></td>.
- Transcribe cell text EXACTLY as printed (numbers, punctuation, symbols, capitalisation). No styles, no markdown.
Return ONLY the HTML table."""
TABLE_PROMPT_BLIND = """You are a precise table recognition system.
The image is a table cropped from a document page.

Reproduce the table in the image as HTML.
{header_rule}
- One <tr> per visual row; every visual cell becomes one cell, empty cells as <td></td>.
- Transcribe cell text EXACTLY as printed (numbers, punctuation, symbols, capitalisation). No styles, no markdown.
Return ONLY the HTML table."""
_TABLE_FENCE = re.compile(r"```[a-z]*")

_PAGE_FENCE = re.compile(r"^```(?:markdown|md)?\s*\n|\n```\s*$")
_REGION_FENCE = re.compile(r"^```[a-z]*\n?|```$")
# How llama-server and vLLM report a prompt larger than the context window.
_CONTEXT_ERROR = re.compile(r"context|too long|exceed", re.IGNORECASE)

_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {}
_SEMAPHORES_LOCK = threading.Lock()


def _semaphore(provider_id: str, size: int) -> threading.BoundedSemaphore:
    """One limiter per provider, shared by every adapter instance of it."""
    with _SEMAPHORES_LOCK:
        if provider_id not in _SEMAPHORES:
            _SEMAPHORES[provider_id] = threading.BoundedSemaphore(max(1, size))
        return _SEMAPHORES[provider_id]


class OpenAiCompatibleVlmProvider:
    """Calls a vision-language model over the OpenAI chat-completions API."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")
        self.config: dict[str, Any] = {**DEFAULTS, **descriptor.config}
        if not self.config.get("model"):
            raise ConfigurationError(
                f"provider {descriptor.id} needs config.model (the id the server serves)",
                provider=descriptor.id,
            )
        self._slots = _semaphore(descriptor.id, int(self.config["max_concurrency"]))

    def _client(self, timeout: float | None = None) -> httpx.Client:
        headers = {}
        api_key = str(self.config.get("api_key") or "")
        if api_key and "${" not in api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.descriptor.timeout_seconds,
            headers=headers,
        )

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        params = dict(parameters or {})
        image = {
            "type": "image_url",
            "image_url": {
                "url": f"data:{media_type};base64,"
                + base64.b64encode(payload).decode("ascii")
            },
        }
        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        if capability_id == PAGE_OCR:
            document = self._transcribe_page(image)
        elif capability_id == JUDGE_PAGE:
            document = self._judge_page(image, params)
        elif capability_id == REGION:
            document = self._transcribe_region(image, params)
        elif capability_id == TABLE:
            document = self._recognize_table(image, params)
        else:
            raise ProviderExecutionError(
                f"{self.descriptor.id} does not implement {capability_id}",
                provider=self.descriptor.id,
            )

        return ExtractionResult(
            document=document,
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            duration_ms=int((perf_counter() - started_clock) * 1000),
            version=self.descriptor.version,
            configuration={
                "provider": self.descriptor.id,
                "capability": capability_id,
                "model": document["model"],
            },
        )

    # -- capabilities ---------------------------------------------------------

    def _transcribe_page(self, image: dict[str, Any]) -> dict[str, Any]:
        response = self._chat(
            [image, {"type": "text", "text": PAGE_PROMPT}],
            max_tokens=int(self.config["page_max_tokens"]),
            extra=dict(self.config.get("page_sampling") or {}),
        )
        choice = _first_choice(response, self.descriptor.id)
        text = _PAGE_FENCE.sub("", _message_text(choice).strip()).strip()
        return {
            "markdown": text,
            "finish": choice.get("finish_reason"),
            "model": str(response.get("model") or self.config["model"]),
        }

    def _judge_page(self, image: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        candidate_a = params.get("candidate_a")
        candidate_b = params.get("candidate_b")
        if not isinstance(candidate_a, str) or not isinstance(candidate_b, str):
            raise InvalidInputError(
                "parameters.candidate_a and parameters.candidate_b must be strings",
                capability=JUDGE_PAGE,
            )
        prompt_name = str(params.get("prompt") or "base")
        if prompt_name not in JUDGE_PROMPTS:
            raise InvalidInputError(
                f"unknown judge prompt {prompt_name!r}; expected one of {sorted(JUDGE_PROMPTS)}",
                capability=JUDGE_PAGE,
            )
        limit = int(self.config["judge_max_chars"])
        a, b = candidate_a[:limit], candidate_b[:limit]

        model = str(self.config["model"])
        probabilities = []
        # p(second candidate) with the order (A, B), then with (B, A).
        for first, second in ((a, b), (b, a)):
            response = self._chat(
                [
                    image,
                    {
                        "type": "text",
                        "text": JUDGE_PROMPTS[prompt_name].format(c1=first, c2=second),
                    },
                ],
                max_tokens=1,
                logprobs=int(self.config["top_logprobs"]),
            )
            model = str(response.get("model") or model)
            probabilities.append(
                p2_from_logprobs(_first_token_logprobs(response, self.descriptor.id))
            )
        p_b_ab, p_a_ba = probabilities
        return {
            "pB_ab": p_b_ab,
            "pA_ba": p_a_ba,
            "pB": debias(p_b_ab, p_a_ba),
            "prompt": prompt_name,
            "model": model,
        }

    def _transcribe_region(
        self, image: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        candidates = params.get("candidates")
        if not isinstance(candidates, list) or not all(
            isinstance(item, str) for item in candidates
        ):
            raise InvalidInputError(
                "parameters.candidates must be a list of strings", capability=REGION
            )
        limit = int(self.config["region_max_chars"])
        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        hypotheses = "\n\n".join(
            f'Hypothesis {labels[index % len(labels)]}:\n"""{text[:limit]}"""'
            for index, text in enumerate(item for item in candidates if item)
        )
        response = self._chat(
            [image, {"type": "text", "text": REGION_PROMPT.format(cands=hypotheses)}],
            max_tokens=int(self.config["region_max_tokens"]),
        )
        text = _REGION_FENCE.sub(
            "", _message_text(_first_choice(response, self.descriptor.id)).strip()
        ).strip()
        return {
            "text": "" if text == REGION_EMPTY else text,
            "model": str(response.get("model") or self.config["model"]),
        }

    def _recognize_table(
        self, image: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        hints = params.get("hints", [])
        if not isinstance(hints, list) or not all(isinstance(item, str) for item in hints):
            raise InvalidInputError("parameters.hints must be a list of strings", capability=TABLE)
        use_hints = bool(params.get("use_hints", True))
        header_cells = str(params.get("header_cells") or "th")
        if header_cells not in _TABLE_HEADER_RULES:
            raise InvalidInputError(
                f"parameters.header_cells must be one of {sorted(_TABLE_HEADER_RULES)}",
                capability=TABLE,
            )
        try:
            samples = int(params.get("samples", 3))
            temperature = float(params.get("temperature", 0.7))
            top_p = float(params.get("top_p", 0.95))
            seed = int(params.get("seed", 0))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(
                "samples, temperature, top_p and seed must be numbers", capability=TABLE
            ) from exc
        max_samples = int(self.config["table_max_samples"])
        if not 0 <= samples <= max_samples:
            raise InvalidInputError(
                f"parameters.samples must be between 0 and {max_samples}", capability=TABLE
            )

        limit = int(self.config["table_hint_chars"])
        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        hypotheses = "\n\n".join(
            f"Hypothesis {labels[index % len(labels)]}:\n{text[:limit]}"
            for index, text in enumerate(item for item in hints if item)
        )
        header_rule = _TABLE_HEADER_RULES[header_cells]
        prompt = (
            TABLE_PROMPT_HINTED.format(cands=hypotheses or "(none)", header_rule=header_rule)
            if use_hints
            else TABLE_PROMPT_BLIND.format(header_rule=header_rule)
        )
        content = [image, {"type": "text", "text": prompt}]
        max_tokens = int(self.config["table_max_tokens"])
        model = str(self.config["model"])

        def generation(response: dict[str, Any]) -> dict[str, Any]:
            choice = _first_choice(response, self.descriptor.id)
            text = _TABLE_FENCE.sub("", _message_text(choice)).strip()
            return {"text": text, "finish": choice.get("finish_reason")}

        greedy_response = self._chat(content, max_tokens=max_tokens)
        model = str(greedy_response.get("model") or model)
        drawn = []
        # One request per sample with its own seed: reproducible, and it works on
        # servers that do not implement n > 1.
        for index in range(samples):
            response = self._chat(
                content,
                max_tokens=max_tokens,
                extra={"temperature": temperature, "top_p": top_p, "seed": seed + index},
            )
            drawn.append({**generation(response), "seed": seed + index})
        return {
            "greedy": generation(greedy_response),
            "samples": drawn,
            "temperature": temperature,
            "top_p": top_p,
            "use_hints": use_hints,
            "header_cells": header_cells,
            "model": model,
        }

    # -- transport ------------------------------------------------------------

    def _chat(
        self,
        content: list[dict[str, Any]],
        *,
        max_tokens: int,
        logprobs: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "temperature": 0,
            **(extra or {}),
            "model": self.config["model"],
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        if logprobs:
            body["logprobs"] = True
            body["top_logprobs"] = logprobs

        provider = self.descriptor.id
        with self._slots:
            try:
                with self._client() as client:
                    response = client.post(CHAT_PATH, json=body)
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(
                    f"{provider} did not answer within {self.descriptor.timeout_seconds}s",
                    provider=provider,
                ) from exc
            except httpx.RequestError as exc:
                raise ProviderUnavailableError(
                    f"{provider} is not reachable at {self.base_url}: {exc}",
                    provider=provider,
                ) from exc

        if response.status_code == 400 and _CONTEXT_ERROR.search(response.text):
            raise InvalidInputError(
                f"input exceeds the context window of {provider}: {response.text[:300]}",
                provider=provider,
            )
        if response.status_code != 200:
            raise ProviderExecutionError(
                f"{provider} failed with status {response.status_code}: {response.text[:300]}",
                provider=provider,
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderExecutionError(
                f"{provider} returned a non-JSON response: {response.text[:200]}",
                provider=provider,
            ) from exc
        if not isinstance(data, dict):
            raise ProviderExecutionError(
                f"{provider} returned {type(data).__name__}; expected a JSON object",
                provider=provider,
            )
        return data

    def versions(self) -> dict[str, str]:
        # The declared version names the served weights; bump it in the provider
        # configuration when the model changes, since it is part of the cache key.
        return {"provider": self.descriptor.version, "model": str(self.config["model"])}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(self.descriptor.health_path or "/health")
                response.raise_for_status()
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=True,
                version=self.descriptor.version,
                checked_at=checked_at,
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                checked_at=checked_at,
            )


def _first_choice(response: dict[str, Any], provider: str) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderExecutionError(f"{provider} returned no choices", provider=provider)
    return choices[0]


def _message_text(choice: dict[str, Any]) -> str:
    message = choice.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else ""


def _first_token_logprobs(response: dict[str, Any], provider: str) -> dict[str, float]:
    """Top log-probabilities of the first generated token, by token text."""
    logprobs = _first_choice(response, provider).get("logprobs") or {}
    content = logprobs.get("content") if isinstance(logprobs, dict) else None
    if not content or not isinstance(content[0], dict):
        raise ProviderExecutionError(
            f"{provider} returned no logprobs; the page judge needs top_logprobs",
            provider=provider,
        )
    first = content[0]
    candidates = first.get("top_logprobs") or [first]
    return {
        str(item["token"]): float(item["logprob"])
        for item in candidates
        if isinstance(item, dict) and "token" in item and "logprob" in item
    }


__all__ = ["CAPABILITIES", "OpenAiCompatibleVlmProvider"]
