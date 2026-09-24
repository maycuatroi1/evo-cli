---
name: railway-graphql-api
description: Query and poll Railway through its GraphQL API with curl when the railway CLI fails, returns nothing, or is not configured
learned: true
pattern_type: error_resolution
learned_at: 2026-05-28T12:42:05
source_session: e1dab864-6315-48f2-974f-6d87dd8f79cc
---

## When to use
- `railway status`, `railway list` or `railway whoami` exit 1 or print nothing, but a token is available.
- You need project, service or deployment state in a script, or need to wait for a redeploy to finish.

## Endpoint and tokens
Endpoint: `https://backboard.railway.com/graphql/v2`. Older notes use `backboard.railway.app`.

| Token | Header | Notes |
|---|---|---|
| Account | `Authorization: Bearer <token>` | Everything the user can see; the only token that can run `me` |
| Workspace (team) | `Authorization: Bearer <token>` | One workspace; `me` fails with it |
| Project | `Project-Access-Token: <token>` | One project environment |

The CLI reads account/workspace tokens from `RAILWAY_API_TOKEN` and project tokens from `RAILWAY_TOKEN`. Load tokens with the credentials-utils skill; never paste them into the repo.

## Queries
```bash
rw() {  # rw '<graphql>' '<variables json>'
  curl -s https://backboard.railway.com/graphql/v2 \
    -H "Authorization: Bearer $RAILWAY_API_TOKEN" -H "Content-Type: application/json" \
    -d "$(jq -n --arg q "$1" --argjson v "${2:-{\}}" '{query: $q, variables: $v}')"
}

rw 'query { projects { edges { node { id name } } } }'

rw 'query($id: String!) { project(id: $id) { name
      environments { edges { node { id name } } }
      services { edges { node { id name } } } } }' '{"id":"<projectId>"}'

rw 'query($i: DeploymentListInput!) { deployments(first: 1, input: $i) { edges { node { id status createdAt } } } }' \
   '{"i":{"projectId":"<pid>","environmentId":"<eid>","serviceId":"<sid>"}}'
```
Build queries up from IDs: projects first, then environments and services, then deployments. If a field errors, check the schema with an introspection query rather than guessing.

Mutations used before: `serviceInstanceRedeploy(environmentId: "...", serviceId: "...")` and `deploymentRestart(id: "...")`.

## Polling a deploy
Wait until every service reaches a terminal status. In-progress statuses include QUEUED, WAITING, INITIALIZING, BUILDING and DEPLOYING, so do not treat "not BUILDING/DEPLOYING" as done.

```bash
status_of() {  # status_of <sid>
  rw 'query($i: DeploymentListInput!) { deployments(first: 1, input: $i) { edges { node { status } } } }' \
     "{\"i\":{\"projectId\":\"$PID\",\"environmentId\":\"$EID\",\"serviceId\":\"$1\"}}" \
    | jq -r '.data.deployments.edges[0].node.status'
}

while :; do
  pending=0
  for svc in "Primary:<sid-1>" "Worker:<sid-2>"; do
    st=$(status_of "${svc##*:}")
    echo "[$(date +%H:%M:%S)] ${svc%%:*}=$st"
    case "$st" in SUCCESS|FAILED|CRASHED|REMOVED|SKIPPED) ;; *) pending=1 ;; esac
  done
  [ "$pending" = 0 ] && break
  sleep 5
done
```
