# Conventions

The rules the team holds itself to. An agent should load these up front,
before it touches the code, not rediscover them by trial and error.

Always run migrations before deploying.
Never commit secrets to the repository.
Always write a test for every new endpoint.
Never call the database directly from a route handler.
