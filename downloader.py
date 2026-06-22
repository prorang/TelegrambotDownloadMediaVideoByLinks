import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import asyncio
import yt_dlp
import config

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

    def _get_ydl_opts(self, platform: str, destination: Path) -> dict:
        """Возвращает настройки в зависимости от платформы."""
        base_opts = {
            'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'js_runtimes': {
                'deno': {},
                'node': {'executable': '/usr/bin/node'},
            },
            'outtmpl': str(destination),
        }

        # Если это Instagram или TikTok — качаем чистый оригинал без сжатия ffmpeg
        if platform in ["instagram", "tiktok"]:
            base_opts['format'] = 'bestvideo+bestaudio/best'
            return base_opts

        # Для остальных платформ (YouTube, Facebook) применяем сжатие
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