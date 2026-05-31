---
name: railway-polling-deployment-status
description: Poll Railway deployments until they reach a terminal state (SUCCESS/FAILED/CRASHED) with timestamped progress
pattern_type: debugging_techniques
learned_at: 2026-05-28T12:42:05
source_session: e1dab864-6315-48f2-974f-6d87dd8f79cc
---

## When to use
After triggering a service redeploy or restart, you need to wait for it to complete and report status at intervals.

## How
Define a function that queries the latest deployment status for one or more services, and call it in a loop until all services reach a stable state (SUCCESS, FAILED, or CRASHED — any status other than DEPLOYING or BUILDING):

```bash
status_of() {
  sid="$1"
  curl -s -X POST https://backboard.railway.app/graphql/v2 \
    -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"query { serviceInstance(id: \"$sid\") { currentDeployment { status createdAt } } }\"}" \
    | python3 -c "import sys, json; d = json.load(sys.stdin); print(d['data']['serviceInstance']['currentDeployment']['status'])"
}

while true; do
  echo "[$(date +%H:%M:%S)] status:"
  all_done=true
  for svc in "Primary:<serviceId-1>" "Worker:<serviceId-2>"; do
    name="${svc%%:*}"; sid="${svc##*:}"
    st=$(status_of "$sid")
    echo "  $name=$st"
    [[ "$st" =~ (DEPLOYING|BUILDING) ]] && all_done=false
  done
  $all_done && break
  sleep 5
done
```

## Example
Monitor Primary and Worker deployments:

```bash
ENV="<environmentId>"
SVCS="Primary:<serviceId-1> Worker:<serviceId-2>"

# ... run the polling loop above
# Output:
# [12:41:21] status:
#   Primary=DEPLOYING
#   Worker=DEPLOYING
# [12:41:26] status:
#   Primary=SUCCESS
#   Worker=SUCCESS
```
