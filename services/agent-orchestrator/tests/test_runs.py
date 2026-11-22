from fastapi.testclient import TestClient

from tests.conftest import ACCOUNT, OTHER, bearer

ACCOUNT_HEADERS = ACCOUNT


def _create_run(
    client: TestClient,
    account: str = "t-1",
    *,
    title: str = "整理本周日程成周报",
    goal: str = "整理本周日程,生成周报草稿",
) -> dict:
    body = {
        "title": title,
        "goal": goal,
        "acceptance_criteria": ["覆盖 5 个工作日", "不超过 300 字"],
    }
    resp = client.post("/api/v1/runs", json=body, headers=bearer(account))
    assert resp.status_code == 201
    return resp.json()


def test_create_and_get_run(client: TestClient) -> None:
    created = _create_run(client)
    assert created["status"] == "pending"
    assert created["rounds"] == 0
    assert len(created["id"]) == 32
    assert created["acceptance_criteria"] == ["覆盖 5 个工作日", "不超过 300 字"]

    got = client.get(f"/api/v1/runs/{created['id']}", headers=ACCOUNT_HEADERS)
    assert got.status_code == 200
    assert got.json()["goal"] == "整理本周日程,生成周报草稿"


def test_events_replay_in_seq_order(client: TestClient) -> None:
    created = _create_run(client)
    resp = client.get(f"/api/v1/runs/{created['id']}/events", headers=ACCOUNT_HEADERS)
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["seq"] == 1
    assert events[0]["type"] == "run.created.v1"
    assert len(events[0]["id"]) == 32  # 事件 ID = 信封 ID(幂等去重键)


def test_account_isolation(client: TestClient) -> None:
    created = _create_run(client, account="t-1")
    resp = client.get(f"/api/v1/runs/{created['id']}", headers=OTHER)
    assert resp.status_code == 404


def test_missing_bearer_rejected(client: TestClient) -> None:
    """无 Bearer token 一律 401(账号只在签名 claim 里,没有临时头后门)。"""
    resp = client.post("/api/v1/runs", json={"title": "t", "goal": "g"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth.token_missing"


def test_validation_error_shape(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/runs", json={"title": "", "goal": "g"}, headers=ACCOUNT_HEADERS
    )
    assert resp.status_code == 422


def test_list_runs_newest_first_and_account_scoped(client: TestClient) -> None:
    """控制台首页:最新在前、账号隔离、limit 生效。"""
    first = _create_run(client)
    second = _create_run(client, account="t-2")
    third = _create_run(client)

    listed = client.get("/api/v1/runs", headers=ACCOUNT_HEADERS).json()
    ids = [run["id"] for run in listed["items"]]
    assert ids == [third["id"], first["id"]]  # 最新在前;t-2 的不可见
    assert second["id"] not in ids
    assert listed["next_cursor"] is None

    limited = client.get("/api/v1/runs?limit=1", headers=ACCOUNT_HEADERS).json()
    assert [run["id"] for run in limited["items"]] == [third["id"]]
    assert limited["next_cursor"] is not None


def test_list_runs_cursor_pagination(client: TestClient) -> None:
    """limit+1 判断 has_more;两页拼起来覆盖全部且不重复。"""
    runs = [_create_run(client) for _ in range(3)]

    first = client.get("/api/v1/runs?limit=2", headers=ACCOUNT_HEADERS).json()
    assert [run["id"] for run in first["items"]] == [runs[2]["id"], runs[1]["id"]]
    assert first["next_cursor"]

    second = client.get(
        f"/api/v1/runs?limit=2&cursor={first['next_cursor']}",
        headers=ACCOUNT_HEADERS,
    ).json()
    assert [run["id"] for run in second["items"]] == [runs[0]["id"]]
    assert second["next_cursor"] is None


def test_list_runs_search_matches_title_or_goal(client: TestClient) -> None:
    _create_run(client)
    other = _create_run(
        client,
        title="修复登录页报错",
        goal="定位并修复登录接口 500",
    )

    by_title = client.get("/api/v1/runs?q=登录页", headers=ACCOUNT_HEADERS).json()
    assert [run["id"] for run in by_title["items"]] == [other["id"]]

    by_goal = client.get("/api/v1/runs?q=周报草稿", headers=ACCOUNT_HEADERS).json()
    assert len(by_goal["items"]) == 1
    assert by_goal["items"][0]["title"] == "整理本周日程成周报"

    empty = client.get("/api/v1/runs?q=不存在的关键词", headers=ACCOUNT_HEADERS).json()
    assert empty["items"] == []


def test_list_runs_bad_cursor_is_400(client: TestClient) -> None:
    resp = client.get("/api/v1/runs?cursor=not-a-cursor!!", headers=ACCOUNT_HEADERS)
    assert resp.status_code == 400
    assert resp.json()["code"] == "run.bad_cursor"


def test_list_runs_cursor_is_account_scoped(client: TestClient) -> None:
    """别的账号的游标只当锚点找不到处理:空页,不泄漏存在性。"""
    _create_run(client)
    _create_run(client)
    page = client.get("/api/v1/runs?limit=1", headers=ACCOUNT_HEADERS).json()
    assert page["next_cursor"]
    resp = client.get(
        f"/api/v1/runs?cursor={page['next_cursor']}",
        headers=OTHER,
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_cors_allows_web_origin(client: TestClient) -> None:
    """浏览器直连(Vite 5173)需要跨域头;白名单来自配置。"""
    resp = client.get("/healthz", headers={"Origin": "http://localhost:5173"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
