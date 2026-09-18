#!/usr/bin/env bash
# End-to-end smoke test against a running API + worker + compose stack (used by `make smoke` and CI).
set -euo pipefail
API="${API_URL:-http://localhost:8000}"
MAILPIT="${MAILPIT_URL:-http://localhost:8025}"
JAR="$(mktemp)"
EMAIL="smoke+$(date +%s)@example.com"
PASS="Sm0ke-test-password!"
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
req() { # method path [json]
  local method="$1" path="$2" body="${3:-}"
  local csrf; csrf=$(grep -o 'csrf_token[[:space:]]*[^[:space:]]*$' "$JAR" | awk '{print $2}' | tail -1 || true)
  if [ -n "$body" ]; then
    curl -sS -b "$JAR" -c "$JAR" -X "$method" "$API$path" -H 'Content-Type: application/json' -H "X-CSRF-Token: ${csrf:-}" -d "$body"
  else
    curl -sS -b "$JAR" -c "$JAR" -X "$method" "$API$path" -H "X-CSRF-Token: ${csrf:-}"
  fi
}

echo "== waiting for API"
for i in $(seq 1 90); do curl -sf "$API/healthz" >/dev/null && break; sleep 1; done
curl -sf "$API/healthz" >/dev/null || fail "API not reachable"
echo "== readyz"; curl -s "$API/readyz"; echo
echo "== templates"; req GET /v1/templates | jq -r '.[].key' | tr '\n' ' '; echo

echo "== signup"
req POST /v1/auth/signup "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"name\":\"Smoke Tester\"}" | jq -c '{user:.user.email, verified:.user.email_verified}'
echo "== verify via mailpit"
TOKEN=""
for i in $(seq 1 20); do
  TOKEN=$(curl -s "$MAILPIT/api/v1/search?query=to:$EMAIL" | jq -r '.messages[0].ID' 2>/dev/null | xargs -I{} curl -s "$MAILPIT/api/v1/message/{}" | jq -r '.Text' 2>/dev/null | grep -o 'token=[A-Za-z0-9_-]*' | head -1 | cut -d= -f2 || true)
  [ -n "$TOKEN" ] && break; sleep 1
done
[ -n "$TOKEN" ] || fail "verification email not found in Mailpit"
req POST /v1/auth/verify-email "{\"token\":\"$TOKEN\"}" | jq -c .
echo "== login"
req POST /v1/auth/login "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | jq -c '{verified:.user.email_verified}'
echo "== bad login is rejected"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/v1/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"wrong-password-1\"}")
[ "$code" = "401" ] || fail "expected 401 for bad login, got $code"

echo "== create org"
ORG=$(req POST /v1/orgs '{"name":"Smoke Org","industry":"tech"}' | jq -r '.id'); echo "org=$ORG"
echo "== create agent from template"
AGENT=$(req POST "/v1/orgs/$ORG/agents" '{"template_key":"hr_onboarding","name":"HR Buddy"}' | jq -r '.id'); echo "agent=$AGENT"
req GET "/v1/orgs/$ORG/agents/$AGENT" | jq -c '{name, status, tools, etag}'
echo "== validate (rag enabled, no KB -> blocking error expected)"
req POST "/v1/orgs/$ORG/agents/$AGENT/validate" | jq -c '{ok, errors: [.errors[].message]}'
echo "== create KB + attach"
KB=$(req POST "/v1/orgs/$ORG/knowledge-bases" '{"name":"HR docs"}' | jq -r '.id'); echo "kb=$KB"
ETAG=$(req GET "/v1/orgs/$ORG/agents/$AGENT" | jq -r '.etag')
CFG=$(req GET "/v1/orgs/$ORG/agents/$AGENT" | jq -c --arg kb "$KB" '.draft_config | .tools.rag.kb_ids=[$kb]')
curl -sS -b "$JAR" -c "$JAR" -X PATCH "$API/v1/orgs/$ORG/agents/$AGENT" -H 'Content-Type: application/json' -H "If-Match: $ETAG" \
  -H "X-CSRF-Token: $(grep -o 'csrf_token[[:space:]]*[^[:space:]]*$' "$JAR" | awk '{print $2}' | tail -1)" -d "{\"draft_config\":$CFG}" | jq -c '{status, tools}'
