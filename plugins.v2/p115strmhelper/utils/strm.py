__all__ = [
    "StrmUrlTemplateResolver",
    "StrmFilenameTemplateResolver",
    "StrmUrlGetter",
    "StrmGenerater",
    "get_source_mtime",
    "write_strm_file",
]


import os
import stat
import tempfile
from pathlib import Path
from time import time
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote

from app.log import logger

from ahocorasick import Automaton
from p115pickcode import to_id
from jinja2 import Template, Environment, select_autoescape
from jinja2.exceptions import TemplateError

from ..core.config import configer
from ..schemas.size import CompareMinSize


def get_source_mtime(source: Any) -> Optional[float]:
    """
    获取源文件的稳定时间戳。

    115 文件优先使用创建时间（ctime），这样新入库的文件在媒体服务器
    中仍然保持“最后加入、最后显示”的语义；没有创建时间时回退到修改时间。
    MoviePilot 的 FileItem 只有 ``modify_time``，也能通过同一入口处理。
    """
    keys = ("ctime", "create_time", "mtime", "modify_time")
    for key in keys:
        if isinstance(source, dict):
            value = source.get(key)
        else:
            value = getattr(source, key, None)
        if value in (None, "", 0, "0"):
            continue
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        # 部分接口返回毫秒时间戳，统一为 POSIX 秒。
        if timestamp > 100_000_000_000:
            timestamp /= 1000
        if timestamp > 0:
            return timestamp
    return None


def write_strm_file(
    file_path: Path,
    strm_url: str,
    source_mtime: Optional[float] = None,
) -> bool:
    """
    幂等、原子地写入 STRM 文件。

    返回值表示内容或文件时间是否实际变化。内容未变化且文件时间已经
    与源文件一致时完全不触碰文件；历史 STRM 若仍保留随机生成时间，
    则在首次升级时校正为源文件时间，修复旧媒体库的乱序。
    """
    path = Path(file_path)
    content = str(strm_url)
    old_stat = None
    source_timestamp = (
        get_source_mtime({"mtime": source_mtime})
        if source_mtime is not None
        else None
    )

    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == content:
                if source_timestamp is None:
                    return False
                current_stat = path.stat()
                if abs(current_stat.st_mtime - source_timestamp) <= 1:
                    return False
                try:
                    os.utime(
                        path,
                        ns=(
                            current_stat.st_atime_ns,
                            int(source_timestamp * 1_000_000_000),
                        ),
                    )
                    return True
                except OSError as exc:
                    logger.debug(
                        "【STRM生成】校正已有文件时间失败，保留文件系统时间: %s",
                        exc,
                    )
                    return False
            old_stat = path.stat()
        except OSError:
            # 文件可能正在被清理或替换，继续走原子写入流程。
            old_stat = None

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(content)

        # mkstemp 默认是 0600；尽量沿用旧文件或目录的普通文件权限，
        # 避免升级为原子写入后 Plex 所在用户无法读取新建 STRM。
        mode = (
            stat.S_IMODE(old_stat.st_mode)
            if old_stat is not None
            else stat.S_IMODE(path.parent.stat().st_mode) & 0o666
        ) or 0o644
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        temporary_path = None

        try:
            if source_timestamp is not None:
                atime_ns = (
                    old_stat.st_atime_ns
                    if old_stat is not None
                    else int(time() * 1_000_000_000)
                )
                os.utime(
                    path,
                    ns=(atime_ns, int(source_timestamp * 1_000_000_000)),
                )
            elif old_stat is not None:
                os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        except OSError as exc:
            # 时间戳只是排序提示，不能因为挂载盘不支持 utime 而判定 STRM 生成失败。
            logger.debug("【STRM生成】同步文件时间失败，保留文件系统时间: %s", exc)
        return True
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


