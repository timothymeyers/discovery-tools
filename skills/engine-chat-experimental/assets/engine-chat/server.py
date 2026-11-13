#!/usr/bin/env python3
"""Read-only loopback server for Engine Chat."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse


SERVICE_ID = "engine-chat"
ENGINE_CHAT_VERSION = "0.1.0-experimental"
PROTOCOL_VERSION = "1"
TESTED_DISCOVERY_VERSION = "0.15.21"
TESTED_OS = "macOS"
LOOPBACK_HOST = "127.0.0.1"
TERMINAL_STATES = {"cancelled", "completed", "failed", "stopped"}
IDLE_LIVE_WINDOW_SECONDS = 30 * 60
ACTIVE_AGENT_STATES = {"running", "queued", "pending", "starting", "waiting", "permissionneeded"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
SOURCE_REF_RE = re.compile(r"\s*\[source:\s*[^\]]+\]", re.IGNORECASE)
TASK_ID_RE = re.compile(r"DX-\d+", re.IGNORECASE)
TOOL_FAILURE_RE = re.compile(r"Tool call failed:\s*(.*?)\s+-\s+", re.DOTALL)
FRACTION_RE = re.compile(r"^(?P<head>.*?\.)(?P<fraction>\d+)(?P<zone>Z|[+-]\d{2}:\d{2})?$")
STEERING_RE = re.compile(
    r"^Communication (?P<delivery>queued|applied) on steering:[^:]+:\s*(?P<message>.*)$",
    re.DOTALL | re.IGNORECASE,
)
STEERING_INTERRUPT_RE = re.compile(r"^Turn interrupted by steering message\b", re.IGNORECASE)
USER_REQUEST_RE = re.compile(
    r"\b(?:waiting\s+(?:for|on)|awaiting|pending|needs?|requires?|ready\s+for)\s+"
    r"(?:your|human|user)\s+(?:final\s+)?"
    r"(?P<kind>approval|review|input|decision|confirmation|response|feedback)\b",
    re.IGNORECASE,
)
NON_CURRENT_REQUEST_RE = re.compile(
    r"\b(?:not|no|never|without|already|previously|earlier|was|were|had|"
    r"if|when|once|after|example|quoted|says?|said|mentions?|"
    r"don't|doesn't|isn't|aren't|wasn't|weren't)\b",
    re.IGNORECASE,
)
RESOLVED_REQUEST_RE = re.compile(
    r"\b(?:approval|review|input|decision|confirmation|response|feedback)\s+"
    r"(?:(?:has been|is|was)\s+)?(?:received|granted|provided|completed|resolved)\b|"
    r"\bno\s+(?:further\s+)?(?:action|approval|review|input)\s+(?:is\s+)?(?:needed|required)\b|"
    r"\bno longer\s+(?:waiting|awaiting|needed|required)\b",
    re.IGNORECASE,
)
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; font-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def workspace_fingerprint(workspace: Path) -> str:
    canonical = str(workspace.expanduser().resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_iso(value: Optional[str]) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    match = FRACTION_RE.match(value)
    if not match:
        return value.replace("Z", "+00:00")
    fraction = (match.group("fraction") + "000000")[:6]
    zone = match.group("zone") or ""
    if zone == "Z":
        zone = "+00:00"
    return "{}{}{}".format(match.group("head"), fraction, zone)


def parse_time(value: Optional[str]) -> Optional[datetime]:
    normalized = normalize_iso(value)
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def iso_from_stat(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def compact_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_inline(value: Any, limit: int = 220) -> str:
    text = str(value or "")
    text = SOURCE_REF_RE.sub("", text)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = re.sub(r"[`*_#>]", "", text)
    text = compact_whitespace(text)
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def clean_narrative(value: Any, workspace: Path, limit: int = 6000) -> str:
    text = str(value or "")
    workspace_text = str(workspace)
    workspace_aliases = {workspace_text}
    if workspace_text.startswith("/private/var/"):
        workspace_aliases.add(workspace_text[len("/private") :])
    elif workspace_text.startswith("/var/"):
        workspace_aliases.add("/private" + workspace_text)
    for workspace_alias in workspace_aliases:
        text = text.replace(workspace_alias + "/", "")
    text = SOURCE_REF_RE.sub("", text)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    lines = [compact_whitespace(line) for line in text.splitlines()]
    cleaned_lines: List[str] = []
    for line in lines:
        if line or (cleaned_lines and cleaned_lines[-1]):
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def extract_starting_prompt(value: Any, workspace: Path) -> Optional[str]:
    text = str(value or "")
    if not text.strip():
        return None
    if "</engine-memory>" in text:
        text = text.split("</engine-memory>", 1)[1]
    text = clean_narrative(text, workspace, limit=12000)
    return text or None


def extract_steering_message(value: Any, workspace: Path) -> Optional[str]:
    match = STEERING_RE.match(str(value or "").strip())
    if not match:
        return None
    message = clean_narrative(match.group("message"), workspace, limit=12000)
    return message or None


def is_steering_boundary(event: Dict[str, Any]) -> bool:
    if event.get("kind") != "Progress":
        return False
    content = str(event.get("content") or "").strip()
    match = STEERING_RE.match(content)
    return bool(
        (match and match.group("delivery").lower() == "applied")
        or STEERING_INTERRUPT_RE.match(content)
    )


def coalesce_streaming_text_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rebuild Thinking/Observation streams that tools can interleave token-by-token."""
    result: List[Dict[str, Any]] = []
    active: Dict[str, Dict[str, Any]] = {}

    for event in events:
        kind = str(event.get("kind", ""))
        if kind not in {"Thinking", "Observation"}:
            result.append(event)
            if kind == "Done" or is_steering_boundary(event):
                active.clear()
            continue

        content = str(event.get("content", ""))
        current = active.get(kind)
        timestamp = parse_time(event.get("timestamp"))
        starts_named_block = content.lstrip().startswith("**")
        last_timestamp = parse_time(current.get("_lastTimestamp")) if current else None
        long_gap = bool(
            timestamp
            and last_timestamp
            and (timestamp - last_timestamp).total_seconds() > 5
        )

        if current is None or starts_named_block or long_gap:
            rebuilt = dict(event)
            rebuilt["content"] = content
            rebuilt["_lastTimestamp"] = event.get("timestamp")
            result.append(rebuilt)
            active[kind] = rebuilt
        else:
            current["content"] = str(current.get("content", "")) + content
            current["_lastTimestamp"] = event.get("timestamp")

    for event in result:
        event.pop("_lastTimestamp", None)
    return result


def markdown_title(content: Any) -> str:
    text = str(content or "")
    match = re.match(r"\s*\*\*(.*?)\*\*", text, flags=re.DOTALL)
    if match:
        return compact_whitespace(match.group(1))
    return compact_whitespace(text.splitlines()[0] if text.splitlines() else text)


def fenced_json(content: Any) -> List[Any]:
    documents: List[Any] = []
    for candidate in JSON_FENCE_RE.findall(str(content or "")):
        try:
            documents.append(json.loads(candidate))
        except (json.JSONDecodeError, TypeError):
            continue
    return documents


