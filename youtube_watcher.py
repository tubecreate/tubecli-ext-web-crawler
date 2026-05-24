"""
YouTube Channel Watcher — Theo dõi kênh YouTube tự động.
- Dùng RSS Feed công cộng của YouTube (không cần API key).
- Phát hiện video mới → Tách transcript → AI viết lại → Đăng WordPress.
"""
import asyncio
import json
import os
import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger("YouTubeWatcher")

try:
    from tubecli.config import DATA_DIR
except ImportError:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")

YT_WATCHES_FILE = os.path.join(str(DATA_DIR), "yt_watches.json")
YT_WATCH_LOGS_FILE = os.path.join(str(DATA_DIR), "yt_watch_logs.json")
TUBECLI_BASE_URL = os.environ.get("TUBECLI_BASE_URL", "http://localhost:5295")


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

class YouTubeWatchConfig:
    def __init__(self, data: dict):
        self.id: str = data.get("id", str(uuid.uuid4()))
        self.channel_url: str = data.get("channel_url", "")
        self.channel_id: str = data.get("channel_id", "")     # UCxxxxxx
        self.channel_name: str = data.get("channel_name", "")
        self.channel_thumbnail: str = data.get("channel_thumbnail", "")
        self.interval_hours: float = data.get("interval_hours", 6)
        self.target_site: str = data.get("target_site", "")
        self.instruction: str = data.get("instruction", "Viết lại thành bài báo hoàn chỉnh")
        self.embed_original_video: bool = data.get("embed_original_video", True)
        self.max_videos_per_check: int = data.get("max_videos_per_check", 3)
        self.processed_video_ids: List[str] = data.get("processed_video_ids", [])
        self.status: str = data.get("status", "active")
        self.created_at: str = data.get("created_at", datetime.now().isoformat())
        self.last_checked_at: Optional[str] = data.get("last_checked_at")
        self.next_check_at: Optional[str] = data.get("next_check_at")
        self.is_initialized: bool = data.get("is_initialized", False)
        self.wp_category_name: Optional[str] = data.get("wp_category_name")
        self.wp_category_id: Optional[int] = data.get("wp_category_id")
        self.telegram_chat_id: Optional[int] = data.get("telegram_chat_id")
        self.telegram_token: Optional[str] = data.get("telegram_token")
        self.stats: dict = data.get("stats", {
            "total_checked": 0,
            "total_published": 0,
            "total_skipped": 0,
            "last_published_title": None,
        })

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_url": self.channel_url,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "channel_thumbnail": self.channel_thumbnail,
            "interval_hours": self.interval_hours,
            "target_site": self.target_site,
            "instruction": self.instruction,
            "embed_original_video": self.embed_original_video,
            "max_videos_per_check": self.max_videos_per_check,
            "processed_video_ids": self.processed_video_ids,
            "status": self.status,
            "created_at": self.created_at,
            "last_checked_at": self.last_checked_at,
            "next_check_at": self.next_check_at,
            "is_initialized": self.is_initialized,
            "wp_category_name": self.wp_category_name,
            "wp_category_id": self.wp_category_id,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_token": self.telegram_token,
            "stats": self.stats,
        }


# ─────────────────────────────────────────────────────────────────
# Watcher
# ─────────────────────────────────────────────────────────────────

