import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import asyncio
import yt_dlp
import config
import instaloader

logger = logging.getLogger(__name__)

class Downloader:
    def __init__(self) -> None:
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
            return None, None

        except yt_dlp.utils.DownloadError as e:
            logger.error(f"yt_dlp error: {e}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected download error: {e}")
            raise
    
    async def get_video_meta(self, url: str, platform: str) -> dict:
        """Быстро извлекает метаданные видео (ширину, высоту, длительность) без скачивания."""
        def _sync_extract():
            ydl_opts = self._get_ydl_opts(platform, Path("dummy_path.mp4"))
            ydl_opts['extract_flat'] = False  # Нам нужна полная инфа, но без загрузки
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
                
        try:
            info = await asyncio.to_thread(_sync_extract)
            return {
                'width': info.get('width'),
                'height': info.get('height'),
                'duration': int(info.get('duration')) if info.get('duration') else None
            }
        except Exception as e:
            logger.warning(f"Не удалось извлечь метаданные видео: {e}")
            return {}
    
    def _sync_download_insta_photos(self, post_url: str, target_dir: Path) -> list[Path]:
        """Синхронное скачивание картинок из инстаграм-поста."""
        try:
            # Извлекаем shortcode из ссылки (например, из /p/DZjnKwHAeDq/ берем DZjnKwHAeDq)
            shortcode = post_url.split("/p/")[1].split("/")[0]
        except IndexError:
            try:
                shortcode = post_url.split("/reel/")[1].split("/")[0]
            except IndexError:
                return []

        try:
            post = instaloader.Post.from_shortcode(self.il.context, shortcode)
            self.il.dirname_pattern = str(target_dir)
            
            # Скачиваем пост (картинки сохранятся в указанную папку)
            self.il.download_post(post, target=shortcode)
            
            # Собираем пути ко всем скачанным файлам .jpg
            photos = sorted(list(target_dir.glob("*.jpg")))
            return photos
        except Exception as e:
            logger.error(f"Instaloader error: {e}")
            return []

    async def download_instagram_photos(self, url: str) -> list[str]:
        """Скачивает картинки и возвращает список путей к ним."""
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        target_dir = config.DOWNLOADS_DIR / f"insta_pts_{ts}"
        target_dir.mkdir(parents=True, exist_ok=True)
        
        photos = await asyncio.to_thread(self._sync_download_insta_photos, url, target_dir)
        return [str(p) for p in photos]