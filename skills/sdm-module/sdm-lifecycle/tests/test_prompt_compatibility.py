"""Names change in new prompt versions; old accepted bytes remain exact."""
import hashlib
import pytest
from proposal_dialogue import prompt
from review_guidance import iteration_guidance,iteration_guidance_v6,bind_iteration_guidance,PROMPT_VERSION
from model_review_bridge import render_prompt
EXPECTED = {'dialogue': {'1a': '5a52c678389cab5d0dfb8a05057e8e3b3634c555d9c1f7651e76b34ea471cf75', '1b': 'b3e626e0dc038ce73acf14b8a3c690878cc7362e428ed2e3f036c7018561c28a', '2a': 'f2761a38eabe054076b483d8a93d09b92a45a08cfc10fffb858858987d038eec', '2b': '94a17b0bf1d899916aa11ef62248902f7abb06bcbfcd5c3e3127fbaff0bea7dd', '3a': '799ffbd203568a7d9eb3c1df02d491cec859ed9eae0708b1a3d1e712732683dd', '3b': 'c31c23020b8eca9b24fd7a56a44cf29b4178be4815317d3c6b12493109d214da'}, 'guidance': {'plan': 'e373f2173017dd3b8077c26189a6549d62fc9452e0c935a7733189c613012797', 'result': '35a9e4384ba6087491bbe2afc4cc3a9250511f863aaf06ca738de1f649fa86b3'}, 'render_v6': 'eb2a8a1f10a1c4808c0852abf111655025ff56a5abefe6394b1cc406d1f46b34'}
sha=lambda value:hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()

@pytest.mark.parametrize('version,role',[(v,r) for v in (1,2,3) for r in ('a','b')])
def test_old_dialogue_is_byte_identical(version,role):
    assert sha(prompt(role,version))==EXPECTED['dialogue'][str(version)+role]

@pytest.mark.parametrize('purpose',['plan','result'])
def test_v6_guidance_replays_without_relabeling_or_upgrading(purpose):
    text=iteration_guidance_v6(purpose)
    assert sha(text)==EXPECTED['guidance'][purpose]
    original={'objective':'Retained original objective'}
    old={**original,'planning_prompt_version':'sdm-research-review/v6','planning_guidance':text}
    assert bind_iteration_guidance(original,old,purpose)==old
    with pytest.raises(ValueError,match='immutable_planning_request_changed'):
        bind_iteration_guidance({'objective':'Different objective'},old,purpose)

def test_v6_bridge_is_byte_identical():
    assert sha(render_prompt({'history':[],'evidence':{}},'sdm-research-review/v6'))==EXPECTED['render_v6']

@pytest.mark.parametrize('role',['a','b'])
def test_new_role_names_do_not_use_experimental_ab_alias(role):
    text=prompt(role,4)
    assert '生成—质询审议' in text and 'Generator–Critic' in text
    assert 'proposer A.' not in text and 'critic B.' not in text and 'each B suggestion' not in text

def test_new_guidance_uses_new_name_and_retains_decision_authority_boundary():
    assert PROMPT_VERSION=='sdm-research-review/v7'
    text=iteration_guidance()
    assert 'A/B' not in text and 'Generator–Critic' in text
    assert 'no execution authority' in text or 'does not grant execution authority' in text
