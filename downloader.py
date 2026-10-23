import asyncio
import logging
from datetime import datetime
from itertools import islice
from pathlib import Path
from urllib.parse import urlparse

import instaloader
import yt_dlp

import config
from cleanup import DownloadsCleaner

logger = logging.getLogger(__name__)


class Downloader:
    # Платформы, для которых комментарии реально достаются текущей реализацией.
    COMMENT_PLATFORMS = frozenset({"youtube", "instagram"})

    def __init__(self, cleaner: DownloadsCleaner) -> None:
        self._cleaner = cleaner
        self._platforms_map = {
            "youtu": "youtube",
            "tiktok": "tiktok",
            "instagram": "instagram",
            "facebook": "facebook",
            "fb.watch": "facebook"
        }
        self.il = instaloader.Instaloader(
            download_videos=False, 
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            post_metadata_txt_pattern=""
        )

    def _get_ydl_opts(self, platform: str, destination: Path) -> dict:
        """Возвращает настройки в зависимости от платформы."""
        
        base_opts = {
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'js_runtimes': {
                'deno': {},
                'node': {'executable': '/usr/bin/node'},
            },
            'outtmpl': str(destination),
        }

        # Настройка форматов для Instagram и TikTok
        if platform == "instagram":
            # ХАК ДЛЯ IPHONE: принудительно забираем кодек avc1 (H.264) и mp4a (AAC)
            base_opts['format'] = 'bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[vcodec^=avc1]/best'
            base_opts['cookiefile'] = '/app/cookies.txt'
            return base_opts
        elif platform == "tiktok":
            # Для TikTok оставляем чистый оригинал без изменений
            base_opts['format'] = 'bestvideo+bestaudio/best'
            return base_opts

        # Для остальных платформ (YouTube, Facebook) применяем сжатие через ffmpeg
        base_opts['postprocessors'] = [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
        base_opts['postprocessor_args'] = [
            '-vcodec', 'libx264',
            '-crf', '18',
            '-preset', 'slow',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart'
        ]
        return base_opts

    def _generate_filepath(self, prefix: str) -> Path:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        platform_dir = config.DOWNLOADS_DIR / prefix
        platform_dir.mkdir(parents=True, exist_ok=True)
        return platform_dir / f"{prefix}_{ts}.mp4"

    def _detect_platform(self, url: str) -> str | None:
        parsed_url = urlparse(url)
        host = (parsed_url.hostname or "").lower()
        
        for key, platform in self._platforms_map.items():
            if key in host or key in url.lower():
                return platform
        return None

    def _sync_download(self, url: str, destination: Path, platform: str) -> str:
        # Получаем кастомные настройки под конкретную платформу
        ydl_opts = self._get_ydl_opts(platform, destination)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        return str(destination)

    async def download_video(self, url: str) -> tuple[str | None, str | None]:
        """Возвращает кортеж (путь_к_файлу, имя_платформы)."""
        platform = self._detect_platform(url)
        if not platform:
            logger.warning(f"Unsupported URL: {url}")
            return None, None

        file_path = self._generate_filepath(platform)
        logger.info(f"Starting download [{platform}]: {url}")

        try:
            path_str = await asyncio.to_thread(self._sync_download, url, file_path, platform)
            path = Path(path_str)

            if path.exists() and path.stat().st_size > 1024:
                logger.info(f"Successfully downloaded: {path_str}")
                return path_str, platform

            logger.error(f"File is empty or does not exist: {path_str}")
            self._cleaner.remove(file_path)
            return None, None

        except yt_dlp.utils.DownloadError as e:
            logger.error(f"yt_dlp error: {e}")
            self._cleaner.remove(file_path)
            raise
        except Exception as e:
            logger.exception(f"Unexpected download error: {e}")
            self._cleaner.remove(file_path)
            raise
    
    @staticmethod
    def _estimate_filesize(info: dict) -> int | None:
        """Оценивает итоговый размер файла (видео + аудио) в байтах до скачивания."""
        def fmt_size(f: dict) -> int:
            return f.get('filesize') or f.get('filesize_approx') or 0

        # 1. Самый точный вариант: потоки, которые yt-dlp уже выбрал под текущий format
        #    (для YouTube это адаптивные bestvideo + bestaudio).
        requested = info.get('requested_formats') or info.get('requested_downloads')
        if requested:
            total = sum(fmt_size(f) for f in requested)
            if total:
                return int(total)

        total = info.get('filesize') or info.get('filesize_approx')
        if total:
            return int(total)

        formats = info.get('formats') or []

        # 2. Склеенный формат: видео и аудио в одном файле.
        #    Важно: на YouTube такой формат обычно 360p и очень лёгкий, поэтому
        #    НЕЛЬЗЯ на нём останавливаться — иначе реальные тяжёлые потоки игнорируются.
        combined = [fmt_size(f) for f in formats
                    if f.get('vcodec') not in (None, 'none')
                    and f.get('acodec') not in (None, 'none')]

        # 3. Раздельные потоки — именно их yt-dlp и складывает в итоговый файл.
        video_sizes = [fmt_size(f) for f in formats
                       if f.get('vcodec') not in (None, 'none')
                       and f.get('acodec') in (None, 'none')]
        audio_sizes = [fmt_size(f) for f in formats
                       if f.get('acodec') not in (None, 'none')
                       and f.get('vcodec') in (None, 'none')]
        separate = max(video_sizes) + max(audio_sizes) if video_sizes and audio_sizes else 0

        # Берём максимум из вариантов: лучше лишний раз спросить, чем молча качать большой файл.
        candidates = [size for size in (
            max(combined) if combined else 0,
            separate,
        ) if size]
        return int(max(candidates)) if candidates else None

    async def get_video_meta(self, url: str, platform: str) -> dict:
        """Быстро извлекает метаданные видео (ширину, высоту, длительность) без скачивания."""
        def _sync_extract():
            ydl_opts = self._get_ydl_opts(platform, Path("dummy_path.mp4"))
            ydl_opts['extract_flat'] = False  # Нам нужна полная инфа, но без загрузки
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
                
        try:
            info = await asyncio.to_thread(_sync_extract)
            if not info:
                return {}
            return {
                'width': info.get('width'),
                'height': info.get('height'),
                'duration': int(info.get('duration')) if info.get('duration') else None,
                'filesize': self._estimate_filesize(info),
                'like_count': info.get('like_count'),
            }
        except Exception as e:
            logger.warning(f"Не удалось извлечь метаданные (возможно, это карусель с фото): {e}")
            return {}
    
    @staticmethod
    def _extract_instagram_shortcode(url: str) -> str | None:
        """Достаёт shortcode из ссылки вида /p/<code>/, /reel/<code>/ или /tv/<code>/."""
        for marker in ("/p/", "/reel/", "/tv/"):
            if marker in url:
                return url.split(marker)[1].split("/")[0]
        return None

    def _sync_download_insta_photos(self, post_url: str, target_dir: Path) -> tuple[list[Path], int | None]:
        """Синхронное скачивание картинок из инстаграм-поста.

        Возвращает пути к файлам и количество лайков поста (если удалось узнать).
        """
        shortcode = self._extract_instagram_shortcode(post_url)
        if not shortcode:
            return [], None

        try:
            post = instaloader.Post.from_shortcode(self.il.context, shortcode)
            self.il.dirname_pattern = str(target_dir)

            # Скачиваем пост (картинки сохранятся в указанную папку)
            self.il.download_post(post, target=shortcode)

            # Собираем пути ко всем скачанным файлам .jpg
            photos = sorted(target_dir.glob("*.jpg"))
            try:
                likes = post.likes
            except Exception:
                likes = None
            return photos, likes
        except Exception as e:
            logger.error(f"Instaloader error: {e}")
            return [], None

    async def download_instagram_photos(self, url: str) -> tuple[list[str], int | None]:
        """Скачивает картинки и возвращает (список путей, количество лайков)."""
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        target_dir = config.DOWNLOADS_DIR / f"insta_pts_{ts}"
        target_dir.mkdir(parents=True, exist_ok=True)

        photos, likes = await asyncio.to_thread(self._sync_download_insta_photos, url, target_dir)

        # Ничего не скачалось (или упало на середине) — подчищаем папку, чтобы не оставлять мусор
        if not photos:
            self._cleaner.remove(target_dir)
            return [], likes

        return [str(p) for p in photos], likes

    def _sync_ydl_comments(self, url: str, platform: str, limit: int) -> list[dict]:
        """Достаёт комментарии через yt-dlp (YouTube, а также платформы, где это поддерживается)."""
        ydl_opts = self._get_ydl_opts(platform, Path("dummy_comments.mp4"))
        ydl_opts.update({
            'skip_download': True,
            'getcomments': True,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        })

        if platform == "youtube":
            # top — сортировка на стороне YouTube; 0 ответов, только корневые комментарии.
            ydl_opts['extractor_args'] = {
                'youtube': {
                    'comment_sort': ['top'],
                    'max_comments': [str(limit), str(limit), '0', '0', '0'],
                },
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        comments = list((info or {}).get('comments') or [])
        comments = [c for c in comments if c.get('parent') in (None, 'root')]
        comments.sort(key=lambda c: c.get('like_count') or 0, reverse=True)

        result = []
        for comment in comments[:limit]:
            text = (comment.get('text') or '').strip()
            if not text:
                continue
            result.append({
                'author': comment.get('author') or 'аноним',
                'text': text,
                'like_count': comment.get('like_count') or 0,
            })
        return result

    def _sync_instagram_comments(self, url: str, limit: int) -> list[dict]:
        """Достаёт комментарии поста Instagram через instaloader."""
        shortcode = self._extract_instagram_shortcode(url)
        if not shortcode:
            return []

        post = instaloader.Post.from_shortcode(self.il.context, shortcode)

        # Не тянем весь пост: для топа достаточно первых ~50-75 комментариев.
        comments = []
        for comment in islice(post.get_comments(), max(limit * 5, 50)):
            text = (comment.text or '').strip()
            if not text:
                continue
            try:
                author = comment.owner.username
            except Exception:
                author = 'аноним'
            comments.append({
                'author': author or 'аноним',
                'text': text,
                'like_count': comment.likes_count or 0,
            })

        comments.sort(key=lambda c: c['like_count'], reverse=True)
        return comments[:limit]

    def supports_comments(self, platform: str | None) -> bool:
        """Можно ли показать кнопку комментариев для этой платформы."""
        return platform in self.COMMENT_PLATFORMS

    async def get_top_comments(self, url: str, platform: str, limit: int = 15) -> list[dict]:
        """Возвращает топ комментариев по лайкам. Пустой список — комментарии недоступны."""
        if platform == "instagram":
            # Основной путь — yt-dlp: он использует /app/cookies.txt и умеет
            # тянуть комментарии Instagram от имени залогиненной сессии.
            try:
                comments = await asyncio.to_thread(
                    self._sync_ydl_comments, url, platform, limit
                )
                if comments:
                    logger.info(f"Instagram: получено {len(comments)} комментариев")
                    return comments
                logger.warning(f"Instagram: yt-dlp вернул 0 комментариев для {url}")
            except Exception:
                logger.warning(
                    f"Instagram: yt-dlp не смог получить комментарии для {url}",
                    exc_info=True,
                )

            # Фолбэк — instaloader (работает, только если у него есть сессия).
            try:
                return await asyncio.to_thread(self._sync_instagram_comments, url, limit)
            except Exception:
                logger.warning(
                    f"Instagram: instaloader не смог получить комментарии для {url}",
                    exc_info=True,
                )
                return []

        try:
            return await asyncio.to_thread(self._sync_ydl_comments, url, platform, limit)
        except Exception:
            logger.warning(
                f"Не удалось получить комментарии для {url}",
                exc_info=True,
            )
            return []
