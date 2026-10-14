"""知识库端点:ingest/列表/详情/删除、租户隔离、检索排序。"""

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, _ingest

CONTRACT = "本周与客户 A 的合同评审安排在周四下午,需要准备报价单。"
BUDGET = "财务预算上限说明:本季度市场费用不超过 50 万,超支需 VP 审批。"


def test_ingest_chunks_and_lists(client: TestClient) -> None:
    created = _ingest(client, "客户 A 合同", CONTRACT)
    assert created.status_code == 201
    document = created.json()
    assert document["title"] == "客户 A 合同"
    assert document["chunk_count"] >= 1
    assert document["size_bytes"] == len(CONTRACT.encode())
    assert document["embedding_model"] == "hashing-256"  # 本地提供方模型名

    listed = client.get("/v1/knowledge/documents", headers=TENANT).json()
    assert [item["id"] for item in listed] == [document["id"]]

    fetched = client.get(
        f"/v1/knowledge/documents/{document['id']}", headers=TENANT
    ).json()
    assert fetched["source"] == "manual"


def test_tenant_isolation(client: TestClient) -> None:
    document_id = _ingest(client, "客户 A 合同", CONTRACT).json()["id"]
    other = OTHER

    assert client.get("/v1/knowledge/documents", headers=other).json() == []
    assert (
        client.get(f"/v1/knowledge/documents/{document_id}", headers=other).status_code
        == 404
    )
    # 别的租户检索不到
    hits = client.post(
        "/v1/knowledge/search", json={"query": "合同"}, headers=other
    ).json()["hits"]
    assert hits == []


def test_delete_removes_document_and_chunks(client: TestClient) -> None:
    document_id = _ingest(client, "客户 A 合同", CONTRACT).json()["id"]

    assert (
        client.delete(
            f"/v1/knowledge/documents/{document_id}", headers=TENANT
        ).status_code
        == 204
    )
    assert (
        client.get(f"/v1/knowledge/documents/{document_id}", headers=TENANT).status_code
        == 404
    )
    hits = client.post(
        "/v1/knowledge/search", json={"query": "合同"}, headers=TENANT
    ).json()["hits"]
    assert hits == []  # 分块一并清理


def test_search_ranks_by_similarity(client: TestClient) -> None:
    _ingest(client, "客户 A 合同", CONTRACT)
    _ingest(client, "预算说明", BUDGET)

    body = client.post(
        "/v1/knowledge/search", json={"query": "合同评审报价"}, headers=TENANT
    ).json()
    assert body["provider"] == "local"
    assert body["model"] == "hashing-256"
    assert body["hits"], "应有命中"
    assert body["hits"][0]["title"] == "客户 A 合同"
    assert body["hits"][0]["score"] > 0
    assert "合同" in body["hits"][0]["content"]


def test_search_limit_and_empty_corpus(client: TestClient) -> None:
    empty = client.post(
        "/v1/knowledge/search", json={"query": "任何"}, headers=TENANT
    ).json()
    assert empty["hits"] == []

    for index in range(3):
        _ingest(client, f"文档{index}", f"第 {index} 篇关于合同的内容")
    limited = client.post(
        "/v1/knowledge/search", json={"query": "合同", "limit": 2}, headers=TENANT
    ).json()
    assert len(limited["hits"]) == 2


def test_validation_and_missing_document(client: TestClient) -> None:
    assert _ingest(client, "", "内容").status_code == 422  # 标题必填
    assert _ingest(client, "标题", "").status_code == 422  # 正文必填
    assert client.get("/v1/knowledge/documents/nope", headers=TENANT).status_code == 404
    assert (
        client.get("/v1/knowledge/documents/nope", headers=TENANT).json()["code"]
        == "knowledge.document_not_found"
    )


def test_accepts_run_scoped_source(client: TestClient) -> None:
    """溯源来源 `run:<32 位 id>` 必须被接受(orchestrator 的自动入库消费者发它)。

    这是真链路冒烟抓到的集成 bug:`source` 原上限 32,放不下 36 字符的溯源串,
    消费者一直拿 422、outbox 反复重试。
    """
    source = f"run:{'a' * 32}"
    created = client.post(
        "/v1/knowledge/documents",
        json={"title": "自动入库的交付物", "content": "正文", "source": source},
        headers=TENANT,
    )
    assert created.status_code == 201
    assert created.json()["source"] == source


def test_document_not_found_details_key(client: TestClient) -> None:
    """404 的 details 键名是 document_id(由仓储 id_detail_key 声明,别改名)。"""
    response = client.get("/v1/knowledge/documents/nope", headers=TENANT)
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "knowledge.document_not_found"
    assert body["details"] == {"document_id": "nope"}