def loose_json_string_field(content: Any, field: str) -> Tuple[Optional[str], bool]:
    text = str(content or "")
    match = re.search(r'"{}"\s*:\s*"'.format(re.escape(field)), text)
    if not match:
        return None, False
    start = match.end() - 1
    escaped = False
    for index in range(start + 1, len(text)):
        character = text[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"':
            try:
                return json.loads(text[start : index + 1]), False
            except json.JSONDecodeError:
                return None, False

    fragment = text[start + 1 :]
    cutoffs = [position for marker in ("\n…", "\n```", "…") if (position := fragment.find(marker)) >= 0]
    if cutoffs:
        fragment = fragment[: min(cutoffs)]
    trailing_backslashes = len(fragment) - len(fragment.rstrip("\\"))
    if trailing_backslashes % 2 == 1:
        fragment = fragment[:-1]
    try:
        return json.loads('"{}"'.format(fragment)), True
    except json.JSONDecodeError:
        return fragment.replace("\\n", "\n").replace('\\"', '"'), True


def proposed_input(content: Any) -> Dict[str, Any]:
    for document in fenced_json(content):
        if isinstance(document, dict) and isinstance(document.get("rawInput"), dict):
            result = dict(document["rawInput"])
            result["__eventKind"] = document.get("kind")
            return result
    recovered: Dict[str, Any] = {}
    truncated = False
    for field in ("agentId", "taskId", "status", "conversationName", "prompt", "path", "filePath", "query", "reason", "description", "text", "comment"):
        value, was_truncated = loose_json_string_field(content, field)
        if value is not None:
            recovered[field] = value
            truncated = truncated or was_truncated
    if recovered:
        recovered["__truncated"] = truncated or "…" in str(content or "")
    return recovered


def nested_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate or candidate[0] not in "[{":
        return value
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return value


def find_nested_key(value: Any, key: str) -> Any:
    value = nested_json(value)
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = find_nested_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_nested_key(child, key)
            if found is not None:
                return found
    return None


def applied_documents(content: Any) -> List[Any]:
    return fenced_json(content)


def applied_failed(content: Any) -> bool:
    for document in applied_documents(content):
        if not isinstance(document, dict):
            continue
        status = str(document.get("status", "")).lower()
        if status in {"error", "failed", "failure"}:
            return True
        raw_output = document.get("rawOutput")
        if isinstance(raw_output, dict):
            if raw_output.get("isError") is True or raw_output.get("success") is False:
                return True
            for field in ("content", "detailedContent"):
                nested = nested_json(raw_output.get(field))
                if isinstance(nested, dict):
                    nested_status = str(nested.get("status", "")).lower()
                    if "error" in nested or nested.get("success") is False:
                        return True
                    if nested_status in {"error", "failed", "failure"}:
                        return True
    return False


def applied_needs_followup(content: Any) -> bool:
    text = str(content or "").lower()
    return any(
        marker in text
        for marker in (
            "file too large to read at once",
            "output too large to read at once",
            "consider using the grep tool",
            "view with view_range",
        )
    )


def extract_sleep_result(content: Any) -> Tuple[Optional[str], Optional[int]]:
    for document in reversed(applied_documents(content)):
        until = find_nested_key(document, "sleepUntil")
        minutes = find_nested_key(document, "_sleepMinutes")
        if until is not None:
            try:
                parsed_minutes = int(minutes) if minutes is not None else None
            except (TypeError, ValueError):
                parsed_minutes = None
            return str(until), parsed_minutes
    return None, None


def display_name(definition_id: str) -> str:
    known = {
        "mission-control": "Mission Control",
        "generic-copilot": "Generic Copilot",
    }
    return known.get(definition_id, definition_id.replace("-", " ").title())


def agent_display_name(agent_id: Any) -> str:
    identifier = str(agent_id or "").strip()
    known = {
        "catalog/batteries-included": "Batteries Included",
        "catalog/postdoctoral-science-writer": "Postdoctoral Writer",
        "catalog/agentic-grader": "Agentic Grader",
    }
    if identifier in known:
        return known[identifier]
    leaf = identifier.split("/")[-1] or "Agent"
    return leaf.replace("-", " ").replace("_", " ").title()


def normalized_agent_state(value: Any) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def agent_state_label(value: Any) -> str:
    normalized = normalized_agent_state(value)
    labels = {
        "running": "Working",
        "queued": "Queued",
        "pending": "Queued",
        "starting": "Starting",
        "waiting": "Waiting",
        "permissionneeded": "Needs permission",
    }
    return labels.get(normalized, clean_inline(value or "Active", 40))


def task_version(task_title: Any, conversation_name: Any, task_id: Any) -> Optional[str]:
    text = "{} {}".format(task_title or "", conversation_name or "")
    match = re.search(r"\bv?(\d+\.\d+)\b", text, flags=re.IGNORECASE)
    if match:
        return "v{}".format(match.group(1))
    task_match = re.fullmatch(r"DX-(\d+)", str(task_id or ""), flags=re.IGNORECASE)
    if task_match and 68 <= int(task_match.group(1)) <= 76:
        return "v10.0"
    return None


def agent_work_label(
    agent_id: Any,
    task_id: Any,
    task_title: Any,
    conversation_name: Any,
) -> str:
    identifier = str(agent_id or "")
    version = task_version(task_title, conversation_name, task_id)
    if identifier.endswith("/agentic-grader") or identifier == "agentic-grader":
        return "Grading {}".format(version) if version else "Grading {}".format(task_id or "project outcomes")
    if task_id:
        return str(task_id)
    conversation = clean_inline(conversation_name, 64)
    return conversation or "Project work"


def state_label(state: Any) -> str:
    normalized = str(state or "Unknown").lower()
    labels = {
        "running": "Working",
        "sleeping": "Sleeping",
        "idle": "Idle",
        "paused": "Paused",
        "completed": "Complete",
        "failed": "Needs attention",
        "cancelled": "Cancelled",
        "stopped": "Stopped",
    }
    return labels.get(normalized, clean_inline(state or "Unknown", 40))


def is_live_state(state: Any, updated_at: Optional[str]) -> bool:
    normalized = str(state or "Unknown").lower()
    if normalized in TERMINAL_STATES:
        return False
    if normalized != "idle":
        return True
    updated = parse_time(updated_at)
    if updated is None:
        return False
    age_seconds = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
    return age_seconds <= IDLE_LIVE_WINDOW_SECONDS


def pretty_status(value: Any) -> str:
    normalized = str(value or "").strip()
    labels = {
        "new": "New",
        "executing": "In progress",
        "executionDone": "Ready for review",
        "complete": "Complete",
        "onHold": "On hold",
        "failed": "Needs attention",
        "incomplete": "Incomplete",
        "stale": "Stale",
        "flaggedHuman": "Needs human review",
        "flaggedAi": "Needs AI review",
        "removed": "Removed",
    }
    return labels.get(normalized, re.sub(r"(?<!^)([A-Z])", r" \1", normalized).title())


def pretty_agent(value: Any) -> str:
    agent = str(value or "").split("/")[-1]
    return agent.replace("-", " ").title() or "an agent"


def relative_display_path(value: Any, workspace: Path) -> str:
    if not value:
        return "a file"
    candidate = Path(str(value)).expanduser()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return candidate.name or "a file"


def collect_task_ids(value: Any) -> List[str]:
    found: List[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, str):
            for match in TASK_ID_RE.finditer(node):
                normalized = match.group(0).upper()
                if normalized not in found:
                    found.append(normalized)

    visit(value)
    return found


def task_scope(task_ids: List[str]) -> str:
    if not task_ids:
        return "the task list"
    if len(task_ids) == 1:
        return task_ids[0]
    if len(task_ids) <= 3:
        return ", ".join(task_ids)
    return "{} tasks ({}–{})".format(len(task_ids), task_ids[0], task_ids[-1])


def tool_key(title: str) -> str:
    return compact_whitespace(title).lower()


def summarize_action(title: str, raw_input: Dict[str, Any], workspace: Path) -> str:
    lowered = title.lower()
    tasks = collect_task_ids(raw_input)
    scope = task_scope(tasks)

    if lowered.replace("_", "") == "applypatch":
        return "Updating project files"
    if lowered.startswith("viewing ") or "read_file" in lowered:
        path = raw_input.get("path") or raw_input.get("filePath")
        return "Reading {}".format(relative_display_path(path, workspace))
    if lowered.startswith("using skill:"):
        skill = raw_input.get("skill") or title.split(":", 1)[-1]
        return "Loading {} support".format(clean_inline(skill, 80).replace("-", " ").title())

    if "agents-run-start" in lowered:
        agent_id = clean_inline(raw_input.get("agentId") or "an agent", 100)
        task_id = clean_inline(raw_input.get("taskId"), 40)
        if task_id:
            return "Launching {} to work on {}".format(agent_id, task_id)
        return "Launching {}".format(agent_id)
    if "agents-run-list" in lowered:
        return "Checking delegated runs"
    if "agents-session-permissionstate" in lowered:
        return "Checking agent permissions"
    if "agents-session-list" in lowered:
        return "Checking agent sessions"
    if "agents-session-get" in lowered:
        return "Checking agent session"
    if "agents-session-cancel" in lowered:
        return "Stopping agent session"
    if "tasks-get-many" in lowered:
        return "Getting additional detail on {}".format(scope)
    if "tasks-get" in lowered:
        return "Getting additional detail on {}".format(scope)
    if "tasks-graph" in lowered or "dependency-graph" in lowered:
        return "Mapping the work around {}".format(scope)
    if "tasks-subtree-summary" in lowered:
        return "Summarizing the work under {}".format(scope)
    if "tasks-ready" in lowered:
        return "Checking what is ready to start"
    if "tasks-blocked" in lowered:
        return "Checking what is blocked"
    if "tasks-list" in lowered or "tasks-find-by-title" in lowered:
        return "Reviewing the task list"
    if "tasks-transition" in lowered:
        return "Updating {} to {}".format(scope, pretty_status(raw_input.get("status")))
    if "tasks-completeexecution" in lowered:
        return "Recording delegated work as complete"
    if "tasks-failexecution" in lowered:
        return "Recording delegated work as needing attention"
    if "tasks-releaseexecution" in lowered:
        return "Releasing a delegated work claim"
    if "tasks-assign" in lowered:
        assignee = raw_input.get("assignedTo")
        agent_id = assignee.get("id") if isinstance(assignee, dict) else assignee
        return "Assigning {} to {}".format(scope, pretty_agent(agent_id))
    if "tasks-comment" in lowered:
        return "Recording progress on {}".format(scope)
    if "tasks-create" in lowered or "tasks-add-subtask" in lowered:
        return "Adding the next piece of work"
    if "tasks-update" in lowered or "tasks-decompose" in lowered:
        return "Refining {}".format(scope)

    if "purpose-progress" in lowered:
        return "Checking overall project progress"
    if "purpose-get" in lowered:
        return "Reviewing the project goal"
    if "purpose-outcome-list" in lowered:
        return "Reviewing project outcomes"
    if "purpose-outcome-get" in lowered:
        return "Reviewing an expected outcome"
    if "purpose-outcome-evaluate" in lowered:
        return "Evaluating an expected outcome"
    if "purpose-outcome-link-task" in lowered:
        return "Linking a task to an expected outcome"
    if "purpose-outcome-update" in lowered:
        return "Updating an expected outcome"
    if "purpose-rubric-list" in lowered or "purpose-rubric-get" in lowered:
        return "Reviewing the success criteria"
    if "purpose-grade" in lowered:
        return "Evaluating the latest work"

    if "agents-run-status" in lowered or "agents-run-get" in lowered:
        return "Checking delegated work on {}".format(scope)
    if "agents-list" in lowered:
        return "Checking available agents"
    if "agents-get" in lowered:
        return "Checking {}".format(pretty_agent(raw_input.get("agentId")))
    if "agents-" in lowered:
        return "Coordinating delegated work"

    if "bookshelf-search" in lowered:
        return "Searching project knowledge"
    if "bookshelf-ask" in lowered:
        return "Consulting project knowledge"
    if "bookshelf" in lowered:
        return "Reviewing project knowledge"

    if "engine-status" in lowered:
        return "Checking engine status"
    if "engine-log" in lowered:
        return "Recording an engine update"
    if "engine-stop" in lowered:
        return "Stopping an engine"
    if "engine-checkpoint" in lowered:
        return "Saving a checkpoint"
    if "engine-done" in lowered:
        return "Finishing the run"
    if "engine-sleep" in lowered:
        return "Scheduling the next check-in"

    if "toolcatalog" in lowered or "tool-catalog" in lowered:
        return "Looking for the right tool"
    if "cognition-startinstance" in lowered:
        return "Starting another engine"
    if lowered.rstrip("-") == "discovery-app-sdk-discovery-tasks":
        return "Updating task records"

    path = raw_input.get("filePath") or raw_input.get("path")
    if path:
        verb = "Updating" if any(word in lowered for word in ("edit", "write", "patch", "create")) else "Viewing"
        return "{} {}".format(verb, relative_display_path(path, workspace))

    if not lowered.startswith("discovery-app-sdk-"):
        friendly = clean_inline(title, 180)
        if friendly:
            return friendly

    stripped = re.sub(r"^discovery-app-sdk-discovery-", "", lowered)
    return "Working with {}".format(stripped.replace("-", " ") or "the project")


def summarize_narrative(value: str, limit: int = 120) -> str:
    text = compact_whitespace(value)
    if not text:
        return "An update"
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return clean_inline(first_sentence, limit)


def detail_repeats_summary(summary: Any, detail: Any) -> bool:
    def normalized(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", " ", compact_whitespace(str(value or "")).lower()).strip()

    summary_text = normalized(summary)
    detail_text = normalized(detail)
    return bool(summary_text and detail_text and summary_text == detail_text)


def action_detail(title: str, raw_input: Dict[str, Any], workspace: Path) -> Tuple[Optional[str], Optional[str]]:
    lowered = title.lower()
    if "agents-run-start" in lowered:
        prompt = clean_narrative(raw_input.get("prompt"), workspace, limit=12000)
        label = "Delegated prompt (log excerpt)" if raw_input.get("__truncated") else "Delegated prompt"
        return (label, prompt) if prompt else (None, None)

    if "tasks-comment" in lowered:
        note = clean_narrative(raw_input.get("text"), workspace, limit=4000)
        return ("Progress note", note) if note else (None, None)

    if "tasks-transition" in lowered:
        note = clean_narrative(raw_input.get("comment"), workspace, limit=4000)
        return ("Progress note", note) if note else (None, None)

    if "bookshelf-search" in lowered or "bookshelf-ask" in lowered:
        query = clean_narrative(raw_input.get("query"), workspace, limit=2000)
        return ("Knowledge request", query) if query else (None, None)

    if lowered.replace("_", "") == "applypatch":
        explanation = clean_narrative(raw_input.get("explanation"), workspace, limit=2000)
        return ("Change", explanation) if explanation else (None, None)

    description = clean_narrative(raw_input.get("description"), workspace, limit=2000)
    if description:
        return "Purpose", description

    path = raw_input.get("path") or raw_input.get("filePath")
    view_range = raw_input.get("view_range")
    if path and isinstance(view_range, list) and len(view_range) == 2:
        return "Read scope", "Lines {}–{}".format(view_range[0], view_range[1])

    return None, None


def action_profile(
    title: str, raw_input: Dict[str, Any], summary: str, workspace: Path
) -> Dict[str, Any]:
    lowered = title.lower()
    summary_lower = summary.lower()
    task_ids = collect_task_ids(raw_input)
    path = raw_input.get("path") or raw_input.get("filePath")
    resource: Optional[str] = None
    read_range: Optional[List[Any]] = None
    is_file_read = lowered.startswith("viewing ") or "read_file" in lowered or summary_lower.startswith("reading ")
    if is_file_read and path:
        resource = relative_display_path(path, workspace)
        candidate_range = raw_input.get("view_range")
        if isinstance(candidate_range, list) and len(candidate_range) == 2:
            read_range = candidate_range

    mutation_markers = (
        "agents-run-start",
        "tasks-transition",
        "tasks-comment",
        "tasks-assign",
        "tasks-create",
        "tasks-update",
        "tasks-decompose",
        "completeexecution",
        "failexecution",
        "releaseexecution",
        "applypatch",
        "engine-stop",
        "engine-done",
    )
    context_prefixes = (
        "reading ",
        "read ",
        "getting additional detail",
        "reviewing ",
        "checking ",
        "check ",
        "searching ",
        "consulting ",
        "mapping ",
        "summarizing ",
        "verify ",
        "validating ",
        "inspect ",
        "evaluating ",
    )
    raw_kind = str(raw_input.get("__eventKind", "")).lower()
    intent = "work"
    if not any(marker in lowered.replace("_", "") for marker in mutation_markers):
        if raw_kind in {"read", "search"} or summary_lower.startswith(context_prefixes):
            intent = "context"

    visual = "agent" if "agents-run-start" in lowered else "default"
    operation = "context" if intent == "context" else "work"
    target_status = None
    agent_role = None
    if "agents-run-start" in lowered:
        operation = "launch"
        agent_text = "{} {} {}".format(
            raw_input.get("agentId", ""),
            raw_input.get("conversationName", ""),
            str(raw_input.get("prompt", ""))[:500],
        ).lower()
        if "grader" in agent_text or "grading" in agent_text:
            agent_role = "grading"
        elif "production" in agent_text or "producer" in agent_text or "build" in agent_text:
            agent_role = "production"
        elif "postdoctoral-science-writer" in agent_text or "writer" in agent_text or "writing" in agent_text:
            agent_role = "writing"
        elif "visual" in agent_text or "figure" in agent_text:
            agent_role = "visuals"
        elif "review" in agent_text:
            agent_role = "review"
        elif "evidence" in agent_text or "research" in agent_text:
            agent_role = "research"
    elif "tasks-transition" in lowered:
        operation = "transition"
        target_status = pretty_status(raw_input.get("status"))
    elif "tasks-comment" in lowered:
        operation = "progress"
    elif "agents-session-cancel" in lowered:
        operation = "session-cancel"
    elif any(
        marker in lowered
        for marker in (
            "agents-run-list",
            "agents-session-list",
            "agents-session-get",
            "agents-session-permissionstate",
        )
    ):
        operation = "context"
        intent = "context"
    scope = task_ids[0] if task_ids else None
    if resource:
        dedupe_key = "read:{}".format(resource.lower())
    elif "tasks-get" in lowered and scope:
        dedupe_key = "task:{}".format(scope.lower())
    else:
        dedupe_key = "summary:{}".format(summary_lower)

    return {
        "intent": intent,
        "visual": visual,
        "scope": scope,
        "resource": resource,
        "resourceLabel": Path(resource).name if resource else None,
        "readRange": read_range,
        "dedupeKey": dedupe_key,
        "operation": operation,
        "targetStatus": target_status,
        "agentRole": agent_role,
        # Structured task references only; task names mentioned in a prompt
        # or progress note must not widen the live approval scope.
        "taskRefs": [str(raw_input["taskId"])]
        if operation in {"launch", "transition", "progress"} and raw_input.get("taskId") else [],
    }


def combined_status(entries: List[Dict[str, Any]]) -> str:
    statuses = [str(entry.get("status", "")) for entry in entries]
    if "pending" in statuses:
        return "pending"
    if "interrupted" in statuses:
        return "interrupted"
    if "completed" in statuses:
        return "completed"
    if "followup" in statuses:
        return "followup"
    return "failed"


def action_attempt_description(action: Dict[str, Any]) -> str:
    if action.get("readRange"):
        read_range = action["readRange"]
        description = "Lines {}–{}".format(read_range[0], read_range[1])
    elif action.get("resource"):
        description = "Whole-file request"
    else:
        description = str(action.get("detail") or action.get("summary") or "Request")
    status = str(action.get("status", "pending"))
    labels = {
        "completed": "completed",
        "failed": "didn’t complete",
        "followup": "continued in smaller chunks" if action.get("resource") else "required a narrower follow-up",
        "pending": "in progress",
    }
    return "{} — {}".format(description, labels.get(status, status))


def merge_adjacent_actions(timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for entry in timeline:
        previous_entry = merged[-1] if merged else None
        same_context_operation = bool(
            previous_entry
            and entry.get("intent") == "context"
            and previous_entry.get("intent") == "context"
        )
        same_session_cleanup = bool(
            previous_entry
            and entry.get("operation") == "session-cancel"
            and previous_entry.get("operation") == "session-cancel"
        )
        if (
            previous_entry
            and entry.get("type") == "action"
            and previous_entry.get("type") == "action"
            and (same_context_operation or same_session_cleanup)
            and entry.get("dedupeKey") == previous_entry.get("dedupeKey")
        ):
            previous = previous_entry
            attempts = previous.pop("_attempts", [dict(previous)])
            attempts.append(dict(entry))
            previous["_attempts"] = attempts
            previous["mergedCount"] = sum(int(attempt.get("mergedCount", 1)) for attempt in attempts)
            previous["status"] = combined_status(attempts)
            previous["finishedAt"] = entry.get("finishedAt") or previous.get("finishedAt")
            previous["detailLabel"] = "Combined activity"
            previous["detail"] = "\n".join(
                "{}. {}".format(index, action_attempt_description(attempt))
                for index, attempt in enumerate(attempts, start=1)
            )
            previous["failedAttemptCount"] = sum(
                1 for attempt in attempts if attempt.get("status") in {"failed", "followup"}
            )
            if previous.get("operation") == "session-cancel":
                previous["summary"] = "Stopping {} agent sessions".format(previous["mergedCount"])
            continue
        merged.append(entry)
    return merged


def label_read_revisits(timeline: List[Dict[str, Any]]) -> None:
    seen: set[str] = set()
    for entry in timeline:
        if entry.get("type") != "action" or not entry.get("resource"):
            continue
        resource = str(entry["resource"])
        if resource in seen:
            entry["summary"] = "Checking {} again".format(entry.get("resourceLabel") or resource)
        seen.add(resource)


def context_group_scope(entries: List[Dict[str, Any]]) -> Optional[str]:
    counts: Dict[str, int] = defaultdict(int)
    order: List[str] = []
    for entry in entries:
        scope = entry.get("scope")
        if not scope:
            continue
        if scope not in counts:
            order.append(scope)
        counts[scope] += 1
    if not counts:
        return None
    return max(order, key=lambda scope: counts[scope])


def roll_up_context(timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    context_run: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal context_run
        if len(context_run) < 2:
            result.extend(context_run)
            context_run = []
            return
        scope = context_group_scope(context_run)
        raw_count = sum(int(entry.get("mergedCount", 1)) for entry in context_run)
        group = {
            "id": "context-{}".format(context_run[0].get("id", len(result) + 1)),
            "type": "group",
            "groupKind": "context",
            "visual": "context",
            "summary": "Gathering context on {}".format(scope) if scope else "Gathering context",
            "status": combined_status(context_run),
            "startedAt": context_run[0].get("startedAt"),
            "finishedAt": context_run[-1].get("finishedAt"),
            "children": context_run,
            "childCount": len(context_run),
            "actionCount": raw_count,
            "failedAttemptCount": sum(int(entry.get("failedAttemptCount", 0)) for entry in context_run),
            "scope": scope,
            "operation": "context",
        }
        result.append(group)
        context_run = []

    for entry in timeline:
        if entry.get("type") == "action" and entry.get("intent") == "context":
            context_run.append(entry)
        else:
            flush()
            result.append(entry)
    flush()
    return result


def iter_actions(timeline: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for entry in timeline:
        if entry.get("type") == "group":
            yield from iter_actions(entry.get("children", []))
        elif entry.get("type") == "action":
            yield entry


def strip_internal_fields(timeline: List[Dict[str, Any]]) -> None:
    for entry in timeline:
        entry.pop("dedupeKey", None)
        entry.pop("_attempts", None)
        if entry.get("type") == "group":
            strip_internal_fields(entry.get("children", []))


def concise_turn_summary(timeline: List[Dict[str, Any]]) -> str:
    actions = list(iter_actions(timeline))
    successful_launches = [
        action
        for action in actions
        if action.get("operation") == "launch" and action.get("status") == "completed"
    ]
    completed_scopes = [
        str(action.get("scope"))
        for action in actions
        if action.get("operation") == "transition"
        and action.get("targetStatus") == "Complete"
        and action.get("scope")
    ]

    if completed_scopes and successful_launches:
        completed_scope = completed_scopes[-1]
        launch = successful_launches[-1]
        launch_scope = launch.get("scope") or "the next task"
        role = " {}".format(launch["agentRole"]) if launch.get("agentRole") else ""
        return "Completed {}; launched {}{}".format(completed_scope, launch_scope, role)

    if successful_launches:
        launch = successful_launches[-1]
        scope = launch.get("scope") or "delegated work"
        role = " {}".format(launch["agentRole"]) if launch.get("agentRole") else ""
        return "Launched {}{}".format(scope, role)

    if completed_scopes:
        return "Completed {}".format(completed_scopes[-1])

    context_scopes = [
        str(entry.get("scope"))
        for entry in timeline
        if entry.get("type") == "group" and entry.get("groupKind") == "context" and entry.get("scope")
    ]
    progress_scopes = [
        str(action.get("scope"))
        for action in actions
        if action.get("operation") == "progress" and action.get("scope")
    ]
    if context_scopes:
        scope = context_scopes[-1]
        if progress_scopes:
            return "Checked {} progress".format(scope)
        return "Reviewed {}".format(scope)

    if progress_scopes:
        return "Updated {} progress".format(progress_scopes[-1])

    observations = [entry for entry in timeline if entry.get("type") == "observation"]
    if observations:
        return clean_inline(observations[-1].get("summary"), 72)

    if actions:
        return clean_inline(actions[-1].get("summary"), 72)
    return "Turn complete"


def thought_block(value: str, workspace: Path) -> Optional[Dict[str, Optional[str]]]:
    raw = str(value or "").strip()
    if not raw:
        return None
    heading = re.match(r"^\s*\*\*(.*?)\*\*\s*", raw, flags=re.DOTALL)
    summary: Optional[str] = None
    if heading:
        summary = clean_inline(heading.group(1), 120)
        raw = raw[heading.end() :]
    text = clean_narrative(raw, workspace)
    if not text:
        return None
    summary = summary or summarize_narrative(text)
    detail = text if summary != text else None
    return {"summary": summary, "detail": detail}


def explicit_user_request(value: str, workspace: Path) -> Optional[Dict[str, str]]:
    """Recognize direct handoff language, not generic waiting or mentions of approval.

    This is an indication from an observation, not an authoritative approval queue.
    Quoted examples, code, historical requests, and negated requests are excluded.
    """
    text = re.sub(r"```.*?```", "", str(value or ""), flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "", text)
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    text = re.sub(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\x27[^\x27\n]+\x27(?!\w)', "", text)
    text = clean_narrative(text, workspace, limit=12000)
    request = None
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if RESOLVED_REQUEST_RE.search(sentence):
            request = None
            continue
        match = USER_REQUEST_RE.search(sentence)
        if match and not NON_CURRENT_REQUEST_RE.search(sentence.replace("’", "'")):
            request = {"kind": match.group("kind").lower(), "excerpt": clean_inline(sentence, 320)}
    return request


def observation_block(value: str, workspace: Path) -> Optional[Dict[str, Any]]:
    text = clean_narrative(value, workspace)
    if not text:
        return None
    lowered = text.lower()
    structured_markers = sum(
        marker in lowered
        for marker in (
            '"questionscores"',
            '"questionid"',
            '"normalizedscore"',
            '"rawvalue"',
            '"reasoning"',
            '"evidence"',
        )
    )
    if structured_markers >= 2:
        summary = "Received grading results" if "normalizedscore" in lowered else "Received structured results"
        return {"summary": summary, "detail": None}
    summary = summarize_narrative(text)
    detail = text if summary != text else None
    return {"summary": summary, "detail": detail, "userRequest": explicit_user_request(value, workspace)}


def user_action_attention(
    state: str, turns: List[Dict[str, Any]], pending_messages: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Surface a completed turn's final request without changing engine lifecycle."""
    if state.lower() not in {"idle", "paused"} or not turns or pending_messages:
        return None
    latest = turns[-1]
    if (
        not latest.get("endedAt") or latest.get("endReason") == "steering"
        or latest.get("pendingActionCount") or latest.get("failedActionCount")
    ):
        return None
    sleep = latest.get("sleep") or {}
    until = parse_time(sleep.get("until"))
    if sleep.get("status") == "pending" or (
        sleep.get("status") == "scheduled" and (until is None or until > datetime.now(timezone.utc))
    ):
        return None
    timeline = [entry for entry in latest.get("timeline", []) if entry.get("type") != "sleep"]
    observation = timeline[-1] if timeline else {}
    request = observation.get("userRequest")
    if observation.get("type") != "observation" or not request:
        return None
    kind = request["kind"]
    return {
        "kind": kind,
        "label": "Needs your action",
        "title": "Ready for your review" if kind == "review" else "Waiting for your {}".format(kind),
        "description": (
            "This turn is finished. Review the results, then approve or request changes in Discovery."
            if kind in {"approval", "review"}
            else "This turn is finished. Read the latest update and provide the requested input in Discovery."
        ),
        "excerpt": request["excerpt"],
        "source": "Latest observation",
        "turnId": latest["id"],
        "entryId": observation["id"],
        "requestedAt": observation.get("startedAt"),
    }


def turn_intro(number: int, timeline: List[Dict[str, Any]], after_steering: bool = False) -> str:
    first_action = next(iter_actions(timeline), None)
    if after_steering:
        if first_action and first_action.get("intent") == "context":
            return "Gathering context after your steering message."
        return "Continuing with your steering message."
    if number == 1:
        return "Waking up. Getting the lay of the land."
    first_summary = str(first_action.get("summary", "")).lower() if first_action else ""
    if any(word in first_summary for word in ("validat", "evaluat", "audit", "checking results")):
        return "Back awake. Checking how things landed."
    if any(word in first_summary for word in ("reading", "reviewing", "getting", "searching", "checking")):
        return "Back awake. Checking what changed."
    return "Waking up. Picking up where things left off."


class EngineRunParser:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def parse(self, run_dir: Path, meta: Dict[str, Any], signature: Tuple[int, int, int, int]) -> Dict[str, Any]:
        output_path = run_dir / "output.jsonl"
        turns: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        pending: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        text_kind: Optional[str] = None
        text_parts: List[str] = []
        text_timestamp: Optional[str] = None
        text_sequence: Optional[Any] = None
        pending_steering: List[Dict[str, Any]] = []
        last_timestamp: Optional[str] = None
        malformed_lines = 0

        def ensure_turn(timestamp: Optional[str]) -> Dict[str, Any]:
            nonlocal current, pending, pending_steering
            if current is None:
                number = len(turns) + 1
                current = {
                    "id": "turn-{}".format(number),
                    "number": number,
                    "startedAt": timestamp,
                    "endedAt": None,
                    "endReason": None,
                    "startReason": "steering" if (
                        any(message.get("delivery") == "applied" for message in pending_steering)
                        or (turns and turns[-1].get("endReason") == "steering")
                    ) else None,
                    "timeline": [],
                    "sleep": None,
                    "queuedMessagesBefore": pending_steering,
                }
                turns.append(current)
                pending = defaultdict(deque)
                pending_steering = []
            return current

        def flush_text() -> None:
            nonlocal text_kind, text_parts, text_timestamp, text_sequence
            if not text_kind or not text_parts or current is None:
                text_kind = None
                text_parts = []
                text_timestamp = None
                text_sequence = None
                return
            joined = "".join(text_parts)
            if text_kind == "Thinking":
                block = thought_block(joined, self.workspace)
                if block:
                    current["timeline"].append(
                        {
                            "id": "thinking-{}".format(text_sequence or len(current["timeline"]) + 1),
                            "type": "thinking",
                            "summary": block["summary"],
                            "detail": block["detail"],
                            "detailLabel": "What the engine was considering",
                            "startedAt": text_timestamp,
                        }
                    )
            else:
                block = observation_block(joined, self.workspace)
                if block:
                    current["timeline"].append(
                        {
                            "id": "observation-{}".format(text_sequence or len(current["timeline"]) + 1),
                            "type": "observation",
                            "summary": block["summary"],
                            "detail": block["detail"],
                            "detailLabel": "Observation",
                            "userRequest": block.get("userRequest"),
                            "startedAt": text_timestamp,
                        }
                    )
            text_kind = None
            text_parts = []
            text_timestamp = None
            text_sequence = None

        def find_pending(title: str) -> Optional[Dict[str, Any]]:
            key = tool_key(title)
            if pending.get(key):
                return pending[key].popleft()
            for pending_key, queue in list(pending.items()):
                if queue and (pending_key in key or key in pending_key):
                    return queue.popleft()
            return None

        def interrupt_turn(timestamp: Optional[str], sequence: Any) -> None:
            nonlocal current, pending
            # The applied message and the restart notice describe one boundary.
            # Neither should create an empty turn while awaiting the next event.
            if current is None:
                return
            for action in iter_actions(current["timeline"]):
                if action.get("status") == "pending":
                    action["status"] = "interrupted"
                    action["finishedAt"] = timestamp
            sleep = current.get("sleep")
            if sleep is not None:
                if sleep.get("status") in {"pending", "scheduled"}:
                    sleep["status"] = "interrupted"
                if sleep not in current["timeline"]:
                    current["timeline"].append(sleep)
            current["timeline"].append(
                {
                    "id": "steering-received-{}".format(sequence),
                    "type": "steering",
                    "summary": "Received steering message",
                    "startedAt": timestamp,
                }
            )
            current["endedAt"] = timestamp
            current["endReason"] = "steering"
            current = None
            pending = defaultdict(deque)

        if output_path.exists():
            parsed_events: List[Dict[str, Any]] = []
            with output_path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        malformed_lines += 1
                        continue
                    if not isinstance(event, dict):
                        continue
                    parsed_events.append(event)

            for event in coalesce_streaming_text_events(parsed_events):
                kind = str(event.get("kind", ""))
                timestamp = event.get("timestamp")
                if isinstance(timestamp, str):
                    last_timestamp = timestamp

                if kind in {"Thinking", "Observation"}:
                    ensure_turn(timestamp)
                    if text_kind and text_kind != kind:
                        flush_text()
                    if text_kind is None:
                        text_timestamp = timestamp
                        text_sequence = event.get("sequence")
                    text_kind = kind
                    text_parts.append(str(event.get("content", "")))
                    continue

                flush_text()

                if kind == "Progress":
                    content = str(event.get("content") or "").strip()
                    steering = STEERING_RE.match(content)
                    if steering:
                        delivery = steering.group("delivery").lower()
                        steering_text = extract_steering_message(content, self.workspace)
                        if steering_text:
                            # A queued message may later be applied. Update its
                            # bubble instead of showing the same message twice.
                            queued = next(
                                (message for message in pending_steering
                                 if message.get("delivery") == "queued" and message["text"] == steering_text),
                                None,
                            ) if delivery == "applied" else None
                            if queued is not None:
                                queued["delivery"] = "applied"
                                queued["receivedAt"] = timestamp
                            else:
                                pending_steering.append(
                                    {
                                        "id": "steering-{}".format(event.get("sequence", len(pending_steering) + 1)),
                                        "text": steering_text,
                                        "queuedAt": timestamp,
                                        "receivedAt": timestamp if delivery == "applied" else None,
                                        "delivery": delivery,
                                    }
                                )
                        if delivery == "applied":
                            interrupt_turn(timestamp, event.get("sequence"))
                        continue
                    if STEERING_INTERRUPT_RE.match(content):
                        interrupt_turn(timestamp, event.get("sequence"))
                        continue

                if kind == "Done":
                    if current is not None:
                        current["endedAt"] = timestamp
                        sleep = current.get("sleep")
                        if sleep is not None and sleep not in current["timeline"]:
                            current["timeline"].append(sleep)
                    current = None
                    pending = defaultdict(deque)
                    continue

                # Late tool acknowledgements from a cancelled turn must not
                # consume the steering bubble or manufacture a new empty turn.
                if kind == "ActionApplied" and current is None:
                    continue

                turn = ensure_turn(timestamp)
                content = event.get("content", "")

                if kind == "ActionProposed":
                    title = markdown_title(content)
                    raw_input = proposed_input(content)
                    if "engine-sleep" in title.lower():
                        try:
                            minutes = int(raw_input.get("minutes"))
                        except (TypeError, ValueError):
                            minutes = None
                        turn["sleep"] = {
                            "id": "sleep-{}".format(event.get("sequence", len(turn["timeline"]) + 1)),
                            "type": "sleep",
                            "durationSeconds": minutes * 60 if minutes is not None else None,
                            "until": None,
                            "reason": clean_inline(raw_input.get("reason"), 420),
                            "status": "pending",
                            "startedAt": timestamp,
                        }
                        pending[tool_key(title)].append({"sleep": turn["sleep"]})
                        continue
                    summary = summarize_action(title, raw_input, self.workspace)
                    detail_label, detail = action_detail(title, raw_input, self.workspace)
                    if detail_repeats_summary(summary, detail):
                        detail_label, detail = None, None
                    profile = action_profile(title, raw_input, summary, self.workspace)
                    action = {
                        "id": "action-{}".format(event.get("sequence", len(turn["timeline"]) + 1)),
                        "type": "action",
                        "summary": summary,
                        "detail": detail,
                        "detailLabel": detail_label,
                        "status": "pending",
                        "startedAt": timestamp,
                        "finishedAt": None,
                        **profile,
                    }
                    turn["timeline"].append(action)
                    pending[tool_key(title)].append(action)
                    continue

                if kind == "ActionApplied":
                    title = markdown_title(content)
                    match = find_pending(title)
                    if match and "sleep" in match:
                        sleep = match["sleep"]
                        until, minutes = extract_sleep_result(content)
                        if until:
                            sleep["until"] = until
                        if minutes is not None:
                            sleep["durationSeconds"] = minutes * 60
                        sleep["status"] = "failed" if applied_failed(content) else "scheduled"
                    elif match:
                        if applied_failed(content):
                            match["status"] = "failed"
                        elif match.get("resource") and applied_needs_followup(content):
                            match["status"] = "followup"
                        else:
                            match["status"] = "completed"
                        match["finishedAt"] = timestamp
                    continue

                if kind == "Error":
                    failure_text = str(content or "")
                    failed_title_match = TOOL_FAILURE_RE.search(failure_text)
                    failed_title = failed_title_match.group(1) if failed_title_match else ""
                    match = find_pending(failed_title) if failed_title else None
                    if match and "sleep" in match:
                        match["sleep"]["status"] = "failed"
                    elif match:
                        match["status"] = "failed"
                        match["finishedAt"] = timestamp
                    else:
                        summary = "Something needs attention"
                        if failed_title:
                            summary = "Couldn’t complete: {}".format(
                                summarize_action(failed_title, {}, self.workspace)
                            )
                        turn["timeline"].append(
                            {
                                "id": "error-{}".format(event.get("sequence", len(turn["timeline"]) + 1)),
                                "type": "action",
                                "summary": clean_inline(summary, 180),
                                "detail": "The action did not complete. Technical payloads are intentionally hidden in this view.",
                                "detailLabel": "What happened",
                                "status": "failed",
                                "startedAt": timestamp,
                                "finishedAt": timestamp,
                                "intent": "work",
                                "visual": "default",
                                "scope": None,
                                "resource": None,
                                "resourceLabel": None,
                                "readRange": None,
                                "dedupeKey": "error:{}".format(event.get("sequence", "unknown")),
                            }
                        )
                    continue

                if kind == "CheckpointCreated":
                    checkpoint = None
                    for queue in pending.values():
                        for candidate in queue:
                            if candidate.get("summary") == "Saving a checkpoint":
                                checkpoint = candidate
                                break
                    if checkpoint is None:
                        turn["timeline"].append(
                            {
                                "id": "checkpoint-{}".format(event.get("sequence", len(turn["timeline"]) + 1)),
                                "type": "action",
                                "summary": "Saved a checkpoint",
                                "detail": None,
                                "detailLabel": None,
                                "status": "completed",
                                "startedAt": timestamp,
                                "finishedAt": timestamp,
                                "intent": "work",
                                "visual": "default",
                                "scope": None,
                                "resource": None,
                                "resourceLabel": None,
                                "readRange": None,
                                "dedupeKey": "checkpoint:{}".format(event.get("sequence", "unknown")),
                            }
                        )
                    continue

                if kind == "Progress":
                    message = str(content or "")
                    if message.lower().startswith("communication applied on config:"):
                        summary = "Updated engine settings"
                    else:
                        summary = clean_inline(message, 180) or "Made progress"
                    turn["timeline"].append(
                        {
                            "id": "progress-{}".format(event.get("sequence", len(turn["timeline"]) + 1)),
                            "type": "action",
                            "summary": summary,
                            "detail": None,
                            "detailLabel": None,
                            "status": "completed",
                            "startedAt": timestamp,
                            "finishedAt": timestamp,
                            "intent": "work",
                            "visual": "default",
                            "scope": None,
                            "resource": None,
                            "resourceLabel": None,
                            "readRange": None,
                            "dedupeKey": "progress:{}".format(event.get("sequence", "unknown")),
                        }
                    )

        flush_text()

        for turn in turns:
            sleep = turn.get("sleep")
            if sleep is not None and sleep not in turn["timeline"]:
                turn["timeline"].append(sleep)
            turn["timeline"] = merge_adjacent_actions(turn["timeline"])
            label_read_revisits(turn["timeline"])
            turn["timeline"] = roll_up_context(turn["timeline"])
            actions = list(iter_actions(turn["timeline"]))
            turn["intro"] = turn_intro(turn["number"], turn["timeline"], turn["startReason"] == "steering")
            turn["summary"] = concise_turn_summary(turn["timeline"])
            if turn["endReason"] == "steering" and turn["summary"] == "Turn complete":
                turn["summary"] = "Received steering message"
            turn["actionCount"] = sum(int(action.get("mergedCount", 1)) for action in actions)
            turn["completedActionCount"] = sum(
                int(action.get("mergedCount", 1))
                for action in actions
                if action["status"] == "completed"
            )
            turn["failedActionCount"] = sum(1 for action in actions if action["status"] == "failed")
            turn["pendingActionCount"] = sum(1 for action in actions if action["status"] == "pending")
            strip_internal_fields(turn["timeline"])

        state = str(meta.get("state", "Unknown"))
        all_actions = [action for turn in turns for action in iter_actions(turn["timeline"])]
        all_action_count = sum(int(action.get("mergedCount", 1)) for action in all_actions)
        all_steering = [message for turn in turns for message in turn["queuedMessagesBefore"]] + pending_steering
        updated_at = last_timestamp or meta.get("completedAt") or meta.get("startedAt")
        run = {
            "definitionId": str(meta.get("definitionId") or run_dir.parent.name),
            "displayName": display_name(str(meta.get("definitionId") or run_dir.parent.name)),
            "instanceId": str(meta.get("instanceId") or run_dir.name),
            "shortId": str(meta.get("instanceId") or run_dir.name)[:8],
            "state": state,
            "stateLabel": state_label(state),
            "isLive": is_live_state(state, updated_at),
            "startedAt": meta.get("startedAt"),
            "completedAt": meta.get("completedAt"),
            "updatedAt": updated_at,
            "version": "{}-{}-{}-{}".format(*signature),
        }
        return {
            "run": run,
            "startingPrompt": extract_starting_prompt(meta.get("prompt"), self.workspace),
            "queuedMessages": pending_steering,
            "stats": {
                "turns": len(turns),
                "actions": all_action_count,
                "completedActions": sum(
                    int(action.get("mergedCount", 1))
                    for action in all_actions
                    if action["status"] == "completed"
                ),
                "failedActions": sum(1 for action in all_actions if action["status"] == "failed"),
                "queuedMessages": sum(1 for message in all_steering if message["delivery"] == "queued"),
                "steeringMessages": len(all_steering),
                "malformedLinesIgnored": malformed_lines,
            },
            "turns": turns,
        }


def valid_run_id(value: str) -> bool:
    return bool(value not in {".", ".."} and RUN_ID_RE.fullmatch(value))


class EngineRunRepository:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.runs_root = self.workspace / ".discovery" / "engine-runs"
        self.sessions_root = self.workspace / ".discovery" / "sessions"
        self.agent_runs_root = self.workspace / ".discovery" / "agent-runs"
        self.tasks_index_path = self.workspace / ".discovery" / "tasks" / "index.json"
        self.parser = EngineRunParser(self.workspace)
        self._cache: Dict[Tuple[str, str], Tuple[Tuple[int, int, int, int], Dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def _safe_run_dir(self, definition_id: str, instance_id: str) -> Optional[Path]:
        if not valid_run_id(definition_id) or not valid_run_id(instance_id):
            return None
        root = self.runs_root.resolve()
        candidate = (self.runs_root / definition_id / instance_id).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        if len(relative.parts) != 2:
            return None
        return candidate

    def _run_dirs(self) -> Iterable[Path]:
        if not self.runs_root.is_dir():
            return []
        safe_dirs: List[Path] = []
        for definition_dir in self.runs_root.iterdir():
            if not definition_dir.is_dir() or not valid_run_id(definition_dir.name):
                continue
            for run_dir in definition_dir.iterdir():
                candidate = self._safe_run_dir(definition_dir.name, run_dir.name)
                if candidate is None:
                    continue
                if candidate.is_dir() and (candidate / "meta.json").is_file():
                    safe_dirs.append(candidate)
        return safe_dirs

    @staticmethod
    def _read_meta(run_dir: Path) -> Optional[Dict[str, Any]]:
        try:
            with (run_dir / "meta.json").open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    def _task_lookup(self) -> Dict[str, Dict[str, Any]]:
        payload = self._read_json(self.tasks_index_path) or {}
        lookup: Dict[str, Dict[str, Any]] = {}
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return lookup
        for task in tasks:
            if not isinstance(task, dict):
                continue
            for key in (task.get("taskId"), task.get("dxId")):
                if key:
                    lookup[str(key)] = task
        return lookup

    def _current_attention(self, parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        attention = user_action_attention(parsed["run"]["state"], parsed["turns"], parsed["queuedMessages"])
        if not attention or attention["kind"] not in {"approval", "review"}:
            return attention

        # The final request may name a subtree. Otherwise use tasks the run
        # actually changed or delegated, never every ready task in the project.
        references = collect_task_ids(attention["excerpt"]) or list({
            reference
            for turn in parsed["turns"] for action in iter_actions(turn["timeline"])
            if action.get("status") == "completed"
            for reference in action.get("taskRefs", [])
        })
        if not references:
            # A general request (e.g. review a design) has no task-board truth.
            # Keep its provenance explicit rather than inventing a task count.
            return attention

        lookup = {key.lower(): task for key, task in self._task_lookup().items()}
        tasks = {str(task["taskId"]): task for task in lookup.values() if task.get("taskId")}
        children: Dict[str, List[str]] = defaultdict(list)
        for task_id, task in tasks.items():
            parent = lookup.get(str(task.get("parentId", "")).lower(), {})
            if parent.get("taskId"):
                children[str(parent["taskId"])].append(task_id)
        pending = [str(lookup[ref.lower()]["taskId"]) for ref in references
                   if lookup.get(ref.lower(), {}).get("taskId")]
        scoped: set[str] = set()
        while pending:
            task_id = pending.pop()
            if task_id in scoped:
                continue
            scoped.add(task_id)
            pending.extend(children.get(task_id, []))
        ready = sorted(
            (tasks[task_id] for task_id in scoped
             if str(tasks[task_id].get("status", "")).lower() in {"executiondone", "flaggedhuman"}),
            key=lambda task: str(task.get("dxId") or task["taskId"]),
        )
        # Missing/unreadable task data is unknown, not evidence that an old
        # approval request is still open. Resolved requests disappear here too.
        if not ready:
            return None
        current_tasks = [
            {
                "id": str(task.get("dxId") or task["taskId"]),
                "title": clean_inline(task.get("title"), 140),
                "status": str(task["status"]),
            }
            for task in ready
        ]
        count = len(current_tasks)
        excerpt = "\n".join("{} — {}".format(task["id"], task["title"]) for task in current_tasks[:3])
        if count > 3:
            excerpt += "\n+ {} more".format(count - 3)
        return {
            **attention,
            "title": "1 task needs your review" if count == 1 else "{} tasks need your review".format(count),
            "description": "This turn is finished. Review the current tasks in Discovery, then approve or request changes.",
            "excerpt": excerpt,
            "source": "Current task board",
            "tasks": current_tasks,
        }

    def _present_run(self, parsed: Dict[str, Any], signature: Tuple[int, int, int, int]) -> Dict[str, Any]:
        """Refresh handoffs even when only task state or a sleep deadline changed."""
        run = parsed["run"]
        attention = self._current_attention(parsed)
        live = is_live_state(run.get("state"), run.get("updatedAt"))
        revision = hashlib.sha256(json.dumps([attention, live], sort_keys=True).encode("utf-8")).hexdigest()[:16]
        # Return a snapshot; concurrent responses must not mutate cached data.
        return {
            **parsed,
            "run": {**run, "attention": attention, "isLive": live,
                    "version": "{}-{}".format("-".join(map(str, signature)), revision)},
        }

    def list_active_agents(self) -> Dict[str, Any]:
        agents: List[Dict[str, Any]] = []
        tasks = self._task_lookup()
        if not self.sessions_root.is_dir():
            return {"agents": [], "count": 0, "updatedAt": datetime.now(timezone.utc).isoformat()}

        for session_path in self.sessions_root.glob("*.json"):
            session = self._read_json(session_path)
            if not session:
                continue
            header = session.get("header") if isinstance(session.get("header"), dict) else session
            status = header.get("status")
            if normalized_agent_state(status) not in ACTIVE_AGENT_STATES:
                continue

            session_id = str(header.get("sessionId") or header.get("legacyRunId") or session_path.stem)
            run = self._read_json(self.agent_runs_root / "{}.json".format(session_id)) or {}
            agent_id = str(header.get("agentId") or run.get("agentId") or "")
            task_reference = header.get("taskId") or run.get("taskId")
            task = tasks.get(str(task_reference), {}) if task_reference else {}
            task_id = task.get("dxId") or task_reference
            conversation_name = run.get("conversationName") or ""
            task_title = task.get("title") or ""
            started_at = header.get("startedAt") or run.get("startedAt")
            last_activity_at = header.get("lastActivityAt") or started_at
            agents.append(
                {
                    "runId": session_id,
                    "shortId": session_id[:8],
                    "agentId": agent_id,
                    "displayName": agent_display_name(agent_id),
                    "taskId": task_id,
                    "taskTitle": clean_inline(task_title, 140),
                    "conversationName": clean_inline(conversation_name, 160),
                    "workingOn": agent_work_label(agent_id, task_id, task_title, conversation_name),
                    "status": str(status or "Active"),
                    "statusLabel": agent_state_label(status),
                    "startedAt": started_at,
                    "lastActivityAt": last_activity_at,
                }
            )

        agents.sort(
            key=lambda item: parse_time(item.get("startedAt")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return {
            "agents": agents,
            "count": len(agents),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _signature(run_dir: Path) -> Tuple[int, int, int, int]:
        meta_stat = (run_dir / "meta.json").stat()
        output_path = run_dir / "output.jsonl"
        if output_path.exists():
            output_stat = output_path.stat()
            return output_stat.st_size, output_stat.st_mtime_ns, meta_stat.st_size, meta_stat.st_mtime_ns
        return 0, 0, meta_stat.st_size, meta_stat.st_mtime_ns

    def list_runs(self) -> Dict[str, Any]:
        runs: List[Dict[str, Any]] = []
        for run_dir in self._run_dirs():
            meta = self._read_meta(run_dir)
            if not meta:
                continue
            output_path = run_dir / "output.jsonl"
            try:
                updated_at = iso_from_stat(output_path.stat().st_mtime if output_path.exists() else (run_dir / "meta.json").stat().st_mtime)
            except OSError:
                continue
            state = str(meta.get("state", "Unknown"))
            definition_id = str(meta.get("definitionId") or run_dir.parent.name)
            instance_id = str(meta.get("instanceId") or run_dir.name)
            runs.append(
                {
                    "definitionId": definition_id,
                    "displayName": display_name(definition_id),
                    "instanceId": instance_id,
                    "shortId": instance_id[:8],
                    "state": state,
                    "stateLabel": state_label(state),
                    "isLive": is_live_state(state, updated_at),
                    "startedAt": meta.get("startedAt"),
                    "completedAt": meta.get("completedAt"),
                    "updatedAt": updated_at,
                }
            )
        runs.sort(key=lambda item: parse_time(item.get("updatedAt")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        recommended = next((run for run in runs if run["isLive"]), runs[0] if runs else None)
        return {
            "runs": runs[:50],
            "recommended": {
                "definitionId": recommended["definitionId"],
                "instanceId": recommended["instanceId"],
            }
            if recommended
            else None,
        }

    def get_run(self, definition_id: str, instance_id: str) -> Optional[Dict[str, Any]]:
        run_dir = self._safe_run_dir(definition_id, instance_id)
        if run_dir is None:
            return None
        if not run_dir.is_dir() or not (run_dir / "meta.json").is_file():
            return None
        try:
            signature = self._signature(run_dir)
        except OSError:
            return None
        key = (definition_id, instance_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == signature:
                return self._present_run(cached[1], signature)
            meta = self._read_meta(run_dir)
            if not meta:
                return None
            parsed = self.parser.parse(run_dir, meta, signature)
            self._cache[key] = (signature, parsed)
            return self._present_run(parsed, signature)


class EngineChatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class EngineChatHandler(BaseHTTPRequestHandler):
    repository: EngineRunRepository
    index_path: Path
    started_at: str
    server_version = "EngineChat"
    sys_version = ""

    def end_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "serviceId": SERVICE_ID,
                    "engineChatVersion": ENGINE_CHAT_VERSION,
                    "protocolVersion": PROTOCOL_VERSION,
                    "testedDiscoveryVersion": TESTED_DISCOVERY_VERSION,
                    "testedOperatingSystem": TESTED_OS,
                    "compatibilityStatement": (
                        "Tested with Discovery {} on {}; other versions and platforms are untested."
                    ).format(TESTED_DISCOVERY_VERSION, TESTED_OS),
                    "workspaceFingerprint": workspace_fingerprint(self.repository.workspace),
                    "pid": os.getpid(),
                    "startedAt": self.started_at,
                }
            )
            return
        if path == "/api/runs":
            self.send_json(self.repository.list_runs())
            return
        if path == "/api/agents":
            self.send_json(self.repository.list_active_agents())
            return
        if path.startswith("/api/runs/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                payload = self.repository.get_run(parts[2], parts[3])
                if payload is not None:
                    self.send_json(payload)
                    return
            self.send_json({"error": "Run not found"}, status=404)
            return
        if path in {"/", "/index.html"}:
            try:
                content = self.index_path.read_bytes()
            except OSError:
                self.send_error(500, "Engine Chat HTML is unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_error(404, "Not found")

    def send_json(self, payload: Any, status: int = 200) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format_string: str, *args: Any) -> None:
        status = str(args[1]) if len(args) > 1 else ""
        if status.startswith(("4", "5")):
            super().log_message(format_string, *args)


def workspace_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def runtime_state_payload(workspace: Path, port: int) -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "serviceId": SERVICE_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "engineChatVersion": ENGINE_CHAT_VERSION,
        "workspaceFingerprint": workspace_fingerprint(workspace),
        "pid": os.getpid(),
        "host": LOOPBACK_HOST,
        "port": port,
        "url": "http://{}:{}/".format(LOOPBACK_HOST, port),
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }


def validate_state_file(workspace: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    candidate = Path(value).expanduser().resolve()
    allowed_root = (workspace / ".discovery" / "engine-chat-experimental").resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError as error:
        raise ValueError("Runtime state file must stay under {}".format(allowed_root)) from error
    return candidate


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.{}.tmp".format(path.name, os.getpid()))
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def remove_owned_state(path: Optional[Path], workspace: Path) -> None:
    if path is None:
        return
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return
    if (
        isinstance(payload, dict)
        and payload.get("serviceId") == SERVICE_ID
        and payload.get("workspaceFingerprint") == workspace_fingerprint(workspace)
        and payload.get("pid") == os.getpid()
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def create_server(workspace: Path, port: int, index_path: Optional[Path] = None) -> EngineChatHTTPServer:
    workspace = workspace.resolve()
    if not (workspace / ".discovery").is_dir():
        raise ValueError("Workspace must contain a .discovery directory: {}".format(workspace))
    repository = EngineRunRepository(workspace)
    attributes = {
        "repository": repository,
        "index_path": (index_path or (Path(__file__).resolve().parent / "index.html")).resolve(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    handler = type("BoundEngineChatHandler", (EngineChatHandler,), attributes)
    return EngineChatHTTPServer((LOOPBACK_HOST, port), handler)


class TerminationRequested(Exception):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Serve Engine Chat 0.1.0-experimental on loopback. Tested with Discovery 0.15.21 "
            "on macOS; other versions and platforms are untested."
        )
    )
    parser.add_argument("--workspace", required=True, help="Workspace containing .discovery")
    parser.add_argument("--port", default=8765, type=int, help="Loopback port (default: 8765)")
    parser.add_argument("--state-file", help="Owned runtime-state path under .discovery/engine-chat-experimental")
    args = parser.parse_args()

    workspace = workspace_path(args.workspace)
    if not (workspace / ".discovery").is_dir():
        parser.error("No .discovery directory found under {}".format(workspace))
    if args.port < 1 or args.port > 65535:
        parser.error("Port must be between 1 and 65535")
    try:
        state_file = validate_state_file(workspace, args.state_file)
    except ValueError as error:
        parser.error(str(error))

    server = create_server(workspace, args.port)
    actual_port = int(server.server_address[1])
    if state_file is not None:
        atomic_write_json(state_file, runtime_state_payload(workspace, actual_port))
    url = "http://{}:{}/".format(LOOPBACK_HOST, actual_port)
    print(
        "ENGINE_CHAT_RUNTIME="
        + json.dumps(
            {
                "ok": True,
                "serviceId": SERVICE_ID,
                "engineChatVersion": ENGINE_CHAT_VERSION,
                "protocolVersion": PROTOCOL_VERSION,
                "url": url,
                "pid": os.getpid(),
                "workspaceFingerprint": workspace_fingerprint(workspace),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    print("Reading local Discovery logs. Press Ctrl+C to stop.", flush=True)

    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def request_termination(signum: int, frame: Any) -> None:
        raise TerminationRequested()

    signal.signal(signal.SIGTERM, request_termination)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, TerminationRequested):
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        server.server_close()
        remove_owned_state(state_file, workspace)


if __name__ == "__main__":
    main()
