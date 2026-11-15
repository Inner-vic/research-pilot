import pytest
from loop_contract import parameter_values

def test_numeric_enum_matches_model_schema_and_guard():
    domain = {'lr': {'type': 'number', 'minimum': .025, 'maximum': .2, 'enum': [.05, .15]}}
    parameter_values({'lr': .15}, domain)
    with pytest.raises(ValueError, match='variable_outside_choices'):
        parameter_values({'lr': .1}, domain)

@pytest.mark.parametrize('choices', [[], [True], [float('nan')], [.3]])
def test_invalid_numeric_choices_are_not_authority(choices):
    with pytest.raises(ValueError, match='finite_numeric_enum_choices_required'):
        parameter_values({'lr': .15}, {'lr': {'type': 'number', 'minimum': .025, 'maximum': .2, 'enum': choices}})
