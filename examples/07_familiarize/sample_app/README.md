# TaskFlow

TaskFlow is a small task-management API. This README is the first thing a
new engineer — or a new agent — reads to get oriented. The prose around
these statements is normal documentation; the *structured* sentences are
what an offline extractor can pull into atoms.

## Stack

TaskFlow's database is PostgreSQL.
TaskFlow's API framework is FastAPI.
TaskFlow's deployment target is AWS ECS.

## Key decisions

We decided to use JWT because it keeps the API stateless.
We decided to use Postgres because we need relational integrity.
We decided to use Alembic because migrations stay reviewable in git.
