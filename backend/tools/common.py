"""What every tool shares: the tool record, strict arguments, validation,
the read-only transaction, and row formatting. Tools take arguments only
through `validate`, which refuses anything the tool's model doesn't name."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, ValidationError

from db.access import set_access


class ToolError(Exception):
    """A refused call: the message is safe to show (it names no other customer's data)."""


class AccessRefused(ToolError):
    """The caller may not use this tool at all; asking again another way won't help."""


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # for the planner (Phase 6)
    args: type[Args]
    run: Callable


def invalid_arguments(name: str, exc: ValidationError) -> ToolError:
    """A refused call's error, naming each bad argument and why."""
    problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors())
    return ToolError(f"Invalid arguments for {name}: {problems}")


def validate(tools: dict[str, Tool], name: str, arguments: dict | None) -> tuple[Tool, Args]:
    tool = tools.get(name)
    if tool is None:
        raise ToolError(f"No such tool: {name!r}.")
    try:
        return tool, tool.args.model_validate(arguments or {})
    except ValidationError as exc:
        raise invalid_arguments(name, exc) from None


def langchain_tool(tool: Tool, run: Callable[[dict], list[dict]]):
    """`tool` as a LangChain StructuredTool: its strict Pydantic model is the
    input schema (an unknown argument, say customer_id, is refused before
    anything runs), and `run(arguments)` runs it as the caller, who is bound
    when the tool is made and is never an argument. The owner's call()
    validates again and runs its parameterized SQL in a read-only
    transaction bound to the caller (RLS), as before."""
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(func=lambda **arguments: run(arguments), name=tool.name,
                                        description=tool.description, args_schema=tool.args)


def run_langchain_tool(tool, arguments: dict):
    """Invoke a LangChain tool, refusing bad arguments with our own ToolError."""
    try:
        return tool.invoke(arguments, config={"run_name": f"tool:{tool.name}"})
    except ValidationError as exc:
        raise invalid_arguments(tool.name, exc) from None


@contextmanager
def read_only(engine: sa.Engine, principal=None):
    """A READ ONLY transaction, bound to the caller when there is one: a write
    fails in Postgres even if a tool ever tried one."""
    with engine.begin() as conn:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))  # must come before any query
        if principal is not None:
            set_access(conn, principal)
        yield conn


def rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    """Query results as plain values: dates as ISO strings, decimals as strings
    (exact; no float rounding)."""
    out = []
    for row in conn.execute(sa.text(sql), params or {}).mappings():
        out.append({k: v.isoformat() if isinstance(v, date) else str(v) if isinstance(v, Decimal) else v
                    for k, v in row.items()})
    return out
