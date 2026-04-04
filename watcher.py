"""
Page Watcher — Theo dõi trang web tự động và đăng bài mới lên WordPress.
Background scheduler kiểm tra định kỳ, phát hiện bài mới bằng cách so sánh link snapshots.
"""
import asyncio
import json
import os
import re
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, urljoin

logger = logging.getLogger("PageWatcher")

try:
    from tubecli.config import DATA_DIR
except ImportError:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")

WATCHES_FILE = os.path.join(str(DATA_DIR), "watches.json")
WATCH_LOGS_FILE = os.path.join(str(DATA_DIR), "watch_logs.json")
TUBECLI_BASE_URL = os.environ.get("TUBECLI_BASE_URL", "http://localhost:5295")


class WatchConfig:
    """Cấu hình theo dõi một trang web."""

    def __init__(self, data: dict):
        self.id: str = data.get("id", str(uuid.uuid4()))
        self.url: str = data.get("url", "")
        self.interval_hours: float = data.get("interval_hours", 6)
        self.target_site: str = data.get("target_site", "")
        self.instruction: str = data.get("instruction", "dịch sang tiếng anh")
        self.telegram_chat_id: Optional[int] = data.get("telegram_chat_id")
        self.telegram_token: Optional[str] = data.get("telegram_token")
        self.status: str = data.get("status", "active")  # active, paused, error
        self.created_at: str = data.get("created_at", datetime.now().isoformat())
        self.last_checked_at: Optional[str] = data.get("last_checked_at")
        self.next_check_at: Optional[str] = data.get("next_check_at")
        self.processed_urls: List[str] = data.get("processed_urls", [])
        self.url_pattern: Optional[str] = data.get("url_pattern")
        self.max_articles_per_check: int = data.get("max_articles_per_check", 5)
        self.stats: dict = data.get("stats", {
            "total_checked": 0,
            "total_published": 0,
            "last_published_url": None,
        })
        # Track if this is the first check (snapshot mode)
        self.is_initialized: bool = data.get("is_initialized", False)
        # WordPress category (cached from last publish)
        self.wp_category_name: Optional[str] = data.get("wp_category_name")
        self.wp_category_id: Optional[int] = data.get("wp_category_id")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "interval_hours": self.interval_hours,
            "target_site": self.target_site,
            "instruction": self.instruction,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_token": self.telegram_token,
            "status": self.status,
            "created_at": self.created_at,
            "last_checked_at": self.last_checked_at,
            "next_check_at": self.next_check_at,
            "processed_urls": self.processed_urls,
            "url_pattern": self.url_pattern,
            "max_articles_per_check": self.max_articles_per_check,
            "stats": self.stats,
            "is_initialized": self.is_initialized,
            "wp_category_name": self.wp_category_name,
            "wp_category_id": self.wp_category_id,
        }


