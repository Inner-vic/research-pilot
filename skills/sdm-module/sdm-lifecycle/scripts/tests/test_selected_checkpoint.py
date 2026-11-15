"""JSON/control checks of real workload validation; never import Torch or run ML."""
import importlib.util
from pathlib import Path
import pytest

source=Path(__file__).resolve().parents[2]/'assets/public-image-classification/workload.py'
spec=importlib.util.spec_from_file_location('public_workload_records',source)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
verify=module.verify_selected_checkpoint
CURVES=[{'epoch':1,'validation_accuracy':.72,'validation_loss':1.1},
        {'epoch':2,'validation_accuracy':.74,'validation_loss':.97},
        {'epoch':3,'validation_accuracy':.74,'validation_loss':.96}]


def test_earliest_maximum_and_replay_are_verified_separately():
    metadata={'epoch':2,'validation':{'accuracy':.74,'loss':.97}}
    assert verify(metadata,CURVES,2)['replay_verified'] is False
    result=verify(metadata,CURVES,2,{'samples':5000,'accuracy':.74,'loss':.9700001})
    assert result['epoch']==2 and result['metadata_verified'] and result['replay_verified']


@pytest.mark.parametrize('epoch,accuracy,loss,selected',[(3,.74,.96,3),(1,.72,1.1,2),(2,.73,.97,2),(2,.74,1.0,2)])
def test_wrong_epoch_tie_or_stored_score_fails(epoch,accuracy,loss,selected):
    with pytest.raises(RuntimeError,match='metadata_mismatch'):
        verify({'epoch':epoch,'validation':{'accuracy':accuracy,'loss':loss}},CURVES,selected)


@pytest.mark.parametrize('samples,accuracy,loss',[(5000,.73,.97),(4999,.74,.97),(5000,.74,.98),(5000,.74,float('nan'))])
def test_wrong_replay_support_accuracy_or_loss_fails(samples,accuracy,loss):
    with pytest.raises(RuntimeError,match='replay_mismatch'):
        verify({'epoch':2,'validation':{'accuracy':.74,'loss':.97}},CURVES,2,
               {'samples':samples,'accuracy':accuracy,'loss':loss})
