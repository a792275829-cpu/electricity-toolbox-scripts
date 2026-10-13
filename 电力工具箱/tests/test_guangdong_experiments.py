from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / '广东电价预测'


class ForecastExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(PROJECT))

    @classmethod
    def tearDownClass(cls):
        if sys.path[0] == str(PROJECT):
            sys.path.pop(0)

    def sample(self):
        times = pd.date_range('2026-09-01', periods=40*96, freq='15min')
        return pd.DataFrame({'日期':times.normalize(), '时刻':times.strftime('%H:%M'),
                             '全省日前平均电价':times.day.astype(float),
                             '日前统调负荷(MW)':100000.+times.day,
                             '日前风电出力(MW)':1000., '日前光伏出力(MW)':2000.})

    def test_calendar_marks_adjusted_workday_and_holiday_recovery(self):
        from gd_price_forecast.calendar_features import calendar_features
        dates = pd.Series(pd.to_datetime(['2026-10-07','2026-10-08','2026-10-10','2025-10-08']))
        f = calendar_features(dates)
        self.assertEqual(1, f.loc[0,'is_holiday_last'])
        self.assertEqual(7, f.loc[0,'holiday_length'])
        self.assertEqual(1, f.loc[1,'post_holiday_workday'])
        self.assertEqual(1, f.loc[2,'is_adjusted_workday'])
        self.assertEqual(1, f.loc[2,'is_workday'])
        self.assertEqual(8, f.loc[3,'holiday_length'])

    def test_unknown_calendar_is_explicit(self):
        from gd_price_forecast.calendar_features import calendar_features
        f = calendar_features(pd.Series(pd.to_datetime(['2027-01-01'])))
        self.assertEqual(0, f.loc[0,'calendar_known'])
        self.assertTrue(pd.isna(f.loc[0,'is_workday']))

    def test_price_lag_uses_calendar_date(self):
        from gd_price_forecast.features import build_features
        f = self.sample()
        f = f.loc[f['日期'].ne(pd.Timestamp('2026-09-06'))]
        X,_,i = build_features(f,exogenous_mode='none')
        self.assertTrue(X.loc[i['日期'].eq('2026-09-07'),'price_lag_1d'].isna().all())
        self.assertTrue(X.loc[i['日期'].eq('2026-09-08'),'price_lag_1d'].eq(7).all())

    def test_missing_entire_day_is_rejected(self):
        from gd_price_forecast.data import validate_quarter_hour_grain
        f=self.sample(); f=f.loc[f['日期'].ne(pd.Timestamp('2026-09-06'))]
        with self.assertRaisesRegex(ValueError,'日期不连续'):
            validate_quarter_hour_grain(f)

    def test_non_quarter_hour_slots_are_rejected(self):
        from gd_price_forecast.data import validate_quarter_hour_grain
        f=self.sample(); f.loc[f['时刻'].eq('00:00'),'时刻']='00:07'
        with self.assertRaisesRegex(ValueError,'标准15分钟'):
            validate_quarter_hour_grain(f)

    def test_lagged_supply_future_does_not_need_current_day_values(self):
        from gd_price_forecast.features import build_features
        from train_d1_forecast import append_prediction_days
        f=append_prediction_days(self.sample(),1)
        X,_,i=build_features(f,exogenous_mode='lagged',holidays=True,supply=True)
        future=i['日期'].eq(i['日期'].max())
        self.assertTrue(X.loc[future,'日前统调负荷(MW)__lag_1d'].notna().all())
        self.assertTrue(X.loc[future,'风光出力合计参考MW__lag_1d'].eq(3000).all())
        # Changing the unknown target day's exogenous fields cannot alter any
        # target-day features used by the historical-only route.
        f.loc[future,'日前统调负荷(MW)']=9e9
        altered,_,_=build_features(f,exogenous_mode='lagged',holidays=True,supply=True)
        pd.testing.assert_frame_equal(X.loc[future],altered.loc[future])

    def test_operational_feature_schema_excludes_actual_and_target_proxies(self):
        from gd_price_forecast.features import build_features
        f=self.sample(); f['实际统调负荷(MW)']=9e9; f['未来电价代理']=f['全省日前平均电价']
        X,_,_=build_features(f,exogenous_mode='lagged')
        self.assertFalse(any('实际' in c or '代理' in c for c in X))

    def test_training_window_never_uses_validation_labels(self):
        from gd_price_forecast.experiments import training_indices
        f=self.sample(); before=pd.Timestamp('2026-10-01')
        idx=training_indices(f[['日期','时刻']],f['全省日前平均电价'],before,3)
        self.assertLess(f.iloc[idx]['日期'].max(),before)

    def test_mix_can_fall_back_when_previous_day_price_is_missing(self):
        from gd_price_forecast.experiments import _mix
        np.testing.assert_allclose(_mix(np.array([300.,400.]),np.array([np.nan,200.]),.7),[300.,340.])

    def test_all_empty_features_remain_stable_with_quantile_model(self):
        from gd_price_forecast.modeling import fit_model,predict_model
        X=pd.DataFrame({'x':np.arange(100.),'absent':np.nan})
        m=fit_model(X,pd.Series(np.arange(100.)),dict(kind='hist_gradient_boosting',params=dict(
            loss='quantile',quantile=.1,max_iter=3,early_stopping=False)))
        self.assertTrue(np.isfinite(predict_model(m,X.iloc[-2:])).all())

    def test_publication_cutoff_filters_future_versions_and_actuals(self):
        from gd_price_forecast.future_inputs import filter_forecast_payload
        records=[{'date':'2026-10-12','versionDate':time,'loadVal':[v]*96} for time,v in (
            ('2026-10-11 10:00:00',100),('2026-10-11 13:00:00',900))]
        payload={'data':{'dataNetLoadDTOList':[{'loadType':'1','forecastPeriodList':records,
                    'actualPeriodList':[records[-1]]}]}}
        result,pub=filter_forecast_payload(payload,'2026-10-12','2026-10-11T12:00:00+08:00')
        item=result['data']['dataNetLoadDTOList'][0]
        self.assertEqual([],item['actualPeriodList'])
        self.assertEqual([100]*96,item['forecastPeriodList'][0]['loadVal'])
        self.assertEqual(1,len(pub))

    def test_future_snapshot_does_not_copy_target_price(self):
        from gd_price_forecast.future_inputs import attach_forecast_snapshot
        f=self.sample()
        day=f['日期'].max()+pd.Timedelta(days=1)
        snap=f.iloc[-96:].copy(); snap['日期']=day; snap['全省日前平均电价']=99999
        result=attach_forecast_snapshot(f,snap,day)
        future=result.loc[result['日期'].eq(day)]
        self.assertTrue(future['全省日前平均电价'].isna().all())
        self.assertTrue(future['日前统调负荷(MW)'].notna().all())

    def test_production_rejects_stale_source_before_training(self):
        from unittest import mock
        from train_d1_forecast import run_pipeline
        import tempfile
        with tempfile.TemporaryDirectory() as folder, mock.patch('train_d1_forecast.load_model_data',return_value=self.sample()):
            with self.assertRaisesRegex(ValueError,'数据过期'):
                run_pipeline('unused.xlsx',folder,as_of='2026-10-11')


if __name__=='__main__':
    unittest.main()
