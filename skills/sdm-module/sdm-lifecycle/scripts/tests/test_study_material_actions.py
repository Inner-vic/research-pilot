from research_model_runner import output_schema

def test_retained_study_action_does_not_use_loop_execution_gate():
    schema = output_schema('a', {'devspace_study': {'allowed_variables': {'lr': {'type': 'number', 'minimum': .01, 'maximum': .2}}}},
        {'evidence': {'actual': 1}, 'allowed_variables': {}}, {})
    assert schema['properties']['action']['enum'] == ['stop-analysis']
    iteration = schema['properties']['next_step']['properties']['iteration']['anyOf'][0]['anyOf'][1]
    assert iteration['properties']['action']['enum'] == ['run-next-round', 'retry-round']

def test_real_loop_action_retains_original_contract():
    schema = output_schema('a', {}, {'evidence': {'actual': 1}, 'allowed_variables': {'lr': {'type': 'number'}}}, {})
    assert 'run-next-round' in schema['properties']['action']['enum']