echo "== stale etag -> 412"
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X PATCH "$API/v1/orgs/$ORG/agents/$AGENT" -H 'Content-Type: application/json' -H "If-Match: $ETAG" \
  -H "X-CSRF-Token: $(grep -o 'csrf_token[[:space:]]*[^[:space:]]*$' "$JAR" | awk '{print $2}' | tail -1)" -d '{"name":"HR Buddy 2"}')
[ "$code" = "412" ] || fail "expected 412 on stale etag, got $code"

echo "== upload a document (presigned PUT) and ingest"
DOC_TXT="$(mktemp).md"
printf '# Leave Policy\n\n## Casual Leave\n\nEmployees get 12 casual leaves per year. Casual leave cannot be carried forward.\n\n## Notice period\n\nThe notice period is 60 days for all employees.\n' > "$DOC_TXT"
SIZE=$(wc -c < "$DOC_TXT" | tr -d ' ')
UP=$(req POST "/v1/orgs/$ORG/knowledge-bases/$KB/uploads" "{\"files\":[{\"name\":\"leave-policy.md\",\"size\":$SIZE,\"mime\":\"text/markdown\"}]}")
URL=$(echo "$UP" | jq -r '.uploads[0].upload_url'); DOCID=$(echo "$UP" | jq -r '.uploads[0].document_id')
curl -sS -X PUT "$URL" -H 'Content-Type: text/markdown' -H "Content-Length: $SIZE" --data-binary @"$DOC_TXT" -o /dev/null -w "upload http=%{http_code}\n"
req POST "/v1/orgs/$ORG/knowledge-bases/$KB/documents" "{\"uploads\":[{\"document_id\":\"$DOCID\",\"name\":\"leave-policy.md\",\"mime\":\"text/markdown\"}]}" | jq -c '.[0] | {filename, status}'
for i in $(seq 1 120); do
  ST=$(req GET "/v1/orgs/$ORG/documents/$DOCID" | jq -r '.status')
  [ "$ST" = "ready" ] && break; [ "$ST" = "failed" ] && { req GET "/v1/orgs/$ORG/documents/$DOCID" | jq .; fail "ingestion failed"; }
  sleep 1
done
[ "$ST" = "ready" ] || fail "ingestion did not finish (status=$ST)"
req GET "/v1/orgs/$ORG/documents/$DOCID" | jq -c '{status, chunk_count}'
echo "== test retrieval"
req POST "/v1/orgs/$ORG/knowledge-bases/$KB/search" '{"query":"how many casual leaves do I get","top_k":3}' | jq -c '{n:(.results|length), top:(.results[0]|{score, section_title, text:(.text[0:60])})}'

echo "== playground session + streamed answer"
SID=$(req POST "/v1/orgs/$ORG/agents/$AGENT/playground/sessions" '{}' | jq -r '.session_id'); echo "session=$SID"
curl -sS -N -b "$JAR" -X POST "$API/v1/orgs/$ORG/playground/sessions/$SID/messages" -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $(grep -o 'csrf_token[[:space:]]*[^[:space:]]*$' "$JAR" | awk '{print $2}' | tail -1)" \
  -d '{"message":"How many casual leaves do I get?"}' --max-time 120 | grep -E '^event:' | sort | uniq -c
echo "== publish"
req POST "/v1/orgs/$ORG/agents/$AGENT/publish" '{"changelog":"v1"}' | jq -c '{version_number, changelog}'
echo "== analytics + usage + audit"
req GET "/v1/orgs/$ORG/analytics/overview" | jq -c '{totals, tool_split}'
req GET "/v1/orgs/$ORG/usage" | jq -c '{rows: [.rows[] | {key, events}]}'
req GET "/v1/orgs/$ORG/audit-logs" | jq -c '[.items[].action]'
echo "== cross-tenant: a second user gets 404 for this org"
JAR2="$(mktemp)"; EMAIL2="smoke2+$(date +%s)@example.com"
curl -sS -c "$JAR2" -X POST "$API/v1/auth/signup" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL2\",\"password\":\"$PASS\",\"name\":\"Other\"}" >/dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR2" "$API/v1/orgs/$ORG/agents")
[ "$code" = "403" ] || [ "$code" = "404" ] || fail "expected 403/404 for unverified other user, got $code"
echo "cross-tenant http=$code"
echo "== rate limit headers"; curl -sI -b "$JAR" -X POST "$API/v1/auth/refresh" | grep -i 'ratelimit-limit' || true
echo "SMOKE OK"
