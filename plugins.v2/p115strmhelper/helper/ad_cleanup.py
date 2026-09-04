"""插件内的广告附件清理，不依赖外部脚本或额外安装包"""
from pathlib import PurePosixPath
from threading import Event, Lock, Thread
from time import monotonic
from unicodedata import normalize, category

DEFAULT_KEYWORDS = ["直播", "收藏不迷路", "最新位址", "最新地址", "聚合全网", "社区最新", "广告"]


def normalized(value: str) -> str:
    """统一全角、空白和常见繁简字"""
    value = normalize("NFKC", value).casefold()
    return "".join(c for c in value if not c.isspace() and category(c) != "Cf").translate(
        str.maketrans({"網": "网", "區": "区", "獲": "获", "廣": "广"}))


def ad_reason(name: str, keywords: list) -> str | None:
    """仅允许广告文本附件，视频和字幕不参与删除"""
    if PurePosixPath(name).suffix.lower() not in {".txt", ".html", ".htm", ".url"}:
        return None
    return next((str(k) for k in keywords if normalized(str(k)) and normalized(str(k)) in normalized(name)), None)


def validate_root(value: str) -> str:
    """清理范围必须是非根绝对目录"""
    path = PurePosixPath(value)
    if not value or not path.is_absolute() or str(path) == "/" or ".." in path.parts:
        raise ValueError("请设置非根目录的绝对路径，不能包含 ..")
    return str(path)


class AdCleanup:
    """单任务、可取消的全目录遍历，单次请求间隔至少三秒"""

    def __init__(self):
        self.lock = Lock()
        self.cancel = Event()
        self.thread = None
        self.state = {"running": False, "message": "尚未运行"}
        self.last_request = 0.0

    def status(self) -> dict:
        """返回当前任务摘要"""
        with self.lock:
            return dict(self.state)

    def update(self, **values) -> None:
        with self.lock:
            self.state.update(values)

    def stop(self) -> None:
        """停止后不再发送新的请求"""
        self.cancel.set()

    def start(self, client, root: str, keywords: list, delete: bool, kwargs: dict) -> None:
        root = validate_root(root)
        keywords = [str(k) for k in keywords if normalized(str(k))]
        if not keywords:
            raise ValueError("广告附件关键词不能为空")
        with self.lock:
            if self.state.get("running"):
                raise ValueError("已有清理任务运行中")
            self.cancel.clear()
            self.state = dict(running=True, root=root, delete=delete, directories=0,
                              files=0, matched=0, deleted=0, failed=0, message="开始扫描")
            self.thread = Thread(target=self.run, args=(client, root, keywords, delete, kwargs),
                                 daemon=True, name="p115-ad-cleanup")
            self.thread.start()

    def call(self, method, *args, **kwargs):
        """失败立即结束本轮，避免风控后反复重试"""
        from p115client import check_response
        if self.cancel.wait(max(0, 3 - (monotonic() - self.last_request))):
            raise InterruptedError("已停止")
        try:
            return check_response(method(*args, **kwargs))
        finally:
            self.last_request = monotonic()

    def listing(self, client, cid: int, root_id: int, kwargs: dict) -> list:
        """完整读取分页后才允许删除，避免删除导致分页偏移"""
        items, offset = [], 0
        while True:
            resp = self.call(client.fs_files, {"cid": cid, "offset": offset, "limit": 1000,
                             "show_dir": 1, "cur": 1}, **kwargs)
            ancestry = [int(p["cid"]) for p in resp.get("path", [])]
            if not ancestry or ancestry[-1] != cid or root_id not in ancestry:
                raise ValueError("目录范围发生变化，已停止")
            page = resp.get("data")
            if not isinstance(page, list):
                raise ValueError("目录响应无效")
            items.extend(page)
            offset += len(page)
            if offset >= int(resp["count"]):
                return items
            if not page:
                raise ValueError("目录分页不完整")

    def run(self, client, root: str, keywords: list, delete: bool, kwargs: dict) -> None:
        from app.log import logger
        counts = dict(directories=0, files=0, matched=0, deleted=0, failed=0)
        try:
            root_id = int(self.call(client.fs_dir_getid, root, **kwargs).get("id", 0))
            if root_id <= 0:
                raise ValueError("目录不存在")
            queue, visited = [(root_id, root)], set()
            while queue:
                cid, path = queue.pop()
                if cid in visited:
                    continue
                visited.add(cid)
                items = self.listing(client, cid, root_id, kwargs)
                counts["directories"] += 1
                for item in items:
                    if self.cancel.is_set():
                        raise InterruptedError("已停止")
                    name = str(item.get("n", ""))
                    if not name or name in {".", ".."} or "/" in name:
                        raise ValueError("文件名无效，已停止")
                    if "fid" not in item:
                        queue.append((int(item["cid"]), path + "/" + name))
                        continue
                    counts["files"] += 1
                    reason = ad_reason(name, keywords)
                    if not reason:
                        continue
                    counts["matched"] += 1
                    logger.info(f"【广告附件清理】命中 {path}/{name}，关键词：{reason}")
                    if delete:
                        # 删除前重新核对父目录边界、文件类型、名称和大小
                        fresh = self.listing(client, cid, root_id, kwargs)
                        current = next((x for x in fresh if str(x.get("fid")) == str(item["fid"])), None)
                        if not current or current.get("n") != name or current.get("s") != item.get("s"):
                            raise ValueError("删除前文件信息发生变化，已停止")
                        self.call(client.fs_delete, int(item["fid"]), **kwargs)
                        counts["deleted"] += 1
                        logger.info(f"【广告附件清理】已删除 {path}/{name}")
                    self.update(**counts)
                self.update(**counts)
            self.update(message="清理完成" if delete else "预览完成")
        except InterruptedError:
            self.update(message="已停止")
        except Exception as exc:
            counts["failed"] += 1
            self.update(message=f"任务失败：{type(exc).__name__}，请检查目录、登录状态或风控冷却后重试")
            logger.warning(f"【广告附件清理】任务中止：{type(exc).__name__}")
        finally:
            self.update(running=False, **counts)
            logger.info(f"【广告附件清理】结束：{counts}")


ad_cleanup = AdCleanup()
