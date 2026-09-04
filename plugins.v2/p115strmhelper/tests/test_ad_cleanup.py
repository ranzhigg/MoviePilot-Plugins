"""广告附件清理的边界和分页回归测试"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('ad_cleanup', Path(__file__).parents[1] / 'helper/ad_cleanup.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CleanupTests(unittest.TestCase):
    def test_matching(self):
        for name in ['聚 合 全 網 H 直 播.html', '最 新 位 址 獲 取 收藏不迷路.txt', '廣告.URL', '直\u200b播.htm']:
            self.assertTrue(m.ad_reason(name, m.DEFAULT_KEYWORDS))
        for name in ['直播.mp4', '广告.srt', '电影.nfo', '说明.txt', '489155.com@JUR-782-C.mp4']:
            self.assertIsNone(m.ad_reason(name, m.DEFAULT_KEYWORDS))
        self.assertIsNone(m.ad_reason('普通.txt', [' ', '']))
        self.assertTrue(m.ad_reason('自 定 义.txt', ['自定义']))

    def test_roots(self):
        for root in ['', '/', 'relative', '/a/../b']:
            with self.assertRaises(ValueError):
                m.validate_root(root)
        self.assertEqual(m.validate_root('/a/b/'), '/a/b')

    def run_scan(self, delete, changed=False):
        job = m.AdCleanup()
        job.call = lambda fn, *args, **kw: fn(*args, **kw)
        client = Mock()
        client.fs_dir_getid.return_value = {'id': 1}
        rows = {i: [{'cid': i + 1, 'n': str(i + 1)}] for i in range(1, 9)}
        rows[9] = [{'fid': i, 'n': f'直播{i}.txt', 's': 1} for i in range(10, 15)] + [{'fid': 99, 'n': '直播.mp4', 's': 99}]
        def listing(payload, **kwargs):
            cid, offset = payload['cid'], payload['offset']
            return {'path': [{'cid': i} for i in range(1, cid + 1)], 'count': len(rows[cid]), 'data': [dict(x) for x in rows[cid][offset:offset + 2]]}
        client.fs_files.side_effect = listing
        def remove(fid, **kwargs):
            rows[9] = [x for x in rows[9] if x.get('fid') != fid]
            if changed:
                rows[9][0]['s'] = 200
            return {'state': True}
        client.fs_delete.side_effect = remove
        with patch.dict(sys.modules, {'app.log': types.SimpleNamespace(logger=Mock())}):
            job.run(client, '/test', m.DEFAULT_KEYWORDS, delete, {})
        return job.status(), client

    def test_deep_paginated_delete(self):
        state, client = self.run_scan(True)
        self.assertEqual(state['directories'], 9)
        self.assertEqual(state['deleted'], 5)
        self.assertEqual(state['failed'], 0)
        self.assertNotIn(99, [c.args[0] for c in client.fs_delete.call_args_list])

    def test_preview(self):
        state, client = self.run_scan(False)
        self.assertEqual(state['matched'], 5)
        client.fs_delete.assert_not_called()

    def test_changed_file_stops(self):
        state, client = self.run_scan(True, True)
        self.assertEqual(state['deleted'], 1)
        self.assertEqual(state['failed'], 1)

    def test_boundary(self):
        job = m.AdCleanup()
        job.call = lambda *args, **kw: {'path': [{'cid': 77}], 'data': [], 'count': 0}
        with self.assertRaises(ValueError):
            job.listing(Mock(), 77, 1, {})

    def test_cancel_before_request(self):
        job = m.AdCleanup()
        job.stop()
        request = Mock()
        with patch.dict(sys.modules, {'p115client': types.SimpleNamespace(check_response=lambda x: x)}):
            with self.assertRaises(InterruptedError):
                job.call(request)
        request.assert_not_called()

if __name__ == '__main__':
    unittest.main()
