from canonical import digest
from specctl import _step_artifact_subjects


def test_g10_digest_is_bound_by_request_without_becoming_its_own_content():
    logical = {'artifact_id':'contracts','research_brief_id':'research'}
    candidate = {'subjects':{'workspace_id':'workspace',**logical},'content':{'metric':'actual-code'}}
    subjects = {**logical,'dataset_namespace':'personal','dataset_name':'dataset','dataset_version':'1',
                'dataset_version_uid':'version-uid','dataset_scope_digest':'sha256:'+'a'*64,
                'calibration_applicability':'applicable','calibration_validity_mode':'snapshot',
                'evaluator_digest':digest(candidate)}
    assert _step_artifact_subjects(3,'workspace',subjects) == candidate['subjects']
    changed = {**candidate,'content':{'metric':'substitution'}}
    assert digest(changed) != subjects['evaluator_digest']
    # The selectors remain in the original request; only authored contract
    # identity excludes them. Exact handoff/manifest checks still consume them.
    assert subjects['dataset_version_uid'] == 'version-uid'
    assert subjects['evaluator_digest'] == digest(candidate)


def test_other_steps_and_logical_subjects_do_not_lose_binding():
    subjects={'artifact_id':'contracts','experiment_id':'experiment','run_id':'run','evaluator_digest':'bound'}
    assert _step_artifact_subjects(7,'workspace',subjects) == {'workspace_id':'workspace',**subjects}
    assert _step_artifact_subjects(3,'workspace',subjects) == {
        'workspace_id':'workspace','artifact_id':'contracts','experiment_id':'experiment','run_id':'run'}
