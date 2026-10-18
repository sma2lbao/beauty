#!/usr/bin/env bash
# 生成一组本地演示数据(组织/用户、会话、产物、run)。
# 需要 account-service(:8001)、agent-orchestrator(:8002)、workspace-service(:8005) 已启动。
# 用法: scripts/seed-local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACCOUNT_URL="${ACCOUNT_URL:-http://127.0.0.1:8001}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://127.0.0.1:8002}"
WORKSPACE_URL="${WORKSPACE_URL:-http://127.0.0.1:8005}"
EMAIL="${SEED_EMAIL:-demo@example.com}"
PASSWORD="${SEED_PASSWORD:-DemoPass123!}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

json_get() {
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1],""))' "$1"
}

request() {
  local method="$1" url="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" "$url" -H 'Content-Type: application/json' -d "$body" -o "$tmp/response.json" -w '%{http_code}'
  else
    curl -sS -X "$method" "$url" -o "$tmp/response.json" -w '%{http_code}'
  fi
}

auth_request() {
  local method="$1" url="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" "$url" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "$body" -o "$tmp/response.json" -w '%{http_code}'
  else
    curl -sS -X "$method" "$url" -H "Authorization: Bearer $TOKEN" -o "$tmp/response.json" -w '%{http_code}'
  fi
}

# 1) 注册或登录演示账号
status=$(request POST "$ACCOUNT_URL/v1/auth/register" "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"display_name\":\"本地演示\"}")
if [ "$status" = "201" ]; then
  echo "已创建演示账号:$EMAIL"
elif [ "$status" = "409" ]; then
  status=$(request POST "$ACCOUNT_URL/v1/auth/login" "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
  if [ "$status" != "200" ]; then
    echo "演示账号已存在但登录失败(密码可能不同)。可删除 account-service/account.db 后重试。" >&2
    cat "$tmp/response.json" >&2
    exit 1
  fi
  echo "复用已有演示账号:$EMAIL"
else
  echo "注册失败($status):" >&2
  cat "$tmp/response.json" >&2
  exit 1
fi
TOKEN=$(json_get access_token < "$tmp/response.json")
if [ -z "$TOKEN" ]; then
  echo "未拿到 access token" >&2
  exit 1
fi

# 2) 会话(幂等:同标题已存在则跳过)
conversations=$(curl -sS "$ORCHESTRATOR_URL/api/v1/conversations?limit=100" -H "Authorization: Bearer $TOKEN")
for title in "【本地演示】周报整理" "【本地演示】待办梳理"; do
  exists=$(printf '%s' "$conversations" | python3 -c 'import json,sys; title=sys.argv[1]; print(any(item.get("title")==title for item in json.load(sys.stdin)["items"]))' "$title")
  if [ "$exists" = "True" ]; then
    echo "会话已存在,跳过:$title"
    continue
  fi
  status=$(auth_request POST "$ORCHESTRATOR_URL/api/v1/conversations" "{\"title\":\"$title\"}")
  if [ "$status" = "201" ] || [ "$status" = "200" ]; then
    echo "已创建会话:$title"
  else
    echo "创建会话失败($status):$title" >&2
  fi
done

# 3) 工作区产物(变更/产物页有内容):列表在 /v1/workspaces/{id}/artifacts
workspace_id=$(curl -sS "$WORKSPACE_URL/v1/workspaces/default" -H "Authorization: Bearer $TOKEN" | json_get id)
artifacts=$(curl -sS "$WORKSPACE_URL/v1/workspaces/$workspace_id/artifacts" -H "Authorization: Bearer $TOKEN" || true)
seed_artifacts() {
  local title="$1" content="$2" kind="$3"
  exists=$(printf '%s' "$artifacts" | python3 -c 'import json,sys; title=sys.argv[1]; data=json.load(sys.stdin); items=data.get("items",data) if isinstance(data,dict) else data; print(any(item.get("title")==title for item in items))' "$title" 2>/dev/null || echo "False")
  if [ "$exists" = "True" ]; then
    echo "产物已存在,跳过:$title"
  else
    body=$(python3 -c 'import json,sys; print(json.dumps({"title":sys.argv[1],"content":sys.argv[2],"kind":sys.argv[3]},ensure_ascii=False))' "$title" "$content" "$kind")
    status=$(auth_request POST "$WORKSPACE_URL/v1/artifacts" "$body")
    if [ "$status" = "201" ] || [ "$status" = "200" ]; then
      echo "已创建产物:$title"
    else
      echo "创建产物失败($status):$title" >&2
    fi
  fi
}
seed_artifacts "【本地演示】本周周报" "# 本周周报

- 邮件已汇总
- 待办已排优先级
- 下周计划已生成" "markdown"
seed_artifacts "【本地演示】周报改动" "--- a/weekly.md
+++ b/weekly.md
@@ -1,3 +1,3 @@
 # 周报
-- 旧内容
+- 本周更新
" "diff"
seed_artifacts "【本地演示】预览图" "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z6ZkAAAAASUVORK5CYII=" "image"

# 4) 一个演示 run(幂等;失败不阻塞 seed)
runs=$(curl -sS "$ORCHESTRATOR_URL/api/v1/runs?limit=100" -H "Authorization: Bearer $TOKEN")
run_exists=$(printf '%s' "$runs" | python3 -c 'import json,sys; title="【本地演示】整理本周邮件"; print(any(item.get("title")==title for item in json.load(sys.stdin)))')
if [ "$run_exists" = "True" ]; then
  echo "演示 run 已存在,跳过"
else
  status=$(auth_request POST "$ORCHESTRATOR_URL/api/v1/runs" '{"title":"【本地演示】整理本周邮件","goal":"把本周邮件整理成周报","acceptance_criteria":["有周报正文"]}')
  if [ "$status" = "201" ] || [ "$status" = "200" ]; then
    run_id=$(json_get id < "$tmp/response.json")
    echo "已创建 run:$run_id"
    auth_request POST "$ORCHESTRATOR_URL/api/v1/runs/$run_id/plan" >/dev/null || true
    echo "已请求生成计划(LLM 关闭时走确定性桩)"
  else
    echo "创建 run 失败($status),不影响其他演示数据" >&2
  fi
fi

echo ""
echo "本地演示数据已就绪:"
echo "  账号:$EMAIL / $PASSWORD"
echo "  打开 http://localhost:5173 登录查看"
