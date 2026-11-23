CREATE TABLE IF NOT EXISTS budgets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    amount_cents  BIGINT NOT NULL,
    version       INT NOT NULL DEFAULT 1,     -- verrouillage optimiste
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Historique complet : aucune valeur n'est jamais écrasée silencieusement.
CREATE TABLE IF NOT EXISTS budget_versions (
    budget_id     UUID NOT NULL REFERENCES budgets(id),
    version       INT NOT NULL,
    amount_cents  BIGINT NOT NULL,
    device_id     TEXT,
    reason        TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (budget_id, version)
);

CREATE TABLE IF NOT EXISTS conflicts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id        UUID NOT NULL REFERENCES budgets(id),
    base_version     INT NOT NULL,
    server_version   INT NOT NULL,
    server_value     BIGINT NOT NULL,
    client_value     BIGINT NOT NULL,
    device_id        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
    resolution       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Chaque opération hors ligne porte un op_id : rejouer une synchro est sans effet.
CREATE TABLE IF NOT EXISTS applied_ops (
    op_id       UUID PRIMARY KEY,
    result      JSONB NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Les dépenses sont des opérations commutatives : elles ne sont jamais en conflit.
CREATE TABLE IF NOT EXISTS expenses (
    op_id         UUID PRIMARY KEY,
    budget_id     UUID NOT NULL REFERENCES budgets(id),
    amount_cents  BIGINT NOT NULL CHECK (amount_cents > 0),
    label         TEXT,
    device_id     TEXT NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL
);
