import numpy as np
import pytest
from scipy.special import expit, logit
from equifire import risk_calibration as rc


def data(seed=1):
    rng=np.random.default_rng(seed)
    true=rng.uniform(.02,.8,12000)
    raw=expit(2.5*logit(true)+.7)
    return raw,(rng.random(len(raw))<true).astype(int),np.repeat(np.arange(40),300)


def test_separate_sample_calibration_improves_known_distortion():
    params=rc.fit_horizon(*data())
    p,y,g=data(2);out=rc.transform(p,params)
    assert params['supported'] and params['slope']>=0
    assert np.all(np.diff(rc.transform(np.linspace(0,1,100),params))>=0)
    assert rc.metrics(out,y,g)['brier']<rc.metrics(p,y,g)['brier']
    assert rc.metrics(out,y,g)['log_loss']<rc.metrics(p,y,g)['log_loss']


def test_each_group_has_equal_total_weight():
    g=np.array([0,0,1,1,1,1,2]);w=rc.group_weights(g)
    assert [w[g==k].sum() for k in range(3)]==[1,1,1]


def test_insufficient_support_never_claims_calibration():
    p=np.linspace(0,1,100);y=np.arange(100)%2
    params=rc.fit_horizon(p,y,np.zeros(100))
    assert not params['supported']
    np.testing.assert_array_equal(rc.transform(p,params),p)


def test_wrong_model_and_future_calibration_are_rejected():
    a={'version':1,'source_sha256':'abc','calibration_outcomes_through_year':2024,
       'horizons':{'1':{'supported':True,'slope':.5,'intercept':0}}}
    with pytest.raises(ValueError,match='blender'):rc.apply([.5],1,a,source_sha256='different',prediction_year=2025,observation_mode='archive')
    with pytest.raises(ValueError,match='earlier'):rc.apply([.5],1,a,source_sha256='abc',prediction_year=2024,observation_mode='archive')
    assert rc.apply([.5],1,a,source_sha256='abc',prediction_year=2025,observation_mode='archive')[0]==.5
    with pytest.raises(ValueError,match='horizon'):rc.apply([.5],4,a,source_sha256='abc',prediction_year=2025,observation_mode='archive')


def test_missing_cells_stay_missing_and_invalid_fit_is_rejected():
    out=rc.transform(np.array([np.nan,.2]),{'supported':True,'slope':1,'intercept':0})
    assert np.isnan(out[0]) and out[1]==pytest.approx(.2)
    with pytest.raises(ValueError):rc.fit_horizon([np.nan],[1],[0])
    with pytest.raises(ValueError):rc.fit_horizon([.5],[2],[0])


def test_reliability_bins_include_zero_and_one():
    m=rc.metrics(np.array([0.,1.]),np.array([0,1]),np.array([0,1]))
    assert m['brier']==0 and m['ece']==0
    assert m['reliability'][0]['cells']==1 and m['reliability'][-1]['cells']==1
    assert sum(x['weight'] for x in m['reliability'])==pytest.approx(1)


def test_bootstrap_uses_event_groups():
    raw=np.array([.9,.9,.8,.8]);cal=np.array([.1,.1,.2,.2]);y=np.zeros(4);g=np.array([0,0,1,1])
    ci=rc.paired_bootstrap(raw,cal,y,g,samples=100)
    assert ci['groups']==2 and ci['upper_95']<0


def test_cumulative_risk_and_case_isolation():
    p=np.array([.8,.2,.6,.1,.3]);h=np.array([1,2,3,2,3]);loc=np.array([0,0,0,1,1])
    np.testing.assert_allclose(rc.cumulative(p,h,loc),[.8,.8,.8,.1,.3])
    with pytest.raises(ValueError,match='Duplicate'):rc.cumulative([.1,.2],[1,1],[0,0])


def test_archive_calibration_cannot_be_used_for_live_mode():
    with pytest.raises(ValueError,match='live'):
        rc.apply([.5],1,{},source_sha256='abc',prediction_year=2026,observation_mode='live')


def test_date_cutoff_allows_honest_within_year_holdout_only():
    a={'version':1,'source_sha256':'abc','calibration_outcomes_through_year':2025,
       'calibration_outcomes_through_date':'2025-05-31',
       'horizons':{'1':{'supported':True,'slope':1,'intercept':0}}}
    kw=dict(source_sha256='abc',prediction_year=2025,observation_mode='archive')
    assert rc.apply([.5],1,a,prediction_date='2025-07-01',**kw)[0]==.5
    with pytest.raises(ValueError,match='earlier'):rc.apply([.5],1,a,prediction_date='2025-05-31',**kw)
