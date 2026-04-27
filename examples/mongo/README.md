# Minimal embedding example

A bare FastAPI app that mounts regstack at `/api/auth`.

## Run it

From the repo root:

```bash
# Generate a JWT secret once
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
export REGSTACK_MONGODB_URL=mongodb://localhost:27017

uv run uvicorn examples.minimal.main:app --reload --port 8000
```

## Exercise it

```bash
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"a@b.test","password":"hunter2hunter2","full_name":"A B"}'

TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
    -H 'content-type: application/json' \
    -d '{"email":"a@b.test","password":"hunter2hunter2"}' | jq -r .access_token)

curl http://localhost:8000/api/auth/me -H "authorization: Bearer $TOKEN"
curl -X POST http://localhost:8000/api/auth/logout -H "authorization: Bearer $TOKEN"
```
