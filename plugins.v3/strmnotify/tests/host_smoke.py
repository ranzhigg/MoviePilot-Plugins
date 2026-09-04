"""在隔离 MoviePilot 环境执行的模拟通知验证，不向外部发送消息"""
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.testing.bootstrap import prepare_backend
prepare_backend()
spec=importlib.util.spec_from_file_location('strmnotify',str(Path(__file__).resolve().parents[1] / '__init__.py'),submodule_search_locations=[str(Path(__file__).resolve().parents[1])])
import sys
module=importlib.util.module_from_spec(spec);sys.modules['strmnotify']=module;spec.loader.exec_module(module)
with patch.object(module._PluginBase, "__init__", return_value=None):
 plugin=module.StrmNotify()
storage={}
plugin.get_data=lambda key:json.loads(json.dumps(storage.get(key)))
plugin.save_data=lambda key,value:storage.update({key:json.loads(json.dumps(value))})
plugin.post_message=Mock()
with TemporaryDirectory() as directory:
 root=Path(directory)
 (root/'old.strm').touch()
 plugin.init_plugin({'enabled':True,'paths':str(root),'batch':1,'wait':120})
 assert plugin.get_state() and len(plugin.get_service())==1
 plugin.get_form()
 plugin.poll();assert plugin.post_message.call_count==0
 for name in ['one','two']:
  (root/f'{name}.strm').touch()
  p=root/f'{name}.nfo';p.write_text(f'<movie><title>{name}</title></movie>');os.utime(p,(0,0))
 plugin.poll();assert plugin.post_message.call_count==1
 plugin.poll();assert plugin.post_message.call_count==2
 plugin.init_plugin({'enabled':True,'paths':str(root)})
 plugin.poll();assert plugin.post_message.call_count==2
 plugin.stop_service();(root/'three.strm').touch();plugin.poll();assert plugin.post_message.call_count==2
print('PASS: real MP plugin load/form/service; baseline; batch; persisted dedup; disable; mocked delivery only')
