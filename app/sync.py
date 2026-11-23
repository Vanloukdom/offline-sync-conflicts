"""Synchronisation hors ligne : détection de conflits par version, sans écrasement silencieux."""
from datetime import datetime
from typing import Literal

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.db import pool


class SetAmount(BaseModel):
    type: Literal["set_amount"]
    op_id: str
    budget_id: str
    amount_cents: int
    base_version: int  # version que le téléphone avait au moment de la modification


class AddExpense(BaseModel):
    type: Literal["add_expense"]
    op_id: str
    budget_id: str
    amount_cents: int = Field(gt=0)
    label: str | None = None
    occurred_at: datetime


Operation = SetAmount | AddExpense


def create_budget(user_id: str, name: str, amount: int) -> dict:
    with pool.connection() as conn:
        b = conn.execute(
            "INSERT INTO budgets (user_id, name, amount_cents) VALUES (%s, %s, %s) RETURNING *",
            (user_id, name, amount),
        ).fetchone()
        conn.execute(
            "INSERT INTO budget_versions VALUES (%s, 1, %s, NULL, 'created')", (b["id"], amount)
        )
    return _public(b)


def get_budget(budget_id: str) -> dict | None:
    with pool.connection() as conn:
        b = conn.execute("SELECT * FROM budgets WHERE id = %s", (budget_id,)).fetchone()
        if not b:
            return None
        spent = conn.execute(
            "SELECT COALESCE(sum(amount_cents), 0) AS s FROM expenses WHERE budget_id = %s", (budget_id,)
        ).fetchone()["s"]
        open_conflicts = conn.execute(
            "SELECT id, server_value, client_value, device_id FROM conflicts WHERE budget_id = %s AND status = 'OPEN'",
            (budget_id,),
        ).fetchall()
    return {**_public(b), "spent_cents": spent,
            "open_conflicts": [{**c, "id": str(c["id"])} for c in open_conflicts]}


def _public(b: dict) -> dict:
    return {"id": str(b["id"]), "name": b["name"], "amount_cents": b["amount_cents"], "version": b["version"]}


def apply(op: Operation, device_id: str) -> dict:
    with pool.connection() as conn:
        done = conn.execute("SELECT result FROM applied_ops WHERE op_id = %s", (op.op_id,)).fetchone()
        if done:
            return {**done["result"], "replayed": True}
        result = _set_amount(conn, op, device_id) if isinstance(op, SetAmount) else _add_expense(conn, op, device_id)
        conn.execute("INSERT INTO applied_ops (op_id, result) VALUES (%s, %s)", (op.op_id, Jsonb(result)))
        return {**result, "replayed": False}


def _set_amount(conn, op: SetAmount, device_id: str) -> dict:
    updated = conn.execute(
        """
        UPDATE budgets SET amount_cents = %s, version = version + 1, updated_by = %s, updated_at = now()
         WHERE id = %s AND version = %s
        RETURNING *
        """,
        (op.amount_cents, device_id, op.budget_id, op.base_version),
    ).fetchone()
    if updated:
        conn.execute(
            "INSERT INTO budget_versions VALUES (%s, %s, %s, %s, 'sync')",
            (op.budget_id, updated["version"], op.amount_cents, device_id),
        )
        return {"op_id": op.op_id, "status": "applied", "budget": _public(updated)}

    # La version a bougé pendant que le téléphone était hors ligne → conflit.
    current = conn.execute("SELECT * FROM budgets WHERE id = %s FOR UPDATE", (op.budget_id,)).fetchone()
    if current is None:
        return {"op_id": op.op_id, "status": "rejected", "reason": "budget inconnu"}
    if current["amount_cents"] == op.amount_cents:
        # Les deux appareils ont fait la même modification : pas de vrai conflit.
        return {"op_id": op.op_id, "status": "applied", "budget": _public(current)}
    conflict = conn.execute(
        """
        INSERT INTO conflicts (budget_id, base_version, server_version, server_value, client_value, device_id)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (op.budget_id, op.base_version, current["version"], current["amount_cents"], op.amount_cents, device_id),
    ).fetchone()
    return {
        "op_id": op.op_id,
        "status": "conflict",
        "conflict_id": str(conflict["id"]),
        "server": _public(current),
        "yours": op.amount_cents,
        "message": "Le budget a été modifié sur un autre appareil. Choisissez la valeur à garder.",
    }


def _add_expense(conn, op: AddExpense, device_id: str) -> dict:
    conn.execute(
        """
        INSERT INTO expenses (op_id, budget_id, amount_cents, label, device_id, occurred_at)
        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (op_id) DO NOTHING
        """,
        (op.op_id, op.budget_id, op.amount_cents, op.label, device_id, op.occurred_at),
    )
    return {"op_id": op.op_id, "status": "applied"}


def resolve(conflict_id: str, amount_cents: int, device_id: str, choice: str) -> dict:
    with pool.connection() as conn:
        c = conn.execute("SELECT * FROM conflicts WHERE id = %s FOR UPDATE", (conflict_id,)).fetchone()
        if c is None or c["status"] != "OPEN":
            return {"status": "not_open"}
        b = conn.execute(
            """
            UPDATE budgets SET amount_cents = %s, version = version + 1, updated_by = %s, updated_at = now()
             WHERE id = %s RETURNING *
            """,
            (amount_cents, device_id, c["budget_id"]),
        ).fetchone()
        conn.execute(
            "INSERT INTO budget_versions VALUES (%s, %s, %s, %s, %s)",
            (c["budget_id"], b["version"], amount_cents, device_id, f"conflict-resolution:{choice}"),
        )
        conn.execute(
            "UPDATE conflicts SET status = 'RESOLVED', resolution = %s WHERE id = %s", (choice, conflict_id)
        )
    return {"status": "resolved", "budget": _public(b)}


def history(budget_id: str) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT version, amount_cents, device_id, reason, created_at FROM budget_versions "
            "WHERE budget_id = %s ORDER BY version",
            (budget_id,),
        ).fetchall()