class PageWatcher:
    """Manages page watches and background scheduling."""

    def __init__(self):
        self._watches: Dict[str, WatchConfig] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        self._load_watches()

    # ── Persistence ──────────────────────────────────────────

    def _load_watches(self):
        """Load watches from JSON file."""
        if os.path.exists(WATCHES_FILE):
            try:
                with open(WATCHES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    w = WatchConfig(item)
                    self._watches[w.id] = w
                logger.info(f"Loaded {len(self._watches)} watches from disk")
            except Exception as e:
                logger.error(f"Error loading watches: {e}")

    def _save_watches(self):
        """Persist watches to JSON file."""
        os.makedirs(os.path.dirname(WATCHES_FILE), exist_ok=True)
        try:
            data = [w.to_dict() for w in self._watches.values()]
            with open(WATCHES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving watches: {e}")

    def _append_log(self, watch_id: str, log_entry: dict):
        """Append a log entry for a watch."""
        logs = []
        if os.path.exists(WATCH_LOGS_FILE):
            try:
                with open(WATCH_LOGS_FILE, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            except Exception:
                logs = []

        log_entry["watch_id"] = watch_id
        log_entry["timestamp"] = datetime.now().isoformat()
        logs.append(log_entry)

        # Keep only last 500 entries
        if len(logs) > 500:
            logs = logs[-500:]

        try:
            with open(WATCH_LOGS_FILE, "w", encoding="utf-8") as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_logs(self, watch_id: str, limit: int = 50) -> list:
        """Get logs for a specific watch."""
        if not os.path.exists(WATCH_LOGS_FILE):
            return []
        try:
            with open(WATCH_LOGS_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
            filtered = [l for l in logs if l.get("watch_id") == watch_id]
            return filtered[-limit:]
        except Exception:
            return []

    # ── CRUD ─────────────────────────────────────────────────

    def add_watch(self, url: str, interval_hours: float = 6,
                  target_site: str = "", instruction: str = "dịch sang tiếng anh",
                  telegram_chat_id: int = None, telegram_token: str = None,
                  max_articles_per_check: int = 5, url_pattern: str = None,
                  wp_category_name: str = None) -> WatchConfig:
        """Add a new page watch."""
        # Check if same URL already watched
        for w in self._watches.values():
            if w.url == url and w.status != "deleted":
                # Reactivate if paused
                w.status = "active"
                w.interval_hours = interval_hours
                w.target_site = target_site
                w.instruction = instruction
                w.telegram_chat_id = telegram_chat_id
                w.telegram_token = telegram_token
                w.max_articles_per_check = max_articles_per_check
                if wp_category_name:
                    w.wp_category_name = wp_category_name
                    w.wp_category_id = None  # Reset to resolve fresh
                w.next_check_at = (datetime.now() + timedelta(hours=interval_hours)).isoformat()
                self._save_watches()
                return w

        watch = WatchConfig({
            "url": url,
            "interval_hours": interval_hours,
            "target_site": target_site,
            "instruction": instruction,
            "telegram_chat_id": telegram_chat_id,
            "telegram_token": telegram_token,
            "max_articles_per_check": max_articles_per_check,
            "url_pattern": url_pattern,
            "wp_category_name": wp_category_name,
            "next_check_at": (datetime.now() + timedelta(minutes=1)).isoformat(),  # First check soon
        })
        self._watches[watch.id] = watch
        self._save_watches()
        logger.info(f"Added watch: {url} (every {interval_hours}h)")
        return watch

    def remove_watch(self, watch_id: str) -> bool:
        """Remove a watch by ID."""
        if watch_id in self._watches:
            del self._watches[watch_id]
            self._save_watches()
            return True
        return False

    def remove_watch_by_url(self, url: str) -> bool:
        """Remove a watch by URL."""
        to_remove = [wid for wid, w in self._watches.items() if w.url == url]
        for wid in to_remove:
            del self._watches[wid]
        if to_remove:
            self._save_watches()
        return bool(to_remove)

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

    def get_watch(self, watch_id: str) -> Optional[WatchConfig]:
        return self._watches.get(watch_id)

    # ── Background Scheduler ─────────────────────────────────

    def start_scheduler(self):
        """Start the background scheduler loop."""
        if self._running:
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("PageWatcher scheduler started")

    def stop_scheduler(self):
        """Stop the background scheduler."""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
        logger.info("PageWatcher scheduler stopped")

    async def _scheduler_loop(self):
        """Main scheduler loop — checks watches every 60 seconds."""
        logger.info("PageWatcher scheduler loop running...")
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
                        logger.info(f"⏰ Scheduler triggered check for: {watch.url}")
                        try:
                            await self.check_watch(watch.id)
                        except Exception as e:
                            logger.error(f"Error checking watch {watch.url}: {e}")
                            self._append_log(watch.id, {
                                "type": "error",
                                "message": f"Check failed: {str(e)[:200]}"
                            })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")

            # Check every 60 seconds
            await asyncio.sleep(60)

    # ── Core Check Logic ─────────────────────────────────────

    async def check_watch(self, watch_id: str) -> dict:
        """Check a watch for new articles. Returns result summary."""
        import httpx

        watch = self._watches.get(watch_id)
        if not watch:
            return {"error": "Watch not found"}

        watch.last_checked_at = datetime.now().isoformat()
        watch.stats["total_checked"] = watch.stats.get("total_checked", 0) + 1

        result = {
            "watch_id": watch_id,
            "url": watch.url,
            "new_articles": [],
            "published": [],
            "errors": [],
        }

        try:
            # Step 1: Crawl the page to get all links
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{TUBECLI_BASE_URL}/api/v1/web_crawler/scrape",
                    json={"url": watch.url, "max_depth": 0, "download_images": False}
                )
                if resp.status_code != 200:
                    raise Exception(f"Scrape failed: HTTP {resp.status_code}")

                scrape_data = resp.json()
                pages = scrape_data.get("data", scrape_data) if isinstance(scrape_data, dict) else scrape_data

                if not isinstance(pages, list) or len(pages) == 0:
                    raise Exception("No content found")

                page = pages[0]
                all_links = page.get("links", [])

            # Step 2: Filter article links
            article_links = self._filter_article_links(all_links, watch)

            if not watch.is_initialized:
                # First run: snapshot current articles, don't process them
                watch.processed_urls = article_links[:100]  # Keep last 100
                watch.is_initialized = True
                watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
                self._save_watches()

                msg = f"📸 Khởi tạo lần đầu: ghi nhận {len(article_links)} bài hiện tại. Sẽ phát hiện bài MỚI từ lần check sau."
                self._append_log(watch_id, {
                    "type": "init",
                    "message": msg,
                    "articles_snapshot": len(article_links),
                })
                result["message"] = msg

                # Notify via Telegram
                if watch.telegram_chat_id and watch.telegram_token:
                    await self._notify_telegram(
                        watch.telegram_chat_id,
                        watch.telegram_token,
                        f"📡 **Watcher đã khởi tạo**\n"
                        f"🔗 {watch.url}\n"
                        f"📸 Ghi nhận {len(article_links)} bài hiện tại\n"
                        f"⏰ Kiểm tra tiếp lúc: {watch.next_check_at[:16].replace('T', ' ')}"
                    )
                return result

            # Step 3: Find new articles (not in processed_urls)
            new_articles = [link for link in article_links if link not in watch.processed_urls]

            if not new_articles:
                watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
                self._save_watches()
                self._append_log(watch_id, {
                    "type": "check",
                    "message": f"Không có bài mới. Kiểm tra {len(article_links)} link."
                })
                result["message"] = "Không có bài mới."
                return result

            # Step 4: Process new articles (limit by max_articles_per_check)
            articles_to_process = new_articles[:watch.max_articles_per_check]
            result["new_articles"] = articles_to_process

            logger.info(f"🆕 Found {len(new_articles)} new articles for {watch.url}, processing {len(articles_to_process)}")

            for article_url in articles_to_process:
                try:
                    pub_result = await self._process_article(article_url, watch)
                    if pub_result.get("success"):
                        result["published"].append({
                            "url": article_url,
                            "post_url": pub_result.get("post_url", ""),
                            "title": pub_result.get("title", ""),
                        })
                        watch.stats["total_published"] = watch.stats.get("total_published", 0) + 1
                        watch.stats["last_published_url"] = pub_result.get("post_url", article_url)
                    else:
                        result["errors"].append({
                            "url": article_url,
                            "error": pub_result.get("error", "Unknown error"),
                        })
                except Exception as e:
                    result["errors"].append({
                        "url": article_url,
                        "error": str(e)[:200],
                    })

                # Mark as processed regardless of success/failure to avoid retry spam
                watch.processed_urls.append(article_url)

                # Small delay between articles
                await asyncio.sleep(3)

            # Keep processed_urls manageable (last 500)
            if len(watch.processed_urls) > 500:
                watch.processed_urls = watch.processed_urls[-500:]

            # Schedule next check
            watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
            self._save_watches()

            # Log result
            self._append_log(watch_id, {
                "type": "check_complete",
                "message": f"Tìm thấy {len(new_articles)} bài mới, xử lý {len(articles_to_process)}, đăng thành công {len(result['published'])}",
                "new_count": len(new_articles),
                "published_count": len(result["published"]),
                "error_count": len(result["errors"]),
            })

            # Notify via Telegram
            if watch.telegram_chat_id and watch.telegram_token:
                await self._send_check_notification(watch, result)

            return result

        except Exception as e:
            watch.next_check_at = (datetime.now() + timedelta(hours=watch.interval_hours)).isoformat()
            watch.status = "active"  # Keep active, don't stop on transient errors
            self._save_watches()
            self._append_log(watch_id, {
                "type": "error",
                "message": f"Check lỗi: {str(e)[:300]}"
            })
            raise

    # ── Article Detection ────────────────────────────────────

    def _filter_article_links(self, all_links: list, watch: WatchConfig) -> list:
        """Filter links to find article URLs using exclusion-based approach.
        
        Strategy: on a category/section page, MOST links are articles.
        Instead of trying to match specific patterns, we EXCLUDE obvious non-articles.
        This ensures compatibility across VNExpress, Kenh14, WordPress, and any site.
        """
        parsed_base = urlparse(watch.url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")
        base_path = parsed_base.path.rstrip("/")

        article_links = []
        for link in all_links:
            # Strip query string and fragment for cleaner comparison
            clean_link = link.split("?")[0].split("#")[0]

            parsed = urlparse(clean_link)
            link_domain = parsed.netloc.lower().replace("www.", "")

            # ── RULE 1: Must be same domain ──
            if link_domain != base_domain:
                continue

            path = parsed.path.rstrip("/")

            # ── RULE 2: Skip empty / root ──
            if not path or path == "/":
                continue

            # ── RULE 3: Skip if link IS the watched URL itself ──
            if path == base_path:
                continue

            # ── RULE 4: Skip static assets & media ──
            lower_path = path.lower()
            asset_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',
                          '.css', '.js', '.ico', '.pdf', '.zip', '.mp4', '.mp3')
            if any(lower_path.endswith(ext) for ext in asset_exts):
                continue

            # ── RULE 5: Skip obvious navigation / utility paths ──
            skip_exact = {
                "/login", "/register", "/signup", "/contact", "/about",
                "/about-us", "/privacy", "/terms", "/policy", "/help",
                "/faq", "/search", "/sitemap", "/rss", "/feed",
                "/wp-admin", "/wp-login", "/admin", "/cart", "/checkout",
            }
            if lower_path in skip_exact:
                continue

            skip_prefixes = (
                "/tag/", "/tags/", "/page/", "/author/", "/user/",
                "/wp-admin/", "/wp-content/", "/wp-includes/",
                "/api/", "/ajax/", "/cdn/", "/static/",
                "/share/", "/print/",
            )
            if any(lower_path.startswith(p) for p in skip_prefixes):
                continue

            # ── RULE 6: Custom URL pattern filter (if provided by user) ──
            if watch.url_pattern:
                try:
                    if not re.search(watch.url_pattern, clean_link):
                        continue
                except Exception:
                    pass

            # ── RULE 7: Article slug detection ──
            # An article URL should have a "slug" — a meaningful path segment
            # containing hyphens, digits, or a file extension like .html/.chn/.htm
            path_parts = [p for p in path.split("/") if p]
            if not path_parts:
                continue

            last_segment = path_parts[-1]

            # Check if the last segment looks like an article slug:
            # - Contains hyphens (slug pattern: "ten-bai-viet-123456")
            # - Contains dots (file pattern: "bai-viet.html", "bai-viet.chn")
            # - Is a long numeric ID (e.g., "12345678")
            # - Has enough length to be meaningful content (not just "news" or "home")
            has_slug_chars = "-" in last_segment or "." in last_segment
            is_long_id = last_segment.isdigit() and len(last_segment) >= 5
            is_meaningful_path = len(last_segment) > 8  # Longer than short nav words

            # If path has multiple directories with a slug-like last part
            if len(path_parts) >= 2 and (has_slug_chars or is_long_id):
                article_links.append(clean_link)
            # Single-level path but clearly an article (has slug with hyphens or extension)
            elif len(path_parts) == 1 and has_slug_chars and is_meaningful_path:
                article_links.append(clean_link)
            # Multi-level path with a meaningful last segment
            elif len(path_parts) >= 2 and is_meaningful_path:
                article_links.append(clean_link)
            # Catch-all: if path is long enough and has content-like structure
            elif len(path) > 20 and "-" in path:
                article_links.append(clean_link)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for link in article_links:
            if link not in seen:
                seen.add(link)
                unique.append(link)

        logger.info(f"Filtered {len(unique)} article links from {len(all_links)} total links (domain: {base_domain})")
        return unique

    # ── Pipeline Execution ───────────────────────────────────

    async def _process_article(self, article_url: str, watch: WatchConfig) -> dict:
        """Run the crawl → AI rewrite → publish pipeline for a single article.
        Includes thumbnail upload and category assignment."""
        import httpx

        logger.info(f"📰 Processing article: {article_url}")

        async with httpx.AsyncClient(timeout=120) as client:
            # Step 1: Scrape the article
            resp = await client.post(
                f"{TUBECLI_BASE_URL}/api/v1/web_crawler/scrape",
                json={"url": article_url, "max_depth": 0, "download_images": False}
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Scrape failed: HTTP {resp.status_code}"}

            scrape_data = resp.json()
            pages = scrape_data.get("data", scrape_data) if isinstance(scrape_data, dict) else scrape_data
            if not isinstance(pages, list) or len(pages) == 0:
                return {"success": False, "error": "No content scraped"}

            page = pages[0]
            title = page.get("title", "Untitled")
            content = page.get("content", "")
            images = page.get("images", [])

            if not content or len(content) < 50:
                return {"success": False, "error": "Content too short"}

            # Extract thumbnail URL (first image from article)
            thumbnail_url = None
            if images:
                # images can be list of strings or list of dicts
                first_img = images[0]
                if isinstance(first_img, dict):
                    thumbnail_url = first_img.get("url") or first_img.get("src", "")
                elif isinstance(first_img, str):
                    thumbnail_url = first_img
                # Validate it's a real image URL
                if thumbnail_url and not thumbnail_url.startswith("http"):
                    thumbnail_url = None

            # Step 2: AI Rewrite
            provider, model = self._get_default_ai_model()

            rewrite_resp = await client.post(
                f"{TUBECLI_BASE_URL}/api/v1/web_crawler/rewrite",
                json={
                    "title": title,
                    "content": content,
                    "instruction": watch.instruction,
                    "provider": provider,
                    "model": model,
                },
                timeout=120
            )

            if rewrite_resp.status_code != 200:
                return {"success": False, "error": f"AI rewrite failed: HTTP {rewrite_resp.status_code}"}

            rewrite_data = rewrite_resp.json()
            if not rewrite_data.get("success"):
                return {"success": False, "error": rewrite_data.get("detail", "AI error")}

            new_title = rewrite_data.get("title", title)
            new_content = rewrite_data.get("content", content)

            # Step 3: Publish to WordPress (with thumbnail + category)
            wp_site = self._find_wp_site(watch.target_site)
            if not wp_site:
                return {"success": False, "error": f"WordPress site '{watch.target_site}' not found"}

            # Convert [IMAGE: url] to HTML
            html_content = new_content.replace("\n", "<br>")
            html_content = re.sub(
                r'\[IMAGE:\s*(https?://[^\]]+)\]',
                r'<br><img src="\1" style="max-width:100%; height:auto; margin: 20px 0; border-radius: 8px;" /><br>',
                html_content
            )

            publish_payload = {
                "wp_url": wp_site["url"],
                "username": wp_site["user"],
                "app_password": wp_site["pass"],
                "title": new_title,
                "content": html_content,
                "status": "publish",
            }

            # Add thumbnail
            if thumbnail_url:
                publish_payload["thumbnail_url"] = thumbnail_url

            # Add category (use cached ID if available, else name)
            if watch.wp_category_id:
                publish_payload["category_id"] = watch.wp_category_id
            elif watch.wp_category_name:
                publish_payload["category_name"] = watch.wp_category_name

            publish_resp = await client.post(
                f"{TUBECLI_BASE_URL}/api/v1/web_crawler/publish_wp",
                json=publish_payload,
                timeout=60
            )

            if publish_resp.status_code == 200:
                pub_data = publish_resp.json()
                if pub_data.get("success"):
                    # Cache the resolved category_id for next time
                    resolved_cat_id = pub_data.get("category_id")
                    if resolved_cat_id and not watch.wp_category_id:
                        watch.wp_category_id = resolved_cat_id
                        self._save_watches()

                    logger.info(f"✅ Published: {new_title} → {pub_data.get('post_url', '')}")
                    return {
                        "success": True,
                        "title": new_title,
                        "post_url": pub_data.get("post_url", ""),
                        "post_id": pub_data.get("post_id"),
                        "featured_media_id": pub_data.get("featured_media_id"),
                        "category_id": resolved_cat_id,
                    }
                return {"success": False, "error": pub_data.get("detail", "Publish failed")}
            else:
                return {"success": False, "error": f"Publish failed: HTTP {publish_resp.status_code}"}

    # ── Telegram Notification ────────────────────────────────

    async def _notify_telegram(self, chat_id: int, token: str, message: str):
        """Send a Telegram notification."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    }
                )
        except Exception as e:
            logger.warning(f"Telegram notify error: {e}")
            # Retry without markdown
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": message}
                    )
            except Exception:
                pass

    async def _send_check_notification(self, watch: WatchConfig, result: dict):
        """Send a detailed notification about check results."""
        published = result.get("published", [])
        errors = result.get("errors", [])
        new_count = len(result.get("new_articles", []))

        lines = [
            f"📡 *Watcher Update*",
            f"🔗 {watch.url}",
            f"🆕 Phát hiện {new_count} bài mới!",
        ]

        if published:
            lines.append(f"✅ Đã đăng {len(published)} bài:")
            for p in published[:5]:
                title_short = (p.get("title", ""))[:50]
                lines.append(f"  📝 {title_short}")
                if p.get("post_url"):
                    lines.append(f"  🔗 {p['post_url']}")

        if errors:
            lines.append(f"❌ {len(errors)} bài lỗi")

        next_time = watch.next_check_at[:16].replace("T", " ") if watch.next_check_at else "N/A"
        lines.append(f"⏰ Kiểm tra tiếp: {next_time}")

        message = "\n".join(lines)
        await self._notify_telegram(watch.telegram_chat_id, watch.telegram_token, message)

    # ── Helpers ──────────────────────────────────────────────

    def _find_wp_site(self, keyword: str) -> Optional[dict]:
        """Find WordPress site config by keyword."""
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
        """Read default AI model from global settings."""
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
                    elif "gpt" in lower or "o1" in lower or "o3" in lower:
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


# Global singleton
page_watcher = PageWatcher()
