# Deux appareils, une donnée — synchronisation

[![ci-cd](https://github.com/Vanloukdom/offline-sync-conflicts/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/Vanloukdom/offline-sync-conflicts/actions/workflows/ci-cd.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791) ![k3s](https://img.shields.io/badge/k3s-deploy-FFC61C)

> Projet **08/10** de la série *« 10 problèmes que tout ingénieur logiciel devrait savoir résoudre »*.
> Démo : `https://sync.apps.vansoreldatacloud.com` · Documentation interactive : `https://sync.apps.vansoreldatacloud.com/docs`


## Le problème

Même budget ouvert sur **deux téléphones hors ligne**. Le premier passe de **500 € à 300 €**, le second de **500 € à 700 €**. Les deux se reconnectent en même temps. Quelle valeur garder ? Comment détecter le conflit **sans écraser silencieusement** une modification légitime ?

## La solution

- **Pas de « dernier qui écrit gagne »** : chaque modification envoie la **`base_version`** connue du téléphone (verrouillage optimiste).
- `UPDATE … WHERE version = base_version` : le premier arrivé passe en v2 ; le second (encore en base v1) déclenche un **conflit enregistré**, avec les deux valeurs.
- L'API renvoie `status: conflict`, la valeur serveur et la valeur du téléphone. **L'utilisateur choisit** (serveur, la sienne ou une valeur personnalisée).
- **Historique complet** (`budget_versions`) : 500 → 300 → 700, avec l'appareil et la raison. Rien n'est perdu.
- Deux modifications **identiques** ne sont pas un conflit.
- Les **dépenses** sont des **opérations commutatives** (ajouts) et non des états : elles fusionnent sans conflit, à la manière d'un CRDT.
- Chaque opération hors ligne a un **`op_id`** : rejouer une synchronisation est sans effet.

## Architecture

```mermaid
sequenceDiagram
    participant P1 as Téléphone 1
    participant P2 as Téléphone 2
    participant S as Serveur
    Note over P1,P2: hors ligne, tous deux en v1 = 500 €
    P1->>S: set 300 (base v1)
    S-->>P1: appliqué → v2
    P2->>S: set 700 (base v1)
    S-->>P2: conflict {serveur: 300, vous: 700}
    P2->>S: resolve choice=client
    S-->>P2: v3 = 700 (historique : 500, 300, 700)
```

## API

| Méthode | Route | Rôle |
|---|---|---|
| POST | `/budgets` | Créer un budget |
| GET | `/budgets/{id}` | État, dépenses, conflits ouverts |
| GET | `/budgets/{id}/history` | Toutes les versions |
| POST | `/sync` | Envoi de la file d'opérations hors ligne |
| POST | `/conflicts/{id}/resolve` | Résolution explicite |

### Démo

```bash
URL=https://sync.apps.vansoreldatacloud.com
curl -s -XPOST $URL/sync -H 'content-type: application/json' -d '{"device_id":"phone-2","operations":[
  {"type":"set_amount","op_id":"'$(uuidgen)'","budget_id":"'$BUDGET'","amount_cents":70000,"base_version":1}]}'
```

## Lancer en local

```bash
docker compose up --build          # API sur http://localhost:8000/docs
```

## Tests

```bash
docker compose up -d db
pip install -r requirements.txt -r requirements-dev.txt
ADMIN_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest -q
```

Ce qui est prouvé : Conflit détecté puis résolu, avec historique 500/300/700 ; synchro rejouée idempotente ; dépenses de deux téléphones fusionnées sans doublon ; modifications identiques non conflictuelles.

## Déploiement en production (k3s sur VPS)

Pipeline GitHub Actions `test → build → deploy` :

1. **test** : pytest avec PostgreSQL en service ;
2. **build** : image multi-couches poussée sur `ghcr.io/vanloukdom/offline-sync-conflicts` (tags `sha` et `latest`) ;
3. **deploy** : `kubectl apply -k k8s/` dans le namespace `p08-sync`, puis attente du `rollout status`.

Secrets à créer dans *Settings → Secrets and variables → Actions* :

| Secret | Contenu |
|---|---|
| `KUBECONFIG_B64` | `base64 -w0 kubeconfig` du cluster k3s (sans ce secret, l'étape deploy est ignorée) |
| `POSTGRES_PASSWORD` | Mot de passe PostgreSQL (fixe : ne pas le changer après le premier déploiement) |
| `APP_SECRET` | `openssl rand -hex 32` |

Côté cluster : Traefik (inclus dans k3s), cert-manager avec un `ClusterIssuer` `letsencrypt-prod`, un enregistrement DNS `*.apps.vansoreldatacloud.com` vers le VPS, et le package GHCR passé en **public** (ou un `imagePullSecret`).

Sécurité des pods : utilisateur non root, système de fichiers en lecture seule, capabilities supprimées, requests/limits, probes `/healthz` et `/readyz`, métriques Prometheus sur `/metrics`.

## Pour aller plus loin

Horloges hybrides (HLC) ou vecteurs de version pour la synchronisation pair à pair ; Automerge/Yjs pour des documents riches ; notifications push des conflits.

---
Van Sorel LOUKDOM · DevOps / Cloud & Data Engineer · Licence MIT
