import uuid


def _op(**kw):
    return {"op_id": str(uuid.uuid4()), **kw}


def test_two_offline_phones_conflict_is_detected_not_overwritten(client):
    b = client.post("/budgets", json={"user_id": "u1", "name": "Courses", "amount_cents": 50_000}).json()
    assert b["version"] == 1

    # Les deux téléphones partent de la version 1, hors ligne.
    phone1 = _op(type="set_amount", budget_id=b["id"], amount_cents=30_000, base_version=1)
    phone2 = _op(type="set_amount", budget_id=b["id"], amount_cents=70_000, base_version=1)

    r1 = client.post("/sync", json={"device_id": "phone-1", "operations": [phone1]}).json()["results"][0]
    r2 = client.post("/sync", json={"device_id": "phone-2", "operations": [phone2]}).json()["results"][0]

    assert r1["status"] == "applied" and r1["budget"]["version"] == 2
    assert r2["status"] == "conflict"
    assert r2["server"]["amount_cents"] == 30_000 and r2["yours"] == 70_000

    state = client.get(f"/budgets/{b['id']}").json()
    assert state["amount_cents"] == 30_000  # rien n'a été écrasé en silence
    assert len(state["open_conflicts"]) == 1

    # L'utilisateur choisit explicitement.
    res = client.post(f"/conflicts/{r2['conflict_id']}/resolve",
                      json={"device_id": "phone-2", "choice": "client"}).json()
    assert res["budget"]["amount_cents"] == 70_000 and res["budget"]["version"] == 3

    values = [h["amount_cents"] for h in client.get(f"/budgets/{b['id']}/history").json()]
    assert values == [50_000, 30_000, 70_000]  # toutes les intentions sont tracées


def test_replayed_sync_is_idempotent(client):
    b = client.post("/budgets", json={"user_id": "u1", "name": "Loisirs", "amount_cents": 10_000}).json()
    op = _op(type="set_amount", budget_id=b["id"], amount_cents=12_000, base_version=1)
    client.post("/sync", json={"device_id": "p1", "operations": [op]})
    again = client.post("/sync", json={"device_id": "p1", "operations": [op]}).json()["results"][0]
    assert again["replayed"] is True
    assert client.get(f"/budgets/{b['id']}").json()["version"] == 2


def test_expenses_from_both_phones_merge_without_conflict(client):
    b = client.post("/budgets", json={"user_id": "u1", "name": "Resto", "amount_cents": 20_000}).json()
    e1 = _op(type="add_expense", budget_id=b["id"], amount_cents=1_500, occurred_at="2026-11-20T12:00:00Z")
    e2 = _op(type="add_expense", budget_id=b["id"], amount_cents=2_500, occurred_at="2026-11-20T12:05:00Z")
    client.post("/sync", json={"device_id": "p1", "operations": [e1]})
    client.post("/sync", json={"device_id": "p2", "operations": [e2, e1]})  # e1 renvoyé par erreur
    assert client.get(f"/budgets/{b['id']}").json()["spent_cents"] == 4_000


def test_identical_concurrent_edits_are_not_a_conflict(client):
    b = client.post("/budgets", json={"user_id": "u1", "name": "Transport", "amount_cents": 5_000}).json()
    for device in ("p1", "p2"):
        r = client.post("/sync", json={"device_id": device, "operations": [
            _op(type="set_amount", budget_id=b["id"], amount_cents=6_000, base_version=1)]}).json()
        assert r["results"][0]["status"] == "applied"
