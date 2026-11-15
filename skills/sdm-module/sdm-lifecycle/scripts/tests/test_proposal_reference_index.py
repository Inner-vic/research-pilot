"""Large shared contexts stay within the existing proposal evidence contract."""
from pathlib import Path
import pytest
import proposal_review as review
from proposal_model_runner import canonical
from research_model_receipts import seal
from review_storage import atomic_json, read_json
from task_pipeline import digest


def test_full_shared_index_preserves_over32_sources_in_one_proposal_reference(tmp_path):
    folder = tmp_path / 'review'
    call = folder / 'model-calls/0001-a-0001'
    sources = [{'id': 'source-' + str(n), 'path': str(tmp_path / ('s' + str(n) + '.json')),
                'sha256': 'sha256:control'} for n in range(40)]
    shared = {'packet': {'context': {'sources': sources}}, 'raw_sources': []}
    index = folder / 'shared-evidence.json'
    atomic_json(index, seal({'schema': 'sdm-shared-research-evidence/v1', 'binding': {}, 'evidence': shared}))
    value = {'model': 'control-model', 'evidence': {'shared': shared, 'subject_digest': 'bound-subject', 'revision': 1}}
    raw = {key: 'Control explanation with no experiment claim' for key in review.FIELDS}
    output = canonical('a', raw, value, call)
    assert len(output['evidence']) == 1 and output['evidence'][0]['path'] == str(index)
    assert read_json(index)['evidence']['packet']['context']['sources'] == sources
    review.validate_proposal(output, 'bound-subject', True)
    changed = read_json(index)
    changed['evidence']['packet']['context']['sources'] = sources[:-1]
    atomic_json(index, seal(changed))
    with pytest.raises(ValueError, match='shared_evidence_index_changed'):
        canonical('a', raw, value, call)
