"""Operation identifiers and cooperative cancellation.

A caller may attach ``task_id``, ``operation_id`` and ``attempt_id`` headers to
a capability or artifact call.  The identifiers are optional so existing
clients keep working, but when an ``operation_id`` is present this registry is
the single place that owns its state:

``running``
    A request is executing (or waiting for a slot/cache/provider).
``cancel_requested``
    A cancel endpoint matched the operation and asked execution to stop.  This
    is not a confirmation that a provider stopped computing.
``cancelled``
    The request observed the cancel request and stopped before publishing a
    result.  No result is stored, cached or returned.
``finished``
    The request completed before any cancel request was observed.
``failed``
    The request failed before any cancel request was observed.

The registry is process-local.  Deployments with independent Toolbox
endpoints share no state, and a load balancer must route the cancel request to
the replica that owns the operation.  Both limits are explicit in ADR-0019.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    OperationCancelledError,
    OperationNotFoundError,
)

OperationState = Literal[
    "running",
    "cancel_requested",
    "cancelled",
    "finished",
    "failed",
]

#: Identifiers travel in headers, so keep them short and log/URL-safe.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_TERMINAL = frozenset({"cancelled", "finished", "failed"})


@dataclass
class Operation:
    operation_id: str
    task_id: str
    attempt_id: str | None
    capability_id: str
    state: OperationState = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    #: The response or error of the owner request, kept for a retry that joins
    #: the same operation instead of running the provider twice.
    result: Any = None
    error: BaseException | None = None

    @property
    def cancel_requested(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL

    def payload(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "state": self.state,
            "cancel_requested": self.cancel_requested,
        }


class OperationRegistry:
    """Thread-safe in-memory operation states, bounded to terminal entries.

    Running entries are never evicted.  Old terminal entries can be removed
    when the registry is full; cancelling an unknown id is a 404, which is the
    same answer an old operation receives after its state was forgotten.
    """

    def __init__(self, *, max_terminal_entries: int = 4096) -> None:
        self._lock = threading.Lock()
        self._operations: dict[str, Operation] = {}
        self._terminal_order: list[str] = []
        self._max_terminal_entries = max(1, max_terminal_entries)

    def begin(
        self,
        operation_id: str,
        *,
        task_id: str | None,
        attempt_id: str | None,
        capability_id: str,
    ) -> tuple[Operation, bool]:
        """Return the operation and whether this caller owns its execution.

        A retry that arrives while the first attempt still runs, or after it
        finished, gets the same operation with ``owner=False`` and joins its
        result.  The provider therefore runs once per ``operation_id`` while
        the state is retained, which is the property #406's retry budget needs.
        """
        operation_id = _validated(operation_id, "operation_id")
        task_id = _validated(task_id, "task_id")
        if attempt_id is not None:
            attempt_id = _validated(attempt_id, "attempt_id")
        with self._lock:
            existing = self._operations.get(operation_id)
            if existing is not None:
                if existing.task_id != task_id:
                    # Do not disclose that another task owns this id.
                    raise OperationNotFoundError(
                        "unknown operation", operation_id=operation_id
                    )
                return existing, False
            operation = Operation(
                operation_id=operation_id,
                task_id=task_id,
                attempt_id=attempt_id,
                capability_id=capability_id,
            )
            self._operations[operation_id] = operation
            return operation, True

    def cancel(self, operation_id: str, *, task_id: str | None) -> Operation:
        operation_id = _validated(operation_id, "operation_id")
        task_id = _validated(task_id, "task_id")
        with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None or operation.task_id != task_id:
                raise OperationNotFoundError(
                    "unknown operation", operation_id=operation_id
                )
            if operation.state == "running":
                operation.state = "cancel_requested"
                operation.cancel_event.set()
            return operation

    def finish(
        self,
        operation: Operation,
        state: Literal["cancelled", "finished", "failed"],
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        with self._lock:
            if operation.terminal:
                return
            if operation.cancel_event.is_set() and state == "finished":
                state = "cancelled"
            operation.state = state
            operation.finished_at = time.time()
            operation.result = result
            operation.error = error
            self._terminal_order.append(operation.operation_id)
            while len(self._terminal_order) > self._max_terminal_entries:
                old = self._terminal_order.pop(0)
                current = self._operations.get(old)
                if current is not None and current.terminal:
                    self._operations.pop(old, None)
            operation.done_event.set()

    def get(self, operation_id: str) -> Operation | None:
        with self._lock:
            return self._operations.get(operation_id)


def ensure_not_cancelled(operation: Operation | None) -> None:
    """Raise before publishing a result from a cancelled operation."""
    if operation is not None and operation.cancel_requested:
        raise OperationCancelledError(
            "operation was cancelled", operation_id=operation.operation_id
        )


def _validated(value: str | None, name: str) -> str:
    if value is None or not value:
        raise InvalidInputError(f"{name} is required with operation headers")
    if not _IDENTIFIER.fullmatch(value):
        raise InvalidInputError(
            f"{name} must be 1-200 log-safe characters", field=name
        )
    return value


__all__ = [
    "Operation",
    "OperationRegistry",
    "OperationState",
    "ensure_not_cancelled",
]
