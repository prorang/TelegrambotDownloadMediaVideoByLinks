"""Уборка временных файлов в downloads/.

Единственная ответственность модуля — удаление скачанных файлов.
Кто и когда вызывает уборку — забота вызывающей стороны (handlers).
"""

import shutil
import threading
import time
from collections.abc import Iterable
from pathlib import Path


class DownloadsCleaner:
    """Удаляет временные файлы загрузок.

    Экземпляр один на приложение. Метод sweep() защищён неблокирующим замком:
    если уборка уже идёт в другом месте, повторный вызов сразу выходит.
    """

    def __init__(self, root: Path, ttl: int = 30 * 60) -> None:
        self._root = Path(root)
        self._ttl = ttl
        self._lock = threading.Lock()

    def remove(self, path: str | Path) -> None:
        """Удаляет конкретный артефакт загрузки.

        Файл — вместе с временными «хвостами» (.part, .fXXX и т.п.),
        каталог — целиком.
        """
        path = Path(path)
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                return
            for leftover in path.parent.glob(f"{path.stem}*"):
                leftover.unlink(missing_ok=True)
            print(f"🧹 Удалён временный файл: {path}")
        except Exception as e:
            print(f"⚠️ Не удалось удалить {path}: {e}")

    def sweep(self, protected: Iterable[str | Path] = (), force: bool = False) -> int:
        """Проходит по каталогу и удаляет всё лишнее.

        protected — пути, которые сейчас нужны (ждут решения пользователя).
        force=True — снести всё подчистую (например, при старте бота).
        Если уборка уже идёт, вызов выходит и возвращает 0.
        """
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            if not self._root.exists():
                return 0
            if force:
                return self._remove_all()
            return self._remove_stale(protected)
        finally:
            self._lock.release()

    def _remove_all(self) -> int:
        removed = 0
        for item in self._root.iterdir():
            self._delete(item)
            removed += 1
        return removed

    def _remove_stale(self, protected: Iterable[str | Path]) -> int:
        now = time.time()
        keep = {Path(p).resolve() for p in protected}
        removed = 0

        # 1. Старые файлы, которые никто не ждёт
        for item in self._root.rglob("*"):
            try:
                if not item.is_file() or item.resolve() in keep:
                    continue
                if now - item.stat().st_mtime <= self._ttl:
                    continue  # свежий — возможно, ещё качается или отправляется
                item.unlink(missing_ok=True)
                removed += 1
            except Exception as e:
                print(f"⚠️ Не удалось удалить {item}: {e}")

        # 2. Опустевшие каталоги
        for folder in sorted(self._root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            except Exception as e:
                print(f"⚠️ Не удалось удалить папку {folder}: {e}")

        return removed

    @staticmethod
    def _delete(item: Path) -> None:
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)
