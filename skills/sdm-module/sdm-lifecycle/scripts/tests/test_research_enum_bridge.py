"""Original domain -> ModelRunner schema for actual model/initialization choices."""
import pytest
from loop_contract import parameter_values
from research_model_runner import parameters
from model_runner import request


def test_original_categorical_domain_becomes_typed_transport_request():
    domain={'initialization':{'type':'enum','choices':['imagenet-cifar-center','random']}}
    schema=parameters(domain)
    for choice in domain['initialization']['choices']:
        parameter_values({'initialization':choice},domain)
        assert choice in schema['properties']['initialization']['enum']
    wire={'schema':'sdm-model-request/v1','purpose':'result-analysis','contract_id':'sdm-result-analysis/v1',
        'model':'control-model','evidence':{'choices':domain},'prompt':'JSON control, no actual invocation',
        'output_schema':schema,'input_kind':'text'}
    assert request(wire)['output_schema']==schema
    with pytest.raises(ValueError,match='outside_choices'):
        parameter_values({'initialization':'unreviewed-model'},domain)
    assert schema['properties']['initialization']['type']=='string'


def test_mixed_original_enum_preserves_types_and_rejects_nonfinite_domain():
    domain={'mode':{'type':'enum','choices':[True,1,'one',.5]}}
    schema=parameters(domain)['properties']['mode']
    assert {row['type'] for row in schema['anyOf']}=={'boolean','integer','string','number'}
    with pytest.raises(ValueError):parameters({'mode':{'type':'enum','choices':[float('nan')]}})
