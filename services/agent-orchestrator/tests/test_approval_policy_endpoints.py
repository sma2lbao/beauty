"""P0-4 审批策略端点:整表替换、账号隔离、参数列往返与校验。"""

from __future__ import annotations

from tests.conftest import ACCOUNT, OTHER, FakeLLM, _tool_client

# ---- 端点:整表替换 + 账号隔离 ----


def test_policies_roundtrip_and_validation(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []

        resp = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {"tool": "external_publish", "action": "auto"},
                    {"tool": "*", "action": "confirm"},
                ]
            },
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        assert [row["tool"] for row in resp.json()] == ["*", "external_publish"]

        # 整表替换:再次 PUT 只提交一条,旧规则消失
        resp = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "search_knowledge", "action": "deny"}]},
            headers=ACCOUNT,
        )
        assert [row["tool"] for row in resp.json()] == ["search_knowledge"]

        bad = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "x", "action": "explode"}]},
            headers=ACCOUNT,
        )
        assert bad.status_code == 422
        dup = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {"tool": "x", "action": "auto"},
                    {"tool": "x", "action": "deny"},
                ]
            },
            headers=ACCOUNT,
        )
        assert dup.status_code == 422


def test_policies_are_account_scoped(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "*", "action": "deny"}]},
            headers=ACCOUNT,
        )
        assert client.get("/api/v1/approval-policies", headers=OTHER).json() == []


def test_load_and_replace_policies(tmp_path) -> None:
    """服务层往返:替换后按工具名排序返回,空提交即清空。"""
    with _tool_client(tmp_path, FakeLLM([])) as client:
        client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {"tool": "b_tool", "action": "deny"},
                    {"tool": "a_tool", "action": "auto"},
                ]
            },
            headers=ACCOUNT,
        )
        rows = client.get("/api/v1/approval-policies", headers=ACCOUNT).json()
        assert rows == [
            {
                "tool": "a_tool",
                "action": "auto",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            },
            {
                "tool": "b_tool",
                "action": "deny",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            },
        ]
        client.put("/api/v1/approval-policies", json={"policies": []}, headers=ACCOUNT)
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []


# ---- 端点:参数列往返 + 校验 ----


def test_policy_argument_fields_roundtrip_and_validation(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        resp = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "deploy",
                        "action": "auto",
                        "arg_name": "env",
                        "arg_match": "prefix",
                        "arg_value": "stag",
                    },
                    {"tool": "deploy", "action": "deny"},
                ]
            },
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        rows = client.get("/api/v1/approval-policies", headers=ACCOUNT).json()
        # 排序键 tool, arg_name:无条件行("")在参数行("env")之前
        assert rows == [
            {
                "tool": "deploy",
                "action": "deny",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            },
            {
                "tool": "deploy",
                "action": "auto",
                "arg_name": "env",
                "arg_match": "prefix",
                "arg_value": "stag",
            },
        ]

        invalid = [
            # arg_name 非空但缺 arg_match/arg_value
            [{"tool": "t", "action": "auto", "arg_name": "x"}],
            # 非法 arg_match
            [
                {
                    "tool": "t",
                    "action": "auto",
                    "arg_name": "x",
                    "arg_match": "regex",
                    "arg_value": "1",
                }
            ],
            # arg_name 非空但 arg_value 为空
            [
                {
                    "tool": "t",
                    "action": "auto",
                    "arg_name": "x",
                    "arg_match": "exact",
                    "arg_value": "",
                }
            ],
            # arg_name 为空却给了 arg_match(三列必须同空)
            [
                {
                    "tool": "t",
                    "action": "auto",
                    "arg_name": "",
                    "arg_match": "exact",
                    "arg_value": "1",
                }
            ],
        ]
        for policies in invalid:
            resp = client.put(
                "/api/v1/approval-policies",
                json={"policies": policies},
                headers=ACCOUNT,
            )
            assert resp.status_code == 422, policies

        # 唯一键 = (tool, arg_name, arg_match, arg_value):同工具不同参数条件可共存
        ok = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "t",
                        "action": "auto",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "staging",
                    },
                    {
                        "tool": "t",
                        "action": "deny",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "prod",
                    },
                ]
            },
            headers=ACCOUNT,
        )
        assert ok.status_code == 200
        dup = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "t",
                        "action": "auto",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "staging",
                    },
                    {
                        "tool": "t",
                        "action": "deny",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "staging",
                    },
                ]
            },
            headers=ACCOUNT,
        )
        assert dup.status_code == 422
