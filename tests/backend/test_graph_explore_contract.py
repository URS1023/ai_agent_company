"""通过真实图谱 HTTP 验证中心保留、逐层探索和实际截断；仅使用隔离 SQLite。"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.modules.knowledge_graph.models import GraphEntity, GraphRelation

from test_graph_property_contracts import GRAPHS, entity, model_graph
from test_knowledge_graph import result


@pytest.fixture
def network(client, administrator):
    """按最远端到中心的反向顺序创建链，避免创建顺序恰好掩盖探索缺陷。"""
    model = model_graph(client, administrator)
    far = result(entity(client, administrator, model, "far-target", target=True), 201)
    middle = result(entity(client, administrator, model, "middle-source"), 201)
    near = result(entity(client, administrator, model, "seed-near", target=True), 201)
    center = result(entity(client, administrator, model, "seed-center"), 201)
    for source, target in [(middle, far), (middle, near), (center, near)]:
        result(client.post(f"{GRAPHS}/{model.graph['id']}/relations", headers=administrator, json={
            "relationTypeId": model.relation["id"], "sourceEntityId": source["id"],
            "targetEntityId": target["id"],
        }), 201)
    return SimpleNamespace(model=model, far=far, middle=middle, near=near, center=center)


def explore(client, headers, network, **parameters):
    """同时确认响应关系不引用被裁掉或不可见的节点。"""
    view = result(client.get(f"{GRAPHS}/{network.model.graph['id']}/explore", headers=headers, params=parameters))
    ids = {node["id"] for node in view["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in view["edges"])
    return view


@pytest.mark.parametrize("depth,limit,expected,truncated", [
    (0, 1, ["center"], False),
    (1, 2, ["center", "near"], False),
    (2, 2, ["center", "near"], True),
    (2, 3, ["center", "near", "middle"], False),
    (3, 3, ["center", "near", "middle"], True),
    (3, 4, ["center", "near", "middle", "far"], False),
])
def test_explicit_center_precedes_old_neighbors_and_depth_bounds_truncation(
        client, administrator, network, depth, limit, expected, truncated):
    view = explore(client, administrator, network, entityId=network.center["id"], depth=depth, limit=limit)
    assert [row["id"] for row in view["nodes"]] == [getattr(network, name)["id"] for name in expected]
    assert view["truncated"] is truncated


@pytest.mark.parametrize("limit,truncated", [(3, True), (4, False), (5, False)])
def test_unfocused_list_reports_only_actually_omitted_nodes(client, administrator, network, limit, truncated):
    view = explore(client, administrator, network, limit=limit)
    assert len(view["nodes"]) == min(limit, 4)
    assert view["truncated"] is truncated


@pytest.mark.parametrize("depth,limit,expected,truncated", [
    (0, 1, ["near"], True),
    (0, 2, ["near", "center"], False),
    (1, 3, ["near", "center", "middle"], False),
])
def test_all_matching_seeds_precede_neighbors(client, administrator, network, depth, limit, expected, truncated):
    view = explore(client, administrator, network, query="seed", depth=depth, limit=limit)
    assert [row["id"] for row in view["nodes"]] == [getattr(network, name)["id"] for name in expected]
    assert view["truncated"] is truncated


def test_no_match_does_not_fall_back_to_unrelated_graph_rows(client, administrator, network):
    view = explore(client, administrator, network, query="不存在的检索词", depth=4, limit=1)
    assert view["nodes"] == [] and view["edges"] == [] and view["truncated"] is False


def test_empty_and_isolated_graph_do_not_claim_truncation(client, administrator):
    model = model_graph(client, administrator)
    empty = SimpleNamespace(model=model)
    view = explore(client, administrator, empty, depth=4, limit=1)
    assert view["nodes"] == [] and view["edges"] == [] and view["truncated"] is False
    only = result(entity(client, administrator, model, "唯一设备"), 201)
    view = explore(client, administrator, empty, entityId=only["id"], depth=4, limit=1)
    assert [row["id"] for row in view["nodes"]] == [only["id"]]
    assert view["edges"] == [] and view["truncated"] is False


@pytest.mark.parametrize("scope", ["other_graph", "deleted"])
def test_foreign_or_deleted_center_is_not_a_valid_start(client, administrator, network, db, scope):
    if scope == "other_graph":
        model = model_graph(client, administrator)
        start = result(entity(client, administrator, model, "另一图的起点"), 201)["id"]
    else:
        start = network.center["id"]
        # 模拟旧版本遗留软删节点及未同步清理的边，不扩大当前接口删除权限。
        db.get(GraphEntity, start).status = "deleted"
        db.commit()
    response = client.get(f"{GRAPHS}/{network.model.graph['id']}/explore", headers=administrator,
                          params={"entityId": start, "depth": 4, "limit": 1})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("kind", ["entity", "relation"])
def test_deleted_nodes_or_edges_cannot_bridge_to_active_neighbors(client, administrator, network, db, kind):
    if kind == "entity":
        db.get(GraphEntity, network.near["id"]).status = "deleted"
    else:
        edge = db.scalar(select(GraphRelation).where(
            GraphRelation.graph_id == network.model.graph["id"],
            GraphRelation.source_entity_id == network.center["id"],
        ))
        edge.status = "deleted"
    db.commit()
    view = explore(client, administrator, network, entityId=network.center["id"], depth=4, limit=1)
    assert [row["id"] for row in view["nodes"]] == [network.center["id"]]
    assert view["edges"] == [] and view["truncated"] is False


def test_cycles_do_not_duplicate_nodes_or_misreport_full_coverage(client, administrator, network):
    result(client.post(f"{GRAPHS}/{network.model.graph['id']}/relations", headers=administrator, json={
        "relationTypeId": network.model.relation["id"], "sourceEntityId": network.center["id"],
        "targetEntityId": network.far["id"],
    }), 201)
    view = explore(client, administrator, network, entityId=network.center["id"], depth=4, limit=4)
    assert [row["id"] for row in view["nodes"]] == [
        network.center["id"], network.far["id"], network.near["id"], network.middle["id"],
    ]
    assert len(view["edges"]) == 4 and view["truncated"] is False


def test_tied_creation_times_keep_stable_seed_and_breadth_order(client, administrator, network, db):
    for row in db.scalars(select(GraphEntity).where(GraphEntity.graph_id == network.model.graph["id"])):
        row.created_at = datetime(2026, 1, 1)
    db.commit()
    view = explore(client, administrator, network, limit=4)
    assert [row["id"] for row in view["nodes"]] == sorted(
        getattr(network, name)["id"] for name in ["far", "middle", "near", "center"]
    )
    view = explore(client, administrator, network, entityId=network.center["id"], depth=3, limit=4)
    assert [row["id"] for row in view["nodes"]] == [
        network.center["id"], network.near["id"], network.middle["id"], network.far["id"],
    ]
