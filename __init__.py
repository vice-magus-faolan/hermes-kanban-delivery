# SPDX-License-Identifier: GPL-3.0-or-later
"""Native registration only; both query surfaces call Queries.query."""
from __future__ import annotations

import json

from .delivery.service import Queries


DESCRIPTIONS = {
    "delivery_policy_check": "Observe the explicitly selected board's v2 delivery policy. No grants or effects.",
    "delivery_context": "Observe bounded native phase, artifact and review facts for one selected-board task. No effect clearance.",
}


def schema(name: str) -> dict:
    properties = {"board": {"type": "string", "description": "Literal board slug; required outside a verified worker."}}
    if name == "delivery_context":
        properties["task_id"] = {"type": "string", "description": "Literal task identifier; defaults only to verified worker's own task."}
    return {"name": name, "description": DESCRIPTIONS[name],
            "parameters": {"type": "object", "properties": properties, "additionalProperties": False}}


def setup_cli(parser) -> None:
    commands = parser.add_subparsers(dest="delivery_operation", required=True)
    for operation in ("policy-check", "context"):
        command = commands.add_parser(operation)
        command.add_argument("--board")
        if operation == "context":
            command.add_argument("--task-id")


def register(ctx) -> None:
    queries = Queries(ctx)
    for name in DESCRIPTIONS:
        ctx.register_tool(name=name, toolset="kanban_delivery", schema=schema(name), handler=queries.tool(name))

    def cli(args) -> None:
        name = "delivery_policy_check" if args.delivery_operation == "policy-check" else "delivery_context"
        params = {"board": args.board} if args.board is not None else {}
        if name == "delivery_context" and args.task_id is not None:
            params["task_id"] = args.task_id
        result = queries.query(name, params)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(1)

    ctx.register_cli_command(name="delivery", help="Read-only native delivery observations",
                             setup_fn=setup_cli, handler_fn=cli)
