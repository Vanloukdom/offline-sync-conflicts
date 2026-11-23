from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import sync
from app.db import close_db, init_db, pool
from app.observability import setup_observability


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    close_db()


app = FastAPI(
    title="Offline sync — conflits",
    description="Deux téléphones hors ligne modifient la même donnée : conflit détecté, jamais écrasé.",
    version="1.0.0",
    lifespan=lifespan,
)
setup_observability(app)


class BudgetIn(BaseModel):
    user_id: str
    name: str
    amount_cents: int


class SyncIn(BaseModel):
    device_id: str
    operations: list[Annotated[sync.Operation, Field(discriminator="type")]]


class ResolveIn(BaseModel):
    device_id: str
    choice: Literal["server", "client", "custom"]
    amount_cents: int | None = None


@app.post("/budgets", status_code=201)
def create_budget(body: BudgetIn):
    return sync.create_budget(body.user_id, body.name, body.amount_cents)


@app.get("/budgets/{budget_id}")
def get_budget(budget_id: str):
    b = sync.get_budget(budget_id)
    if not b:
        raise HTTPException(404, "Budget introuvable")
    return b


@app.get("/budgets/{budget_id}/history")
def history(budget_id: str):
    return sync.history(budget_id)


@app.post("/sync")
def sync_batch(body: SyncIn):
    """Le téléphone envoie sa file d'opérations hors ligne, dans l'ordre."""
    return {"results": [sync.apply(op, body.device_id) for op in body.operations]}


@app.post("/conflicts/{conflict_id}/resolve")
def resolve(conflict_id: str, body: ResolveIn):
    with pool.connection() as conn:
        c = conn.execute("SELECT * FROM conflicts WHERE id = %s", (conflict_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Conflit introuvable")
    amount = {"server": c["server_value"], "client": c["client_value"]}.get(body.choice, body.amount_cents)
    if amount is None:
        raise HTTPException(422, "amount_cents requis pour un choix 'custom'")
    result = sync.resolve(conflict_id, amount, body.device_id, body.choice)
    if result["status"] != "resolved":
        raise HTTPException(409, "Conflit déjà résolu")
    return result
