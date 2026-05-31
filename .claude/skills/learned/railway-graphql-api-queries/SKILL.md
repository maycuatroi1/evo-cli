---
name: railway-graphql-api-queries
description: Query Railway's GraphQL API directly with curl when the CLI is unresponsive or unavailable
pattern_type: error_resolution
learned_at: 2026-05-28T12:42:05
source_session: e1dab864-6315-48f2-974f-6d87dd8f79cc
---

## When to use
When the `railway` CLI returns empty output, exits silently, or is otherwise unresponsive, but you have a valid `RAILWAY_API_TOKEN` set in your environment.

## How
Query Railway's backboard GraphQL endpoint at `https://backboard.railway.app/graphql/v2` using curl:

```bash
curl -X POST https://backboard.railway.app/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { me { id name email } }"}'
```

Common queries:
- `query { me { id name email } }` — current user/auth check
- `query { projects { edges { node { id name } } } }` — list all projects
- `query { project(id: "...") { name services { edges { node { id name } } } } }` — project details & services
- `query { serviceInstance(id: "...") { status } }` — service status
- `query { deployments(first: 5, input: { serviceId: "..." }) { edges { node { id status createdAt } } } }` — deployment history

Mutations for service operations:
- `mutation { serviceInstanceRedeploy(environmentId: "...", serviceId: "...") }` — redeploy a service
- `mutation { deploymentRestart(id: "...") }` — restart a deployment

## Example
Check deployment statuses for a project's services:

```bash
ENV="<environmentId>"
for sid in "<serviceId-1>" "<serviceId-2>"; do
  curl -s -X POST https://backboard.railway.app/graphql/v2 \
    -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"query { serviceInstance(id: \"$sid\") { currentDeployment { status } } }\"}"
done
```
