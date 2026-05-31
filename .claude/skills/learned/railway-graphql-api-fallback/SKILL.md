---
name: railway-graphql-api-fallback
description: Use Railway's GraphQL API with team token when CLI is unavailable or misconfigured
pattern_type: error_resolution
learned_at: 2026-05-30T16:29:22
source_session: f470fba7-cbe2-4c9a-ab06-76600d9ead8c
---

## When to Use
- Railway CLI commands (status, list, whoami) fail with exit code 1 or return empty output
- RAILWAY_API_TOKEN environment variable is set (team token from Railway dashboard)
- Need to query projects, environments, services, or deployment status programmatically

## How
1. Verify token is set: `echo $RAILWAY_API_TOKEN`
2. POST to https://backboard.railway.com/graphql/v2 with Bearer token in Authorization header
3. Start with simple schema exploration queries (projects, environments) to understand available fields
4. Build complex queries incrementally with variables for nested data
5. For batch status queries, enumerate parent entities first (project/env/service IDs), then batch-query deployment status
6. Parse response JSON and filter/format results

## Example

**All projects:**
```bash
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { projects { edges { node { id name } } } }"}'
```

**Project with environments and services:**
```bash
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"query(\$id: String!){ project(id:\$id){ name environments{ edges{ node{ id name } } } services{ edges{ node{ id name } } } } }", "variables":{"id":"<projectId>"}}'
```

**Deployment status for a service:**
```bash
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"query(\$i: DeploymentListInput!){ deployments(first:1, input:\$i){ edges{ node{ status createdAt } } } }", "variables":{"i":{"projectId":"<pid>","environmentId":"<eid>","serviceId":"<sid>"}}}'
```

## Key Insight
Team tokens from RAILWAY_API_TOKEN env var work with GraphQL API even when CLI is unconfigured or fails. When CLI troubleshooting hits a wall, check for env tokens and fallback to direct API queries.
