import pytest
from contracts import _calibration_uncertainty_valid, evaluate_scientific_decision
from specctl import _require_diagnostic_calibration_facts, CLIError


def test_unmeasured_is_explicit_and_only_applies_to_exact_diagnostic_snapshot():
    validity = dict(mode='snapshot',dataset_content_digest='sha256:'+'a'*64,
                    membership_digest='sha256:'+'b'*64,calibration_content_digest='sha256:'+'c'*64)
    value = dict(status='not-measured',scope='diagnostic-only',reason='Source contains no physical calibration accuracy report.')
    assert _calibration_uncertainty_valid(value,validity)
    for substitution in ({**value,'scope':'production'}, {**value,'bound':0}, {**value,'reason':'TODO'}):
        assert not _calibration_uncertainty_valid(substitution,validity)
    assert not _calibration_uncertainty_valid(value,{'from':'2026-01-01','to':'2026-02-01'})
    assert _calibration_uncertainty_valid({'rotation_deg':0.1},validity)
    assert not _calibration_uncertainty_valid({'rotation_deg':-0.1},validity)


def test_unmeasured_cannot_support_or_reject_hypothesis():
    calibration={'uncertainty':{'status':'not-measured','scope':'diagnostic-only'}}
    evidence={'protocol_invalid':False,'underpowered':False,'predicates':{'support':True,'reject':False}}
    with pytest.raises(CLIError, match='Unmeasured calibration'):
        _require_diagnostic_calibration_facts([calibration], evidence)
    evidence['underpowered']=True
    _require_diagnostic_calibration_facts([calibration], evidence)
    rules=dict(schema='sdm-scientific-rule/v1',inconclusive_rule='underpowered_or_no_decisive_result',
               invalid_rule='protocol_invalid',support_predicate='support',rejection_predicate='reject')
    result=evaluate_scientific_decision(rules,[{'run_id':'run','required_attempt_ids':['attempt']}],
        [{'run_id':'run','attempt_id':'attempt','terminal_status':'successful','accounted':True,'validity':'valid','system_failure':False}],evidence)
    assert result['outcome']=='inconclusive'
    _require_diagnostic_calibration_facts([{'uncertainty':{'rotation_deg':0.1}}],{**evidence,'underpowered':False})
