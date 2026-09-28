"""库对外抛出的异常类型。"""

from __future__ import annotations


class InfluencerSyncError(Exception):
    """所有库异常的基类。"""


class StorageError(InfluencerSyncError):
    """存储层错误。"""


class ValidationError(InfluencerSyncError):
    """单条新建/修改时的校验错误（含全部错误项）。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class ImportFileError(InfluencerSyncError):
    """导入文件整体不可解析。"""


class NotFoundError(InfluencerSyncError):
    """按编号找不到达人。"""