class YouTubeWatcher:
    """Manages YouTube channel watches and background scheduling."""

    def __init__(self):
        self._watches: Dict[str, YouTubeWatchConfig] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        self._load_watches()

    # ── Persistence ────────────────────────────────────────────

    def _load_watches(self):
        if os.path.exists(YT_WATCHES_FILE):
            try:
                with open(YT_WATCHES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    w = YouTubeWatchConfig(item)
                    self._watches[w.id] = w
                logger.info(f"Loaded {len(self._watches)} YouTube watches from disk")
            except Exception as e:
                logger.error(f"Error loading yt_watches: {e}")

    def _save_watches(self):
        os.makedirs(os.path.dirname(YT_WATCHES_FILE), exist_ok=True)
        try:
            data = [w.to_dict() for w in self._watches.values()]
            with open(YT_WATCHES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving yt_watches: {e}")

    def _append_log(self, watch_id: str, log_entry: dict):
        logs = []
        if os.path.exists(YT_WATCH_LOGS_FILE):
            try:
                with open(YT_WATCH_LOGS_FILE, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            except Exception:
                logs = []

        log_entry["watch_id"] = watch_id
        log_entry["timestamp"] = datetime.now().isoformat()
        logs.append(log_entry)

        if len(logs) > 1000:
            logs = logs[-1000:]

        try:
            with open(YT_WATCH_LOGS_FILE, "w", encoding="utf-8") as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_logs(self, watch_id: str, limit: int = 50) -> list:
        if not os.path.exists(YT_WATCH_LOGS_FILE):
            return []
        try:
            with open(YT_WATCH_LOGS_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
            filtered = [l for l in logs if l.get("watch_id") == watch_id]
            return filtered[-limit:]
        except Exception:
            return []

    # ── CRUD ────────────────────────────────────────────────────

    async def add_watch(
        self,
        channel_url: str,
        interval_hours: float = 6,
        target_site: str = "",
        instruction: str = "Viết lại thành bài báo hoàn chỉnh",
        embed_original_video: bool = True,
        max_videos_per_check: int = 3,
        wp_category_name: str = None,
        telegram_chat_id: int = None,
        telegram_token: str = None,
    ) -> YouTubeWatchConfig:
        """Resolve channel ID then create watch."""
        # Resolve channel_id from URL
        channel_id, channel_name, channel_thumbnail = await self._resolve_channel(channel_url)

        if not channel_id:
            raise ValueError(f"Không thể xác định channel ID từ URL: {channel_url}")

        # Check if same channel already watched
        for w in self._watches.values():
            if w.channel_id == channel_id and w.status != "deleted":
                w.status = "active"
                w.interval_hours = interval_hours
                w.target_site = target_site
                w.instruction = instruction
                w.embed_original_video = embed_original_video
                w.max_videos_per_check = max_videos_per_check
                if wp_category_name:
                    w.wp_category_name = wp_category_name
                    w.wp_category_id = None
                w.next_check_at = (datetime.now() + timedelta(minutes=2)).isoformat()
                self._save_watches()
                return w

        watch = YouTubeWatchConfig({
            "channel_url": channel_url,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "channel_thumbnail": channel_thumbnail,
            "interval_hours": interval_hours,
            "target_site": target_site,
            "instruction": instruction,
            "embed_original_video": embed_original_video,
            "max_videos_per_check": max_videos_per_check,
            "wp_category_name": wp_category_name,
            "telegram_chat_id": telegram_chat_id,
            "telegram_token": telegram_token,
            "next_check_at": (datetime.now() + timedelta(minutes=1)).isoformat(),
        })
        self._watches[watch.id] = watch
        self._save_watches()
        logger.info(f"Added YouTube watch: {channel_name} ({channel_id})")
        return watch

    def remove_watch(self, watch_id: str) -> bool:
        if watch_id in self._watches:
            del self._watches[watch_id]
            self._save_watches()
            return True
        return False

    def update_watch(self, watch_id: str, **kwargs) -> Optional[YouTubeWatchConfig]:
        w = self._watches.get(watch_id)
        if not w:
            return None
        for k, v in kwargs.items():
            if hasattr(w, k) and v is not None:
                setattr(w, k, v)
        self._save_watches()
        return w

    def pause_watch(self, watch_id: str) -> bool:
        w = self._watches.get(watch_id)
        if w:
            w.status = "paused"
            self._save_watches()
            return True
        return False

    def resume_watch(self, watch_id: str) -> bool:
        w = self._watches.get(watch_id)
        if w:
            w.status = "active"
            w.next_check_at = (datetime.now() + timedelta(minutes=1)).isoformat()
            self._save_watches()
            return True
        return False

    def list_watches(self) -> List[dict]:
        return [w.to_dict() for w in self._watches.values()]

    def get_watch(self, watch_id: str) -> Optional[YouTubeWatchConfig]:
        return self._watches.get(watch_id)

    # ── Scheduler ───────────────────────────────────────────────

    def start_scheduler(self):
        if self._running:
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("YouTubeWatcher scheduler started")

    def stop_scheduler(self):
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None

    async def _scheduler_loop(self):
        logger.info("YouTubeWatcher scheduler loop running...")
        while self._running:
            try:
                now = datetime.now()
                for watch in list(self._watches.values()):
                    if watch.status != "active":
                        continue
                    if not watch.next_check_at:
                        continue
                    try:
                        next_check = datetime.fromisoformat(watch.next_check_at)
                    except Exception:
                        continue
                    if now >= next_check:
                        logger.info(f"⏰ YouTube Scheduler triggered for: {watch.channel_name}")
                        try:
                            await self.check_watch(watch.id)
                        except Exception as e:
                            logger.error(f"Error checking YouTube watch {watch.channel_id}: {e}")
                            self._append_log(watch.id, {"type": "error", "message": str(e)[:300]})
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"YouTubeWatcher scheduler error: {e}")

            await asyncio.sleep(60)

    # ── Core Check ──────────────────────────────────────────────

    async def check_watch(self, watch_id: str) -> dict:
        """Check for new videos on a YouTube channel."""
        watch = self._watches.get(watch_id)
        if not watch:
            return {"error": "Watch not found"}

        watch.last_checked_at = datetime.now().isoformat()
        watch.stats["total_checked"] = watch.stats.get("total_checked", 0) + 1

        result = {
            "watch_id": watch_id,
            "channel_name": watch.channel_name,
            "new_videos": [],
            "published": [],
            "skipped": [],
            "errors": [],
        }

        try:
            # Step 1: Get videos from RSS feed
            videos = await self._fetch_rss_videos(watch.channel_id)
            if not videos:
                watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
                self._save_watches()
                self._append_log(watch_id, {"type": "check", "message": "RSS không trả video nào."})
                result["message"] = "RSS feed trống."
                return result

            # Step 2: First run — snapshot current videos
            if not watch.is_initialized:
                video_ids = [v["video_id"] for v in videos]
                watch.processed_video_ids = video_ids
                watch.is_initialized = True
                watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
                self._save_watches()

                msg = f"📸 Khởi tạo: ghi nhận {len(video_ids)} video hiện tại. Sẽ xử lý video MỚI từ lần sau."
                self._append_log(watch_id, {"type": "init", "message": msg})
                result["message"] = msg
                return result

            # Step 3: Find new videos
            new_videos = [v for v in videos if v["video_id"] not in watch.processed_video_ids]
            if not new_videos:
                watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
                self._save_watches()
                self._append_log(watch_id, {"type": "check", "message": f"Không có video mới. Đã check {len(videos)} video."})
                result["message"] = "Không có video mới."
                return result

            # Step 4: Process new videos
            to_process = new_videos[:watch.max_videos_per_check]
            result["new_videos"] = [v["video_id"] for v in to_process]
            logger.info(f"🆕 Found {len(new_videos)} new videos for {watch.channel_name}, processing {len(to_process)}")

            for video in to_process:
                try:
                    pub_result = await self._process_video(video, watch)
                    if pub_result.get("success"):
                        result["published"].append({
                            "video_id": video["video_id"],
                            "title": pub_result.get("title", ""),
                            "post_url": pub_result.get("post_url", ""),
                        })
                        watch.stats["total_published"] = watch.stats.get("total_published", 0) + 1
                        watch.stats["last_published_title"] = pub_result.get("title", "")
                        
                        # Gi log index
                        idx_res = pub_result.get("google_indexed")
                        if idx_res is not None:
                            status_icon = "✅" if idx_res else "❌"
                            self._append_log(watch_id, {
                                "type": "info",
                                "message": f"Google Indexing API ({status_icon}): {pub_result.get('post_url', '')}"
                            })
                    elif pub_result.get("skipped"):
                        result["skipped"].append({
                            "video_id": video["video_id"],
                            "reason": pub_result.get("reason", ""),
                        })
                        watch.stats["total_skipped"] = watch.stats.get("total_skipped", 0) + 1
                    else:
                        result["errors"].append({
                            "video_id": video["video_id"],
                            "error": pub_result.get("error", "Unknown"),
                        })
                except Exception as e:
                    result["errors"].append({"video_id": video["video_id"], "error": str(e)[:200]})

                watch.processed_video_ids.append(video["video_id"])
                await asyncio.sleep(3)

            # Keep last 500 processed IDs
            if len(watch.processed_video_ids) > 500:
                watch.processed_video_ids = watch.processed_video_ids[-500:]

            watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
            self._save_watches()

            self._append_log(watch_id, {
                "type": "check_complete",
                "message": (
                    f"Tìm thấy {len(new_videos)} video mới, xử lý {len(to_process)}, "
                    f"đăng {len(result['published'])}, bỏ qua {len(result['skipped'])}"
                ),
                "published_count": len(result["published"]),
                "skipped_count": len(result["skipped"]),
                "error_count": len(result["errors"]),
            })

            return result

        except Exception as e:
            watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
            self._save_watches()
            self._append_log(watch_id, {"type": "error", "message": str(e)[:300]})
            raise

    # ── Video Processing Pipeline ────────────────────────────────

    async def _process_video(self, video: dict, watch: YouTubeWatchConfig) -> dict:
        """Pipeline: Transcript → AI Rewrite → WordPress Publish."""
        import httpx
        from youtube_scraper import get_transcript, get_video_metadata, build_youtube_embed_html

        video_id = video["video_id"]
        video_title = video.get("title", "")
        logger.info(f"🎬 Processing video: {video_title} [{video_id}]")

        # Step 1: Get full video metadata
        meta = await get_video_metadata(video_id)
        title = meta.get("title") or video_title or f"Video {video_id}"
        thumbnail_url = meta.get("thumbnail_url", "")

        # Step 2: Get transcript
        transcript = get_transcript(video_id, title=title)
        if not transcript:
            logger.info(f"[Pipeline] No transcript for {video_id} — skipping")
            return {"skipped": True, "reason": "Không có phụ đề/transcript"}

        # Step 3: AI Rewrite (transcript → article)
        provider, model = self._get_default_ai_model()

        async with httpx.AsyncClient(timeout=180) as client:
            rewrite_resp = await client.post(
                f"{TUBECLI_BASE_URL}/api/v1/web_crawler/rewrite",
                json={
                    "title": title,
                    "content": transcript,
                    "instruction": (
                        f"{watch.instruction}\n\n"
                        "QUAN TRỌNG: Đây là transcript (phụ đề tự động) từ video YouTube. "
                        "Hãy viết lại thành bài báo đọc được, cấu trúc rõ ràng theo đoạn văn. "
                        "Loại bỏ các ký tự lặp, từ đệm và lỗi OCR. KHÔNG sử dụng markdown (**, __, ##)."
                    ),
                    "provider": provider,
                    "model": model,
                },
                timeout=180,
            )

            if rewrite_resp.status_code != 200:
                return {"success": False, "error": f"AI rewrite failed: HTTP {rewrite_resp.status_code}"}

            rewrite_data = rewrite_resp.json()
            if not rewrite_data.get("success"):
                return {"success": False, "error": rewrite_data.get("detail", "AI error")}

            new_title = rewrite_data.get("title", title)
            new_content = rewrite_data.get("content", "")

            # Step 4: Build HTML — embed video + article content
            html_parts = []
            if watch.embed_original_video:
                html_parts.append(build_youtube_embed_html(video_id))

            # Convert line breaks to HTML
            article_html = new_content.replace("\n\n", "</p><p>").replace("\n", "<br>")
            article_html = f"<p>{article_html}</p>"
            html_parts.append(article_html)

            final_html = "\n".join(html_parts)

            # Step 5: Publish to WordPress
            wp_site = self._find_wp_site(watch.target_site)
            if not wp_site:
                return {"success": False, "error": f"WordPress site '{watch.target_site}' not found"}

            publish_payload = {
                "wp_url": wp_site["url"],
                "username": wp_site["user"],
                "app_password": wp_site["pass"],
                "title": new_title,
                "content": final_html,
                "status": "publish",
            }

            if thumbnail_url:
                publish_payload["thumbnail_url"] = thumbnail_url

            if watch.wp_category_id:
                publish_payload["category_id"] = watch.wp_category_id
            elif watch.wp_category_name:
                publish_payload["category_name"] = watch.wp_category_name
                
            idx_cred = wp_site.get("google_indexing_cred_id")
            if idx_cred:
                publish_payload["google_indexing_cred_id"] = idx_cred

            publish_resp = await client.post(
                f"{TUBECLI_BASE_URL}/api/v1/web_crawler/publish_wp",
                json=publish_payload,
                timeout=60,
            )

            if publish_resp.status_code == 200:
                pub_data = publish_resp.json()
                if pub_data.get("success"):
                    resolved_cat_id = pub_data.get("category_id")
                    if resolved_cat_id and not watch.wp_category_id:
                        watch.wp_category_id = resolved_cat_id
                        self._save_watches()

                    indexed = pub_data.get("google_indexed")
                    index_msg = f" | Indexed: {indexed}" if indexed is not None else ""
                    logger.info(f"✅ Published: {new_title} → {pub_data.get('post_url', '')}{index_msg}")
                    return {
                        "success": True,
                        "title": new_title,
                        "post_url": pub_data.get("post_url", ""),
                        "post_id": pub_data.get("post_id"),
                        "google_indexed": indexed,
                    }
                return {"success": False, "error": pub_data.get("detail", "Publish failed")}
            else:
                return {"success": False, "error": f"Publish failed: HTTP {publish_resp.status_code}"}

    # ── RSS Feed ─────────────────────────────────────────────────

    async def _fetch_rss_videos(self, channel_id: str) -> List[dict]:
        """Fetch latest videos from YouTube RSS feed."""
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(rss_url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; TubeCLI/1.0)"
                })
                if resp.status_code != 200:
                    logger.warning(f"RSS feed error {resp.status_code} for {channel_id}")
                    return []

                return self._parse_rss(resp.text)
        except Exception as e:
            logger.warning(f"Failed to fetch RSS for {channel_id}: {e}")
            return []

    def _parse_rss(self, xml_text: str) -> List[dict]:
        """Parse YouTube RSS XML, return list of {video_id, title, published}."""
        videos = []
        try:
            import xml.etree.ElementTree as ET
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015",
                "media": "http://search.yahoo.com/mrss/",
            }
            root = ET.fromstring(xml_text)
            entries = root.findall("atom:entry", ns)
            for entry in entries:
                video_id_el = entry.find("yt:videoId", ns)
                title_el = entry.find("atom:title", ns)
                published_el = entry.find("atom:published", ns)
                if video_id_el is not None and title_el is not None:
                    videos.append({
                        "video_id": video_id_el.text,
                        "title": title_el.text or "",
                        "published": published_el.text if published_el is not None else "",
                    })
        except Exception as e:
            logger.warning(f"RSS parse error: {e}")
        return videos

    # ── Channel Resolution ────────────────────────────────────────

    async def _resolve_channel(self, channel_url: str):
        """
        Resolve channel ID, name and thumbnail from a YouTube channel URL.
        Supports: /channel/UC..., /@handle, /c/name, /user/name
        Returns (channel_id, channel_name, thumbnail_url)
        """
        from youtube_scraper import extract_channel_id_from_url

        # Direct channel ID in URL
        channel_id = extract_channel_id_from_url(channel_url)
        if channel_id:
            name, thumb = await self._fetch_channel_meta(channel_id)
            return channel_id, name, thumb

        # Need to resolve via web scraping
        channel_id, name, thumb = await self._resolve_channel_via_web(channel_url)
        return channel_id, name, thumb

    async def _resolve_channel_via_web(self, channel_url: str):
        """Scrape the channel page to find the UCxxxx channel ID."""
        try:
            if not channel_url.startswith("http"):
                channel_url = "https://www.youtube.com/" + channel_url.lstrip("/")

            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }) as client:
                resp = await client.get(channel_url)
                if resp.status_code != 200:
                    return None, "", ""

                html = resp.text

                # Various patterns to find channel ID in page source
                patterns = [
                    r'"channelId":"(UC[A-Za-z0-9_-]+)"',
                    r'"externalChannelId":"(UC[A-Za-z0-9_-]+)"',
                    r'channel/(UC[A-Za-z0-9_-]+)',
                    r'"browseId":"(UC[A-Za-z0-9_-]+)"',
                ]
                channel_id = None
                for pat in patterns:
                    m = re.search(pat, html)
                    if m:
                        channel_id = m.group(1)
                        break

                if not channel_id:
                    return None, "", ""

                # Extract channel name
                name_match = re.search(r'"title":"([^"]{2,80})"', html)
                channel_name = name_match.group(1) if name_match else channel_url.rstrip("/").split("/")[-1]

                # Extract avatar thumbnail
                thumb_match = re.search(r'"avatar".*?"url":"(https://yt3\.ggpht[^"]+)"', html)
                thumbnail = thumb_match.group(1) if thumb_match else ""

                return channel_id, channel_name, thumbnail

        except Exception as e:
            logger.warning(f"Channel resolve via web failed: {e}")
            return None, "", ""

    async def _fetch_channel_meta(self, channel_id: str):
        """Get channel name and thumbnail via RSS feed title."""
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(rss_url)
                if resp.status_code == 200:
                    import xml.etree.ElementTree as ET
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    root = ET.fromstring(resp.text)
                    title_el = root.find("atom:title", ns)
                    name = title_el.text if title_el is not None else channel_id
                    return name, ""
        except Exception:
            pass
        return channel_id, ""

    # ── Helpers ───────────────────────────────────────────────────

    def _find_wp_site(self, keyword: str):
        wp_file = os.path.join(str(DATA_DIR), "wp_sites.json")
        if not os.path.exists(wp_file):
            return None
        try:
            with open(wp_file, "r", encoding="utf-8") as f:
                sites = json.load(f)
        except Exception:
            return None

        if not keyword:
            return sites[0] if sites else None

        keyword_lower = keyword.lower().strip()
        for s in sites:
            name = (s.get("name") or "").lower()
            url = (s.get("url") or "").lower()
            if keyword_lower in name or keyword_lower in url:
                return s
        return None

    def _get_default_ai_model(self):
        try:
            settings_file = os.path.join(str(DATA_DIR), "global_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                model_str = data.get("default_model", "")
                if model_str and "|" in model_str:
                    return model_str.split("|", 1)
                elif model_str:
                    lower = model_str.lower()
                    if "deepseek" in lower:
                        return ("deepseek", model_str)
                    elif "gemini" in lower:
                        return ("gemini", model_str)
                    elif "gpt" in lower or "o1" in lower:
                        return ("openai", model_str)
                    elif "claude" in lower:
                        return ("claude", model_str)
                    elif "grok" in lower:
                        return ("grok", model_str)
                    else:
                        return ("ollama", model_str)
        except Exception:
            pass
        return ("gemini", "gemini-2.0-flash")


import httpx

# Global singleton
youtube_watcher = YouTubeWatcher()