class StrmUrlTemplateResolver:
    """
    基于 Jinja2 的 STRM URL 模板解析器
    """

    def __init__(
        self,
        base_template: Optional[str] = None,
        custom_rules: Optional[str] = None,
        auto_escape: bool = False,
    ):
        """
        初始化模板解析器

        :param base_template (str): 基础 Jinja2 模板字符串
        :param custom_rules (str): 扩展名特定模板规则，格式：ext1,ext2 => template
        :param auto_escape (bool): 是否自动转义
        """
        self.env = Environment(
            autoescape=select_autoescape(["html", "xml"]) if auto_escape else False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self._register_filters()

        self.base_template = None
        if base_template:
            try:
                self.base_template = self.env.from_string(base_template)
            except TemplateError as e:
                logger.error(f"【STRM URL 模板】基础模板解析失败: {e}")
                raise

        self.extension_templates: Dict[str, Template] = {}
        if custom_rules:
            self._parse_custom_rules(custom_rules)

    def _register_filters(self):
        """
        注册自定义过滤器
        """

        def urlencode_filter(value: str) -> str:
            """
            URL 编码过滤器
            """
            if not value:
                return ""
            return quote(str(value), safe="")

        def path_encode_filter(value: str) -> str:
            """
            路径编码过滤器（保留斜杠）
            """
            if not value:
                return ""
            return quote(str(value), safe="/")

        def upper_filter(value: str) -> str:
            """
            转大写
            """
            return str(value).upper() if value else ""

        def lower_filter(value: str) -> str:
            """
            转小写
            """
            return str(value).lower() if value else ""

        self.env.filters["urlencode"] = urlencode_filter
        self.env.filters["path_encode"] = path_encode_filter
        self.env.filters["upper"] = upper_filter
        self.env.filters["lower"] = lower_filter

    def _parse_custom_rules(self, config_str: str):
        """
        解析扩展名特定模板规则

        :param config_str (str): 规则字符串，格式：ext1,ext2 => template（每行一个）
        """
        for rule in config_str.strip().split("\n"):
            rule = rule.strip()
            if not rule or "=>" not in rule:
                continue

            try:
                extensions_part, template_str = rule.split("=>", 1)
                extensions = [ext.strip().lower() for ext in extensions_part.split(",")]
                template_str = template_str.strip()

                if not template_str:
                    logger.warning(f"【STRM URL 模板】规则模板为空，跳过: {rule}")
                    continue

                # 解析模板
                try:
                    template = self.env.from_string(template_str)
                except TemplateError as e:
                    logger.error(
                        f"【STRM URL 模板】扩展名模板解析失败: {rule}, 错误: {e}"
                    )
                    continue

                # 为每个扩展名注册模板
                for ext in extensions:
                    if not ext:
                        continue
                    if not ext.startswith("."):
                        ext = "." + ext
                    self.extension_templates[ext] = template
                    logger.debug(
                        f"【STRM URL 模板】注册扩展名模板: {ext} => {template_str[:50]}..."
                    )

            except Exception as e:
                logger.error(f"【STRM URL 模板】解析规则失败: {rule}, 错误: {e}")
                continue

    def get_template_for_file(self, file_name: str) -> Optional[Template]:
        """
        根据文件名获取对应的模板

        :param file_name (str): 文件名

        :return Template: 匹配的模板，如果没有匹配则返回基础模板，如果都没有则返回 None
        """
        extension = Path(file_name).suffix.lower()

        if extension in self.extension_templates:
            return self.extension_templates[extension]

        return self.base_template

    def render(
        self,
        file_name: str,
        base_url: str,
        pickcode: Optional[str] = None,
        share_code: Optional[str] = None,
        receive_code: Optional[str] = None,
        file_id: Optional[str] = None,
        file_path: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[str]:
        """
        渲染 URL 模板

        :param file_name (str): 文件名
        :param base_url (str): 基础 URL
        :param pickcode (str): 文件 pickcode
        :param share_code (str): 分享码
        :param receive_code (str): 提取码
        :param file_id (str): 文件 ID
        :param file_path (str): 文件网盘路径

        :return str: 渲染后的 URL 字符串，如果没有可用模板则返回 None
        """
        template = self.get_template_for_file(file_name)

        if not template:
            return None

        context = {
            "base_url": base_url.rstrip("/"),
            "pickcode": pickcode or "",
            "share_code": share_code or "",
            "receive_code": receive_code or "",
            "file_id": file_id or "",
            "file_name": file_name or "",
            "file_path": file_path or "",
            **kwargs,
        }

        try:
            return template.render(**context)
        except TemplateError as e:
            logger.error(f"【STRM URL 模板】模板渲染失败: {e}, 上下文: {context}")
            raise
        except Exception as e:
            logger.error(f"【STRM URL 模板】渲染时发生未知错误: {e}")
            raise


class StrmFilenameTemplateResolver:
    """
    基于 Jinja2 的 STRM 文件名模板解析器
    """

    def __init__(
        self,
        base_template: Optional[str] = None,
        custom_rules: Optional[str] = None,
    ):
        """
        初始化模板解析器

        :param base_template (str): 基础 Jinja2 模板字符串
        :param custom_rules (str): 扩展名特定模板规则，格式：ext1,ext2 => template
        """
        self.env = Environment(
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self._register_filters()

        self.base_template = None
        if base_template:
            try:
                self.base_template = self.env.from_string(base_template)
            except TemplateError as e:
                logger.error(f"【STRM 文件名模板】基础模板解析失败: {e}")
                raise

        self.extension_templates: Dict[str, Template] = {}
        if custom_rules:
            self._parse_custom_rules(custom_rules)

    def _register_filters(self):
        """
        注册自定义过滤器
        """

        def upper_filter(value: str) -> str:
            """
            转大写
            """
            return str(value).upper() if value else ""

        def lower_filter(value: str) -> str:
            """
            转小写
            """
            return str(value).lower() if value else ""

        def sanitize_filter(value: str) -> str:
            """
            文件名清理过滤器（移除或替换不合法字符）
            """
            if not value:
                return ""
            result = str(value).replace(":", "：")
            illegal_chars = '<>"/\\|?*'
            for char in illegal_chars:
                result = result.replace(char, "_")
            return result

        self.env.filters["upper"] = upper_filter
        self.env.filters["lower"] = lower_filter
        self.env.filters["sanitize"] = sanitize_filter

    def _parse_custom_rules(self, config_str: str):
        """
        解析扩展名特定模板规则

        :param config_str (str): 规则字符串，格式：ext1,ext2 => template（每行一个）
        """
        for rule in config_str.strip().split("\n"):
            rule = rule.strip()
            if not rule or "=>" not in rule:
                continue

            try:
                extensions_part, template_str = rule.split("=>", 1)
                extensions = [ext.strip().lower() for ext in extensions_part.split(",")]
                template_str = template_str.strip()

                if not template_str:
                    logger.warning(f"【STRM 文件名模板】规则模板为空，跳过: {rule}")
                    continue

                try:
                    template = self.env.from_string(template_str)
                except TemplateError as e:
                    logger.error(
                        f"【STRM 文件名模板】扩展名模板解析失败: {rule}, 错误: {e}"
                    )
                    continue

                for ext in extensions:
                    if not ext:
                        continue
                    if not ext.startswith("."):
                        ext = "." + ext
                    self.extension_templates[ext] = template
                    logger.debug(
                        f"【STRM 文件名模板】注册扩展名模板: {ext} => {template_str[:50]}..."
                    )

            except Exception as e:
                logger.error(f"【STRM 文件名模板】解析规则失败: {rule}, 错误: {e}")
                continue

    def get_template_for_file(self, file_name: str) -> Optional[Template]:
        """
        根据文件名获取对应的模板

        :param file_name (str): 文件名

        :return Template: 匹配的模板，如果没有匹配则返回基础模板，如果都没有则返回 None
        """
        extension = Path(file_name).suffix.lower()

        if extension in self.extension_templates:
            return self.extension_templates[extension]

        return self.base_template

    def render(
        self,
        file_name: str,
        file_path: Optional[str] = None,
        file_stem: Optional[str] = None,
        file_suffix: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[str]:
        """
        渲染文件名模板

        :param file_name (str): 文件名（包含扩展名）
        :param file_path (str): 文件路径
        :param file_stem (str): 文件名（不含扩展名）
        :param file_suffix (str): 文件扩展名（包含点号）

        :return str: 渲染后的文件名字符串，如果没有可用模板则返回 None
        """
        template = self.get_template_for_file(file_name)

        if not template:
            return None

        if file_stem is None:
            file_stem = Path(file_name).stem
        if file_suffix is None:
            file_suffix = Path(file_name).suffix

        context = {
            "file_name": file_name or "",
            "file_stem": file_stem or "",
            "file_suffix": file_suffix or "",
            "file_path": file_path or "",
            **kwargs,
        }

        try:
            result = template.render(**context)
            result = result.replace(":", "：")
            illegal_chars = '<>"/\\|?*'
            for char in illegal_chars:
                result = result.replace(char, "_")
            return result
        except TemplateError as e:
            logger.error(f"【STRM 文件名模板】模板渲染失败: {e}, 上下文: {context}")
            raise
        except Exception as e:
            logger.error(f"【STRM 文件名模板】渲染时发生未知错误: {e}")
            raise


class StrmUrlGetter:
    """
    获取 Strm URL
    """

    def __init__(self):
        """
        初始化 STRM URL 获取器，加载 URL 模板解析器
        """
        self.strm_url_encode = configer.strm_url_encode
        self.base_url_cache = (
            f"{configer.moviepilot_address.rstrip('/')}/api/v1/plugin/P115StrmHelper/redirect_url"
        )

        self.url_template_resolver = None
        if configer.strm_url_template_enabled:
            try:
                self.url_template_resolver = StrmUrlTemplateResolver(
                    base_template=configer.strm_url_template,
                    custom_rules=configer.strm_url_template_custom,
                )
            except Exception as e:
                logger.error(f"【STRM URL 模板】初始化失败: {e}")
                self.url_template_resolver = None

    def get_strm_url(self, pickcode: str, file_name: str, file_path: str) -> str:
        """
        获取普通 STRM URL

        :param pickcode (str): 文件 pickcode
        :param file_name (str): 文件名称
        :param file_path (str): 文件网盘路径

        :return str: STRM URL 字符串
        """
        if self.url_template_resolver:
            try:
                result = self.url_template_resolver.render(
                    file_name=file_name,
                    base_url=self.base_url_cache,
                    pickcode=pickcode,
                    file_path=file_path,
                    file_id=str(to_id(pickcode)),
                )
                if result:
                    return result
            except Exception as e:
                logger.error(f"【STRM URL 模板】渲染失败，使用默认格式: {e}")

        if configer.fuse_strm_takeover_enabled:
            try:
                from ..helper.strm.mount import match_fuse_strm_takeover

                fuse_strm_content = match_fuse_strm_takeover(
                    file_name=file_name, file_path=file_path
                )
                if fuse_strm_content:
                    return fuse_strm_content
            except Exception as e:
                logger.error(f"【FUSE STRM 接管】处理失败: {e}", exc_info=True)

        params = [("pickcode", pickcode)]
        if configer.strm_url_format == "pickname":
            params.append(("file_name", file_name))
        return self._build_url(params)

    def get_share_strm_url(
        self,
        share_code: str,
        receive_code: str,
        file_id: str,
        file_name: str,
        file_path: str,
    ) -> str:
        """
        获取分享 STRM URL

        :param share_code (str): 分享码
        :param receive_code (str): 提取码
        :param file_id (str): 文件 ID
        :param file_name (str): 文件名称
        :param file_path (str): 文件网盘路径

        :return str: 分享 STRM URL 字符串
        """
        if self.url_template_resolver:
            try:
                result = self.url_template_resolver.render(
                    file_name=file_name,
                    base_url=self.base_url_cache,
                    share_code=share_code,
                    receive_code=receive_code,
                    file_id=file_id,
                    file_path=file_path,
                )
                if result:
                    return result
            except Exception as e:
                logger.error(f"【STRM URL 模板】渲染失败，使用默认格式: {e}")

        params = [
            ("share_code", share_code),
            ("receive_code", receive_code),
            ("id", file_id),
        ]
        if configer.strm_url_format == "pickname":
            params.append(("file_name", file_name))
        return self._build_url(params)

    def _build_url(self, params: List[Tuple[str, Any]]) -> str:
        query = []
        for key, value in params:
            if key == "file_name" and self.strm_url_encode:
                value = quote(str(value))
            query.append(f"{key}={value}")
        return f"{self.base_url_cache}?{'&'.join(query)}"


class StrmGenerater:
    """
    STRM 文件生成工具类
    """

    _filename_template_resolver: Optional[Union[StrmFilenameTemplateResolver, bool]] = (
        None
    )

    @staticmethod
    def _get_filename_template_resolver() -> Optional[StrmFilenameTemplateResolver]:
        """
        获取文件名模板解析器
        """
        resolver = StrmGenerater._filename_template_resolver

        if isinstance(resolver, StrmFilenameTemplateResolver):
            return resolver

        if resolver is False:
            return None

        if not configer.strm_filename_template_enabled:
            StrmGenerater._filename_template_resolver = False
            return None

        try:
            resolver = StrmFilenameTemplateResolver(
                base_template=configer.strm_filename_template,
                custom_rules=configer.strm_filename_template_custom,
            )
            StrmGenerater._filename_template_resolver = resolver
            return resolver
        except Exception as e:
            logger.error(f"【STRM 文件名模板】初始化失败: {e}")
            StrmGenerater._filename_template_resolver = False
            return None

    @staticmethod
    def _reset_filename_template_resolver():
        """
        重置文件名模板解析器
        """
        StrmGenerater._filename_template_resolver = None

    @staticmethod
    def should_generate_strm(
        filename: str,
        mode: str,
        filesize: Optional[int] | CompareMinSize = None,
        blacklist_automaton: Optional[Automaton] = None,
    ) -> tuple[str, bool]:
        """
        判断文件是否能生成总规则

        :param filename (str): 文件名
        :param mode (str): 同步模式
        :param filesize (int): 文件大小，可为 CompareMinSize
        :param blacklist_automaton (Automaton): 黑名单自动机

        :return Tuple: (拒绝原因, 是否允许)
        """
        # 1. 判断是否在黑名单
        if blacklist_automaton:
            blacklist_msg, blacklist_status = StrmGenerater.not_blacklist_key_automaton(
                filename, blacklist_automaton
            )
        else:
            blacklist_msg, blacklist_status = StrmGenerater.not_blacklist_key(filename)
        if not blacklist_status:
            return blacklist_msg, blacklist_status

        # 2. 判断大小是否低于最低限制
        minsize_msg, minsize_status = StrmGenerater.not_min_limit(mode, filesize)
        if not minsize_status:
            return minsize_msg, minsize_status

        return "", True

    @staticmethod
    def not_blacklist_key_automaton(
        filename, blacklist_automaton: Automaton
    ) -> tuple[str, bool]:
        """
        使用 Aho-Corasick 自动机判断文件名是否包含黑名单中的任何关键词

        :param filename (str): 文件名
        :param blacklist_automaton (Automaton): 黑名单自动机

        :return Tuple: (匹配到的关键词, 是否通过)
        """
        if not blacklist_automaton:
            return "", True
        lower_filename = filename.lower()
        try:
            _, (original_keyword, _) = next(blacklist_automaton.iter(lower_filename))
            return f"匹配到黑名单关键词 {original_keyword}", False
        except StopIteration:
            return "", True

    @staticmethod
    def not_blacklist_key(filename) -> tuple[str, bool]:
        """
        判断文件名是否包含黑名单中的任何关键词

        :param filename (str): 文件名

        :return Tuple: (匹配到的关键词, 是否通过)
        """
        blacklist = configer.strm_generate_blacklist

        if not blacklist:
            return "", True
        lower_filename = filename.lower()
        for keyword in blacklist:  # pylint: disable=E1133
            if keyword.lower() in lower_filename:
                return f"匹配到黑名单关键词 {keyword}", False
        return "", True

    @staticmethod
    def not_min_limit(
        mode: str, filesize: Optional[int] | CompareMinSize = None
    ) -> tuple[str, bool]:
        """
        判断文件大小是否低于最低限制

        :param mode (str): 同步模式（full / life / increment）
        :param filesize (int): 文件大小，可为 CompareMinSize

        :return Tuple: (拒绝原因, 是否通过)
        """
        min_size = None
        if isinstance(filesize, CompareMinSize):
            min_size = filesize.min_size
            filesize = filesize.file_size
        if mode == "full":
            min_size = configer.full_sync_min_file_size
        elif mode == "life":
            min_size = configer.monitor_life_min_file_size
        elif mode == "increment":
            min_size = configer.increment_sync_min_file_size

        if not min_size or min_size == 0:
            return "", True

        if not filesize:
            return "", True

        if filesize < min_size:
            return "小于最小文件大小", False

        return "", True

    @staticmethod
    def get_strm_filename(
        file_path: Path,
        file_name: Optional[str] = None,
        file_path_str: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """
        根据原始文件路径生成 STRM 文件名

        :param file_path (Path): 原始文件路径（Path 对象）
        :param file_name (str): 文件名（可选，如果不提供则从file_path提取）
        :param file_path_str (str): 文件路径字符串（可选，用于模板渲染）
        :param kwargs (Dict): 其他上下文信息（用于模板渲染）

        :return str: STRM 文件名（如 "movie.iso.strm" 或 "movie.strm"）
        """
        if StrmGenerater._filename_template_resolver is False:
            suffix = file_path.suffix.lower()
            stem = file_path.stem
            if suffix == ".iso":
                return f"{stem}.iso.strm"
            return f"{stem}.strm"

        template_resolver = StrmGenerater._get_filename_template_resolver()
        if template_resolver:
            try:
                suffix = file_path.suffix
                stem = file_path.stem

                if file_name is None:
                    file_name = file_path.name

                result = template_resolver.render(
                    file_name=file_name,
                    file_path=file_path_str or str(file_path),
                    file_stem=stem,
                    file_suffix=suffix,
                    **kwargs,
                )
                if result:
                    return result
            except Exception as e:
                logger.error(f"【STRM 文件名模板】渲染失败，使用默认格式: {e}")

        suffix = file_path.suffix.lower()
        stem = file_path.stem
        if suffix == ".iso":
            return f"{stem}.iso.strm"
        return f"{stem}.strm"
