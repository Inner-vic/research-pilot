import hashlib
from research_model_runner import shared_evidence

def test_small_registered_observation_is_not_crowded_out_by_large_sources(tmp_path):
    sources = {}
    for index in range(8):
        p = tmp_path / ('large-%d.log' % index)
        p.write_text('large source text ' * 1300)
        sources[str(p)] = 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()
    critical = tmp_path / 'critical.json'
    critical.write_text('{"actual_score":0.8}')
    sources[str(critical)] = 'sha256:' + hashlib.sha256(critical.read_bytes()).hexdigest()
    job = tmp_path / 'job'
    job.mkdir()
    result = shared_evidence({}, {}, {}, sources, job)
    row = next(r for r in result['raw_sources'] if r['path'] == str(critical))
    assert row['inlined'] and row['omitted_bytes'] == 0 and row['text'] == critical.read_text()
    assert any(r.get('omitted_bytes', 0) for r in result['raw_sources'])
