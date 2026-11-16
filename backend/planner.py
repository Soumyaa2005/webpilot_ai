"""Structured task planner: Intent -> TaskPlan using only approved steps."""
from __future__ import annotations

from backend.models import STEP_ORDER, Intent, TaskPlan


def build_plan(intent: Intent) -> TaskPlan:
    output_format = []
    if intent.summary_required:
        output_format.append("summary")
    if intent.json_required:
        output_format.append("json")
    if not output_format:
        output_format = ["summary", "json"]
    # The pipeline is fixed and canonical; the model can never add arbitrary steps.
    return TaskPlan(task_type=intent.task_type, url=intent.url, steps=list(STEP_ORDER),
                    output_format=output_format, max_pages=intent.max_pages, fields=intent.fields)
