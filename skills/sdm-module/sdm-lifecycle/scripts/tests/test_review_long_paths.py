"""Long supplemental filenames preserve atomic publication and immutability."""
import json
from pathlib import Path
import pytest
from proposal_review import once, supplied, supply
from review_storage import atomic_json, native_path, read_json
from task_pipeline import digest

def test_long_destination_round_trip_and_existing_value_is_immutable(tmp_path):
    parent = tmp_path / ('d' * max(1, 205 - len(str(tmp_path))))
    parent.mkdir()
    target = parent / ('supplement-' + 'f' * 64 + '.json')
    assert len(str(target)) > 260
    once(target, {'actual': 1})
    assert read_json(target) == {'actual': 1}
    once(target, {'actual': 1})
    with pytest.raises(ValueError, match='proposal_review_immutable_record_changed'):
        once(target, {'actual': 2})
    assert read_json(target) == {'actual': 1}

def test_original_supplement_reader_accepts_long_validated_view(tmp_path):
    from project_driver import file_digest
    source = tmp_path / 'evidence.txt'
    source.write_text('actual source\n')
    manifest = tmp_path / 'review-context.json'
    value = {'schema': 'sdm-review-context/v1', 'subject_digest': 'subject',
             **{k: 'control fixture' for k in ('goal', 'constraints', 'data', 'code_revision', 'configuration', 'history', 'unknowns')},
             'sources': [{'id': 'raw', 'kind': 'artifact', 'path': str(source), 'sha256': file_digest(source)}]}
    atomic_json(manifest, value)
    packet = {'shared_context': {'manifest_path': str(manifest), 'value': value}}
    query = {'request_digest': digest(packet), 'queries': [{'source_id': 'raw', 'question': 'Show original', 'start_line': 1, 'line_count': 1}]}
    folder = tmp_path / ('q' * max(1, 205 - len(str(tmp_path))))
    folder.mkdir()
    result = supply(packet, folder, query)
    assert len(result['supplement_path']) > 260
    assert supplied(packet, folder)[0]['value']['results'][0]['text'] == 'actual source'
