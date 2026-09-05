"""
HDHive 浏览器资源响应测试模块
"""

from unittest import TestCase

from helper.hdhive.browser import HDHivePlaywrightClient
from helper.hdhive.open.constants import HDHIVE_OPEN_BASE_URL
from utils.hdhive import extract_hdhive_resource_rows


class TestExtractHDHiveResourceRows(TestCase):
    """测试 HDHive 资源响应筛选"""

    def test_uses_current_site_domain(self):
        """测试浏览器与 Open API 使用当前站点域名"""
        self.assertEqual(HDHivePlaywrightClient.DEFAULT_BASE_URL, "https://re0.me")
        self.assertEqual(HDHIVE_OPEN_BASE_URL, "https://re0.me/api/open")

    def test_ignores_unrelated_rows_with_source_field(self):
        """测试忽略含 source 字段的非资源列表"""
        body = {
            "data": [
                {
                    "name": "unrelated entry",
                    "source": "account",
                }
            ]
        }

        self.assertEqual(extract_hdhive_resource_rows(body), [])

    def test_keeps_rows_with_resource_href(self):
        """测试保留带资源详情链接的条目"""
        resource = {
            "title": "resource entry",
            "href": "/resource/115/example-slug",
        }
        body = {"data": [resource]}

        self.assertEqual(extract_hdhive_resource_rows(body), [resource])

    def test_filters_mixed_response_rows(self):
        """测试混合响应中仅保留资源条目"""
        resource = {
            "title": "resource entry",
            "href": "https://re0.me/resource/115/example-slug",
        }
        body = {
            "data": [
                {"source": "account"},
                resource,
                "invalid row",
            ]
        }

        self.assertEqual(extract_hdhive_resource_rows(body), [resource])
