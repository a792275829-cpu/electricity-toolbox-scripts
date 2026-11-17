"""Every live catalog operation reaches the existing validated worker contract.

The ProcessRunner boundary records requests rather than contacting websites or
uploading. Existing business tests and live verification cover the implementations.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch
from datetime import datetime

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱')]
from toolbox.service import ToolboxService, RESULT_PREFIX
from toolbox.runtime import ToolPaths
from toolbox.service_catalog import desktop_catalog
from toolbox.workflows import execute, validate, plan

CATALOG = desktop_catalog(ToolPaths(ROOT))

class BusinessRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.environment = patch.dict(os.environ, TOOLBOX_DATA_ROOT=str(self.root/'data'), TOOLBOX_CODE_ROOT=str(ROOT))
        self.environment.start()
        self.service = ToolboxService(ROOT)
        import openpyxl
        self.workbook = self.root/'出清情况（分公司2026.11.15）.xlsx'
        book=openpyxl.Workbook();book.active.title='新快报';book.active['A6']=datetime(2026,11,15);book.save(self.workbook);book.close()
        self.pdf=self.root/'运行日报.pdf';self.pdf.touch()
        self.topology=self.root/'拓扑.md';self.topology.write_text('# 验证')
    def tearDown(self):
        self.service.call('shutdown',{'cancel':True})
        self.environment.stop();self.temp.cleanup()
    def values(self, tool):
        p={field['key']:field['default'] for field in tool['fields']}
        for field in tool['fields']:
            key=field['key']
            if field['kind']=='files':p[key]=[str(self.workbook)]
            if field['kind']=='file':p[key]=str(self.workbook)
            if field['kind']=='directory':p[key]=str(self.root)
        for key in ('guihu','tuolian','haifeng'):
            if key+'_selected' in p:
                p[key+'_selected']=key=='guihu'
                file=self.root/f'功率收益-{dict(guihu="归湖",tuolian="鮀莲",haifeng="海风")[key]}.xlsx'
                file.touch();p[key+'_workbook']=str(file)
        if tool['id']=='daily-pdf-parser':p.update(pdf_path=str(self.pdf),output_path=str(self.root/'解析.xlsx'))
        if tool['id']=='report':p['template_report']=''
        if tool['id']=='section-analysis':p['topology_path']=str(self.topology)
        if tool['id']=='wps-writer':p['config']=json.dumps({'configs':[]});p['operation']='{}'
        return p
    def verify_route(self, tool, action):
        p=self.values(tool)
        with patch('toolbox.workflows.node_executable',return_value='node'),patch('toolbox.runtime.node_executable',return_value='node'):
            prepared=self.service.call('prepare_task',dict(tool_id=tool['id'],action=action,values=p))
            for command,cwd in plan(self.service.paths,tool['id'],action,dict(prepared['values'])):
                self.assertTrue(Path(cwd).is_dir(),cwd)
                for argument in command:
                    if str(argument).endswith(('.py','.mjs')):
                        self.assertTrue(Path(argument).is_file(),argument)
            requests=[];request_paths=[]
            class RecordingRunner:
                def run(_self, command, **kwargs):
                    path=Path(command[command.index('--request')+1]);request_paths.append(path)
                    request=json.loads(path.read_text());requests.append(request)
                    kwargs['on_output'](RESULT_PREFIX+json.dumps({'message':'routing verified','outputs':[]}))
            with patch('toolbox.service.ProcessRunner',RecordingRunner):
                task_id=self.service.call('start',dict(tool_id=tool['id'],action=action,values=prepared['values'],request_id='matrix',confirmed=True))
                deadline=time.monotonic()+5
                while self.service.registry.has_running_tasks() and time.monotonic()<deadline:time.sleep(.01)
                snapshot=self.service.registry.snapshot(task_id)
                self.assertEqual(snapshot.state.value,'succeeded',snapshot.error)
            self.assertEqual(len(requests),1)
            self.assertEqual(set(prepared['values']),{field['key'] for field in tool['fields']})
            expected=validate(self.service.paths,tool['id'],action,prepared['values'])
            self.assertEqual(requests[0],dict(tool_id=tool['id'],action=action,values=expected))
            self.assertFalse(request_paths[0].exists())
    def test_login_does_not_check_or_report_business_outputs(self):
        for tool in CATALOG:
            if not any(action['id']=='login' for action in tool['actions']) or tool['id']=='wps-writer':continue
            with self.subTest(tool=tool['id']),patch('toolbox.workflows.subprocess.run'):
                result=execute(self.service.paths,tool['id'],'login',self.values(tool))
                self.assertEqual(result['outputs'],[])
                self.assertIn('登录流程',result['message'])
    def test_workbook_results_are_returned_for_price_and_benefit_updates(self):
        for tool in CATALOG:
            if tool['id'] not in {'spot-price','power-benefit'}:continue
            with self.subTest(tool=tool['id']),patch('toolbox.workflows.subprocess.run'):
                p=validate(self.service.paths,tool['id'],'run',self.values(tool))
                result=execute(self.service.paths,tool['id'],'run',p)
                self.assertEqual(result['outputs'],[p['guihu_workbook']])
    def test_prediction_resolves_default_output_before_launching(self):
        tool=next(tool for tool in CATALOG if tool['id']=='guangdong-price')
        p=self.values(tool);p['prediction_output_dir']=''
        with patch('toolbox.workflows.subprocess.run'):
            p=validate(self.service.paths,tool['id'],'predict',p)
            self.assertEqual(Path(p['prediction_output_dir']),self.service.paths.guangdong_prediction_output_dir)
            self.assertIn(p['prediction_output_dir'],execute(self.service.paths,tool['id'],'predict',p)['outputs'])
    def test_uploads_keep_confirmation_before_starting_a_worker(self):
        for tool in CATALOG:
            if tool['id'] not in {'group-upload','private-batch-upload','summary-upload'}:continue
            with self.subTest(tool=tool['id']),patch('toolbox.service.ProcessRunner') as runner,patch('toolbox.workflows.node_executable',return_value='node'):
                with self.assertRaisesRegex(ValueError,'确认'):
                    self.service.call('start',dict(tool_id=tool['id'],action='run',values=self.values(tool),request_id='unconfirmed'))
                runner.assert_not_called()

    def test_uploader_preview_starts_without_optional_config_but_rejects_bad_custom_paths(self):
        from toolbox.runtime import node_executable
        empty=self.root/'empty-review';empty.mkdir()
        command=[node_executable(),str(ROOT/'电力工具脚本/private-data-uploader-tool/scripts/upload-private-data.mjs'),'--plan','--source',str(empty)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('当前为预览模式',result.stdout)
        self.assertFalse((self.root/'data/电力工具脚本/private-data-uploader-tool/upload.config.json').exists())
        result=subprocess.run([*command,'--config',str(self.root/'missing.json')],capture_output=True,text=True,timeout=15)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('ENOENT',result.stderr)
        broken=self.root/'broken.json';broken.write_text('{')
        result=subprocess.run([*command,'--config',str(broken)],capture_output=True,text=True,timeout=15)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('SyntaxError',result.stderr)

def route_test(tool, action):
    def test(self):self.verify_route(tool,action)
    return test
for tool in CATALOG:
    for action in tool['actions']:
        setattr(BusinessRoutesTests,'test_route_'+tool['id'].replace('-','_')+'_'+action['id'],route_test(tool,action['id']))

if __name__=='__main__':unittest.main()
