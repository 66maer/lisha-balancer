"""Exact ordinary-player/guide replay of generated spoken policy examples."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading

import numpy as np
import pytest

from lisha_overlay.strategy_catalogue import Catalogue
from lisha_overlay.spoken_catalogue import SpokenCatalogue
from lisha_overlay.solver import BuffState,receive_buff

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def catalogue():
    base=ROOT/'src/lisha_overlay/assets/strategy'
    return SpokenCatalogue(Catalogue(base),base)


@pytest.mark.parametrize("policy", ["histogram", "spoken"])
@pytest.mark.parametrize("invalid", [True, 1.0, float("nan"), "1", None])
def test_catalogue_rejects_non_integer_populations(catalogue, policy, invalid):
    selected = catalogue.old if policy == "histogram" else catalogue
    with pytest.raises(ValueError):
        selected.solve([0, 0, 24, 0, invalid, 0, 0], "all")


def test_catalogue_preserves_numpy_integer_populations(catalogue):
    counts = [np.int64(value) for value in [0, 0, 24, 0, 1, 0, 0]]
    assert catalogue.solve(counts, "all")["index"] == catalogue.solve([0, 0, 24, 0, 1, 0, 0], "all")["index"]


def final_state(value,fan,hits):
    state=BuffState(value)
    if fan:state=receive_buff(state,'red').state
    for _ in range(hits):state=receive_buff(state,'black').state
    return state


def test_generated_isolation_stays_blocked_during_subsequent_whole_raid_circles():
    state=final_state(-3,True,3)
    assert state.value==-2 and state.isolated
    assert final_state(3,False,3).value==2


def test_entire_raid_can_take_fan_without_self_classifying(catalogue):
    data=catalogue.solve([0,0,24,0,1,0,0],'all')
    assert data['source']=='bulk' and data['body_fan']
    assert data['named_count']==3 and data['exception_count']==0
    assert data['fan_prompt']=='全团吃扇'
    assert not any(label in data['prompt'] for label in ('红一','黑一'))
    assert abs(data['final_difference'])<=2


def test_entire_raid_can_stack_at_main_point(catalogue):
    data=catalogue.solve([0,0,0,0,25,0,0],'circle')
    assert data['source']=='bulk' and data['body_hits']==1 and not data['body_fan']
    assert data['named_count']==3 and data['final_difference']==0
    assert data['body_prompt']=='大团去主点抱团'
    assert data['guide_prompt']=='甲去主点，乙单走，丙去主点旁'
    assert '1→3' not in data['prompt'] and '1→3' not in data['guide_prompt']


def test_two_circle_instructions_use_main_and_nearby_locations(catalogue):
    data=catalogue.solve([0,0,0,3,4,18,0],'all')
    assert data['source']=='bulk' and data['body_hits']==2
    assert '大团主点抱团，出圈后一起去旁点' in data['body_prompt']
    assert data['extra_named_count']<=5 and data['named_count']<=8


def test_budget_is_five_extra_people_and_old_total_five_cases_are_no_longer_rejected(catalogue):
    assert catalogue.extra_budget==5 and catalogue.total_budget==8 and not catalogue.guides_included
    for mode,table in catalogue.tables.items():
        assert np.all(table['named']<=8)
        assert np.count_nonzero(table['source'])==np.count_nonzero(catalogue.old.tables[mode]['level']<255)
        ids=np.flatnonzero(table['named']>5)
        assert len(ids)>0
        data=catalogue.solve(catalogue.counts[int(ids[0])].tolist(),mode)
        assert data['source']!='none' and 2<data['extra_named_count']<=5
        assert not data['guides_included'] and data['named_count']==data['extra_named_count']+3


def test_five_replacements_keep_all_five_actions_in_the_replay_path(catalogue):
    counts=[0,0,0,10,0,5,10]
    index=catalogue.old.solve(counts,'all')['index'];table=catalogue.bulk['all']
    assert int(table['baseline'][index])==0
    codes=[int(table[f'exception{i}'][index]) for i in range(1,6)]
    assert codes==[43]*5  # Five distinct red2 players each take three black hits.
    g=catalogue.profiles[0,int(table['profile'][index])]
    assert g.tolist()==[0,0,0,3,0,0,0] and int(table['final'][index])==2


def test_payloads_replay_and_guides_are_counted_once(catalogue):
    rng=np.random.default_rng(506)
    for mode in ('all','circle'):
        ids=rng.choice(len(catalogue.counts),size=500,replace=False)
        for index in ids:
            data=catalogue.solve(catalogue.counts[int(index)].tolist(),mode)
            if data['source']=='none':continue
            assert data['extra_named_count']==len(data['exceptions'])<=5
            assert data['named_count']==3+len(data['exceptions'])<=8
            assert len({p['alias'] for p in data['guide_roles']+data['exceptions']})==data['named_count']
            total=0
            for p in data['guide_roles']:
                if mode=='circle':assert not p['fan']
                state=final_state(p['state'],p['fan'],1);total+=(state.value>0)-(state.value<0)
            if data['source']=='bulk':
                for si,(count,g) in enumerate(zip(data['counts'],data['guide_counts'])):
                    special=[e for e in data['exceptions'] if e['state']==si-3]
                    ordinary=count-g-len(special)
                    if ordinary:
                        state=final_state(si-3,data['body_fan'],data['body_hits'])
                        total+=ordinary*((state.value>0)-(state.value<0))
                    for e in special:
                        actual=final_state(e['state'],e['fan'],e['hits'])
                        total+=(actual.value>0)-(actual.value<0)
            else:
                h=np.array(data['counts'])-np.array(data['guide_counts'])
                total+=sum(int(n)*((si>3)-(si<3)) for si,n in enumerate(h))
                for count,(value,fan,hits) in zip(data['quota'],((-1,True,0),(0,True,0),(0,False,1),(1,False,1),(1,False,2),(2,False,2),(2,False,3))):
                    actual=final_state(value,fan,hits)
                    total+=count*((actual.value>0)-(actual.value<0)-(value>0)+(value<0))
            assert total==data['final_difference'] and abs(total)<=2


@pytest.fixture(scope="module")
def lab_server(catalogue):
    from tools.strategy_lab import create_handler

    handler = create_handler(catalogue.old, catalogue)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=5)


def lab_request(address, path, body=None):
    connection = HTTPConnection(*address, timeout=10)
    try:
        connection.request("GET" if body is None else "POST", path, body=body)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


@pytest.mark.parametrize("path", ["/", "/catalogue"])
def test_lab_pages_include_a_click_through_author_watermark(lab_server, path):
    status, page = lab_request(lab_server, path)
    assert status == 200
    assert "华乾·丐箩箩" in page
    assert "pointer-events:none" in page


@pytest.mark.parametrize("policy", ["bulk", "buff"])
def test_lab_catalogue_routes_use_bundled_data(lab_server, policy):
    status, payload = lab_request(lab_server, f"/api/summary?policy={policy}")
    assert status == 200
    assert set(json.loads(payload)["modes"]) == {"all", "circle"}

    status, payload = lab_request(
        lab_server,
        f"/api/solve?policy={policy}&mode=all&counts=0,0,24,0,1,0,0",
    )
    assert status == 200
    assert abs(json.loads(payload)["final_difference"]) <= 2

    status, payload = lab_request(
        lab_server, f"/api/example?policy={policy}&mode=all&family=0"
    )
    assert status == 200
    assert sum(json.loads(payload)["counts"]) == 25


@pytest.mark.parametrize("path", [
    "/api/summary?policy=invalid",
    "/api/solve?policy=",
    "/api/solve?mode=invalid&counts=0,0,24,0,1,0,0",
    "/api/solve?counts=0,0,24,0,2,0,0",
    "/api/example?family=not-a-number",
    "/api/example?family=-1",
])
def test_lab_rejects_invalid_query_parameters(lab_server, path):
    status, payload = lab_request(lab_server, path)
    assert status == 400
    assert json.loads(payload)["error"]


@pytest.mark.parametrize("body", [
    b"[]",
    b"{",
    b"\xff",
    b'{"mode": [], "roster": []}',
    b" " * 20_001,
])
def test_lab_rejects_invalid_step_requests(lab_server, body):
    status, payload = lab_request(lab_server, "/api/steps", body)
    assert status == 400
    assert json.loads(payload)["error"]


def test_lab_steps_route_returns_an_executable_plan(lab_server):
    from lisha_overlay.step_strategy import roster_from_counts

    request = {"roster": roster_from_counts([0, 0, 24, 0, 1, 0, 0]), "mode": "all"}
    status, payload = lab_request(lab_server, "/api/steps", json.dumps(request))
    assert status == 200
    result = json.loads(payload)
    assert result["status"] == "solved"
    assert abs(result["final"]["difference"]) <= 2
