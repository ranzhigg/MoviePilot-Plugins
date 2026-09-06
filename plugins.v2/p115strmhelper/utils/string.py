__all__ = ["StringUtils"]


class StringUtils:
    """
    类型转换辅助类
    """

    @staticmethod
    def format_size(size: float, precision: int = 2) -> str:
        """
        字节数转换

        :param size (float): 字节数
        :param precision (int): 小数精度

        :return str: 格式化后的大小字符串
        """
        if not isinstance(size, (int, float)) or size < 0:
            return "N/A"
        suffixes = ["B", "KB", "MB", "GB", "TB"]
        suffix_index = 0
        while size >= 1024 and suffix_index < 4:
            suffix_index += 1
            size /= 1024.0
        return f"{size:.{precision}f} {suffixes[suffix_index]}"

    @staticmethod
    def to_emoji_number(n: int) -> str:
        """
        将一个整数转换为对应的带圈数字 Emoji 字符串 (例如 ①, ②, ⑩)

        :param n (int): 待转换的整数

        :return str: 带圈数字 Emoji 字符串
        """
        if not isinstance(n, int):
            return "❓"
        if n == 10:
            return "⑩"
        emoji_map = {
            "0": "⓪",
            "1": "①",
            "2": "②",
            "3": "③",
            "4": "④",
            "5": "⑤",
            "6": "⑥",
            "7": "⑦",
            "8": "⑧",
            "9": "⑨",
        }
        return "".join(emoji_map.get(digit, digit) for digit in str(n))
