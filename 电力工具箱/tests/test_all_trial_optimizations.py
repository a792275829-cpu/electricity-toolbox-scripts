from __future__ import annotations
from contextlib import nullcontext
from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal
from openpyxl import Workbook
from toolbox.runtime import load_module
from toolbox_shared import native_excel as excel

ROOT = Path(__file__).resolve().parents[2]


class IncrementalMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=load_module('trial_incremental_tests',ROOT/'广东电价预测/update_gdfire_data.py')

    def compare(self,base,increment):
        original,inc=base.copy(deep=True),increment.copy(deep=True)
        with patch.dict(os.environ,TOOLBOX_FORECAST_MERGE='full'):
            expected=self.module.merge_data_frames(base,increment)
        with patch.dict(os.environ,TOOLBOX_FORECAST_MERGE='incremental'):
            actual=self.module.merge_data_frames(base,increment)
        assert_frame_equal(expected,actual,check_exact=True)
        assert_frame_equal(base,original,check_exact=True)
        assert_frame_equal(increment,inc,check_exact=True)
        return actual

    def test_incremental_append_overlap_nulls_and_column_order(self):
        base=pd.DataFrame({'值':[1.,float('nan'),3.], '日期':['2026-10-01']*3,'时刻':['00:00','00:15','00:30'], '标签':['甲','乙','丙']})
        increment=pd.DataFrame({'日期':['2026-10-01','2026-10-02'],'时刻':['00:00','00:00'],'值':[None,4.], '标签':['新',None]})
        actual=self.compare(base,increment)
        self.assertEqual(list(actual.columns),['日期','时刻','值','标签'])
        self.assertEqual(actual['值'].iloc[0],1.)
        self.assertEqual(actual['标签'].iloc[0],'新')
        self.compare(base,increment.iloc[1:])

    def test_duplicates_missing_keys_and_object_dtype_fall_back_exactly(self):
        base=pd.DataFrame({'日期':['2026-10-01']*2,'时刻':['00:00','00:15'],'值':[1.,2.]})
        inc=pd.DataFrame({'日期':['2026-10-01']*2,'时刻':['00:00']*2,'值':[5.,None]})
        self.compare(base,inc)
        self.compare(pd.concat([base,base.iloc[:1]],ignore_index=True),inc)
        invalid=base.copy();invalid.loc[0,'日期']=None
        self.compare(invalid,inc)
        obj=base.copy();obj['混合']=pd.Series([1,'文本'],dtype=object)
        objinc=inc.copy();objinc['混合']=pd.Series([None,True],dtype=object)
        self.compare(obj,objinc)

    def test_nullable_numeric_types_match_original(self):
        base=pd.DataFrame({'日期':['2026-10-01']*2,'时刻':['00:00','00:15'], '整数':pd.Series([1,None],dtype='Int64'), '布尔':pd.Series([True,None],dtype='boolean')})
        inc=pd.DataFrame({'日期':['2026-10-01','2026-10-02'],'时刻':['00:00','00:00'], '整数':pd.Series([None,2],dtype='Int64'),'布尔':pd.Series([False,True],dtype='boolean')})
        self.compare(base,inc)


@unittest.skipUnless(excel.native_binary(),'optional native helper unavailable')
class NativeSessionTests(unittest.TestCase):
    def workbook(self,folder,name='input.xlsx',value=1.25):
        path=Path(folder)/name;book=Workbook();book.active['A1']=value;book.save(path);book.close();return path

    def test_cached_values_are_isolated_and_changed_files_are_invalidated(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,TOOLBOX_XLSX_BACKEND='rust',TOOLBOX_EXCEL_SESSION='session'):
            path=self.workbook(folder)
            with excel.excel_session():
                first=excel.load_value_workbook(path)
                first.active._data['rows'][0][0]=999
                second=excel.load_value_workbook(path)
                self.assertEqual(second.active['A1'].value,1.25)
                self.workbook(folder,value=2.75)
                self.assertEqual(excel.load_value_workbook(path).active['A1'].value,2.75)
                state=excel._excel_session.get();child=state.process
            self.assertEqual(child.poll(),0)
            self.assertIsNone(excel._excel_session.get())

    def test_nested_scopes_are_shared_and_exceptions_close_child(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,TOOLBOX_XLSX_BACKEND='rust',TOOLBOX_EXCEL_SESSION='session'):
            path=self.workbook(folder)
            with self.assertRaisesRegex(RuntimeError,'stop'):
                with excel.excel_session():
                    excel.load_value_workbook(path)
                    outer=excel._excel_session.get();child=outer.process
                    with excel.excel_session():self.assertIs(excel._excel_session.get(),outer)
                    raise RuntimeError('stop')
            self.assertIsNotNone(child.poll())
            self.assertIsNone(excel._excel_session.get())

    def test_cache_is_bounded_and_legacy_helper_uses_oneshot(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,TOOLBOX_XLSX_BACKEND='rust',TOOLBOX_EXCEL_SESSION='session'):
            paths=[self.workbook(folder,f'{i}.xlsx',i) for i in range(5)]
            with excel.excel_session():
                for path in paths:excel.load_value_workbook(path)
                state=excel._excel_session.get()
                self.assertEqual(len(state.cache),4)
                self.assertLessEqual(state.cache_bytes,16*1024*1024)
            original=excel._oneshot_call
            def legacy(binary,operation,path,**params):
                if operation=='capabilities':raise ValueError('legacy helper')
                return original(binary,operation,path,**params)
            with patch.object(excel,'_oneshot_call',side_effect=legacy),excel.excel_session():
                self.assertEqual(excel.load_value_workbook(paths[1]).active['A1'].value,1)
                self.assertIsNone(excel._excel_session.get().process)

    def test_file_transport_keeps_excel_types_and_dataframe_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'frame.xlsx';book=Workbook();sheet=book.active;sheet.title='整合数据'
            sheet.append(['日期','文本','值','错误']);sheet.append([datetime(2026,10,20),' 中文\r\n<&> ',.125,'#DIV/0!'])
            sheet.append([None,'NA',None,'=1+1']);book.save(path);book.close()
            frames=[]
            for transport in ['pipe','file']:
                with patch.dict(os.environ,TOOLBOX_XLSX_BACKEND='rust',TOOLBOX_FRAME_TRANSPORT=transport):
                    frames.append(excel.read_model_frame(path))
            assert_frame_equal(*frames,check_exact=True)

    def test_auto_batch_reuses_only_when_inputs_repeat(self):
        report=load_module('trial_auto_batch_tests',ROOT/'电力工具脚本/电力交易分析工具/generate_electricity_report.py')
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,TOOLBOX_XLSX_BACKEND='rust',TOOLBOX_EXCEL_SESSION='auto'):
            first=self.workbook(folder,'one.xlsx',1)
            second=self.workbook(folder,'two.xlsx',2)
            def generate(path,out_path):
                book=excel.load_value_workbook(path)
                Path(out_path).write_text(str(book.active['A1'].value))
                book.close()
                return Path(out_path)
            original=excel.subprocess.Popen
            for paths,servers in [([first,second],0),([first,first,first],1)]:
                spawned=[]
                def spawn(*args,**kwargs):
                    value=original(*args,**kwargs);spawned.append((args[0],value));return value
                with patch.object(report,'generate_report',side_effect=generate),patch.object(excel.subprocess,'Popen',side_effect=spawn):
                    result=report.generate_batch(paths,Path(folder)/'output')
                self.assertEqual(len(result),len(paths))
                self.assertEqual(sum('--serve' in command for command,_ in spawned),servers)
                self.assertTrue(all(process.poll() is not None for _,process in spawned))
