from contracts import _calibration_validity_valid, validate_schema, AssetBundle


def test_snapshot_is_closed_and_requires_all_actual_digests():
    value = dict(mode='snapshot', dataset_content_digest='sha256:'+'a'*64,
                 membership_digest='sha256:'+'b'*64, calibration_content_digest='sha256:'+'c'*64)
    assert _calibration_validity_valid(value)
    for key in value:
        broken = dict(value)
        broken.pop(key)
        assert not _calibration_validity_valid(broken)
    assert not _calibration_validity_valid({**value, 'to':'2099-01-01'})
    assert not _calibration_validity_valid({**value, 'membership_digest':'unknown'})


def test_source_interval_remains_supported_without_defaults():
    assert _calibration_validity_valid({'from':'2026-01-01','to':'2026-02-01'})
    assert not _calibration_validity_valid({'from':'2026-02-01','to':'2026-01-01'})
    assert not _calibration_validity_valid({})
