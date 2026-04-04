"""
Web Crawler Extension — Trích xuất dữ liệu, metadata và liên kết từ website.
Bao gồm Page Watcher — theo dõi trang tự động và đăng bài mới lên WordPress.
"""
import asyncio
import logging
import os
import sys
import json

try:
    from tubecli.core.extension_manager import Extension
    from tubecli.config import DATA_DIR
except ImportError:
    from zhiying.core.extension_manager import Extension
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")

logger = logging.getLogger("WebCrawlerExtension")

TUBECLI_BASE_URL = os.environ.get("TUBECLI_BASE_URL", "http://localhost:5295")
WP_SITES_FILE = os.path.join(str(DATA_DIR), "wp_sites.json")


class WebCrawlerExtension(Extension):
    name = "web_crawler"
    description = "Trích xuất title, metadata và links từ website bất kỳ. Theo dõi trang tự động."
    version = "1.1.0"
    enabled_by_default = True

    def setup(self):
        logger.info("Web Crawler Extension loaded")

    def on_enable(self):
        """Start the watcher scheduler when extension is enabled."""
        try:
            from watcher import page_watcher
            # Check if there's a running event loop
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context — schedule scheduler start
                loop.call_soon(lambda: asyncio.create_task(self._start_watcher_async()))
            except RuntimeError:
                # No running loop — scheduler will start when server runs
                logger.info("PageWatcher scheduler will start when server loop is ready")
        except Exception as e:
            logger.warning(f"Could not start PageWatcher scheduler on enable: {e}")

    async def _start_watcher_async(self):
        """Start the watcher scheduler asynchronously."""
        try:
            from watcher import page_watcher
            page_watcher.start_scheduler()
            logger.info("✅ PageWatcher scheduler started from on_enable")
        except Exception as e:
            logger.error(f"Failed to start PageWatcher scheduler: {e}")

    def on_disable(self):
        """Stop the watcher scheduler when extension is disabled."""
        try:
            from watcher import page_watcher
            page_watcher.stop_scheduler()
        except Exception:
            pass

    def get_routes(self):
        try:
            import crawler_routes
            return crawler_routes.router
        except Exception as e:
            logger.error(f"Failed to load Web Crawler routes: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_telegram_actions(self):
        """Register Telegram action handlers for this extension."""
        return {
            "crawl_website": self._action_crawl_website,
            "crawl_and_publish": self._action_crawl_and_publish,
            "add_wp_site": self._action_add_wp_site,
            # Watcher actions
            "watch_page": self._action_watch_page,
            "unwatch_page": self._action_unwatch_page,
            "update_watch": self._action_update_watch,
            "list_watches": self._action_list_watches,
        }

    def get_skill_md(self):
        """Return base SKILL.md (crawl only)."""
        return self._read_skill_file("SKILL.md")

    def get_extra_skill_mds(self):
        """Return additional SKILL files for the watcher feature."""
        content = self._read_skill_file("SKILL_WATCHER.md")
        if content:
            return [{"extension": "web_crawler_watcher", "version": self.version, "skill_md": content}]
        return []

    def _read_skill_file(self, filename: str):
        """Read a skill file from the extension directory."""
        if self.extension_dir:
            path = os.path.join(self.extension_dir, filename)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    pass
        return None

    # ── Telegram Action Handlers ─────────────────────────────

    async def _action_crawl_website(self, action_data: dict, context: dict) -> str:
        """Cào dữ liệu từ URL và trả kết quả tóm tắt."""
        import httpx
        url = action_data.get("url", "")
        if not url:
            return "❌ Thiếu URL cần cào."

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{TUBECLI_BASE_URL}/api/v1/web_crawler/scrape",
                    json={"url": url, "max_depth": 0, "download_images": False}
                )
                if resp.status_code != 200:
                    return f"❌ Lỗi cào dữ liệu: {resp.text[:300]}"
                data = resp.json()
                pages = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(pages, list) and len(pages) > 0:
                    page = pages[0]
                    title = page.get("title", "N/A")
                    content = page.get("content", "")[:500]
                    img_count = len(page.get("images", []))
                    return (
                        f"✅ **Cào thành công!**\n\n"
                        f"📰 **{title}**\n"
                        f"🖼️ {img_count} ảnh\n\n"
                        f"📝 Nội dung (trích):\n{content}..."
                    )
                return "⚠️ Không tìm thấy nội dung từ URL."
        except Exception as e:
            return f"❌ Lỗi: {str(e)[:300]}"

    async def _action_crawl_and_publish(self, action_data: dict, context: dict) -> str:
        """Pipeline tự động: Cào → AI Biên tập → Đăng WordPress."""
        import httpx

        url = action_data.get("url", "")
        instruction = action_data.get("instruction", "dịch sang tiếng anh")
        target_site_keyword = action_data.get("target_site", "")

        if not url:
            return "❌ Thiếu URL bài viết cần xử lý."

        # ── Step 0: Resolve WordPress site ──
        wp_site = self._find_wp_site(target_site_keyword)
        if not wp_site:
            return (
                f"❌ Tôi chưa có cấu hình đăng nhập của website WordPress `{target_site_keyword}`.\n\n"
                f"Vui lòng gửi cho tôi thông tin của website này (URL, tài khoản và mật khẩu ứng dụng) để tôi lưu lại và dùng cho các lần sau nhé!"
            )
        site_name = wp_site.get("name", wp_site.get("url", ""))

        # ── Step 1: Scrape ──
        status_parts = [f"🔄 **Bước 1/3**: Đang cào bài viết từ `{url}`..."]

        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{TUBECLI_BASE_URL}/api/v1/web_crawler/scrape",
                    json={"url": url, "max_depth": 0, "download_images": False}
                )
                if resp.status_code != 200:
                    return f"❌ Bước 1 thất bại — lỗi cào dữ liệu: {resp.text[:200]}"

                scrape_data = resp.json()
                pages = scrape_data.get("data", scrape_data) if isinstance(scrape_data, dict) else scrape_data
                if not isinstance(pages, list) or len(pages) == 0:
                    return "❌ Bước 1 thất bại — không tìm thấy nội dung từ URL."

                page = pages[0]
                original_title = page.get("title", "Untitled")
                original_content = page.get("content", "")

                if not original_content or len(original_content) < 50:
                    return "❌ Bước 1 thất bại — nội dung bài viết quá ngắn hoặc trống."

                status_parts.append(f"✅ Cào thành công: **{original_title}** ({len(original_content)} ký tự)")

                # ── Step 2: AI Rewrite ──
                status_parts.append(f"🔄 **Bước 2/3**: AI đang xử lý: _{instruction}_...")

                # Detect AI model from global settings
                provider, model = self._get_default_ai_model()

                rewrite_resp = await client.post(
                    f"{TUBECLI_BASE_URL}/api/v1/web_crawler/rewrite",
                    json={
                        "title": original_title,
                        "content": original_content,
                        "instruction": instruction,
                        "provider": provider,
                        "model": model,
                    },
                    timeout=120
                )

                if rewrite_resp.status_code != 200:
                    return "\n".join(status_parts) + f"\n❌ Bước 2 thất bại — AI xử lý lỗi: {rewrite_resp.text[:200]}"

                rewrite_data = rewrite_resp.json()
                if not rewrite_data.get("success"):
                    return "\n".join(status_parts) + f"\n❌ Bước 2 thất bại — {rewrite_data.get('detail', 'Lỗi AI')}"

                new_title = rewrite_data.get("title", original_title)
                new_content = rewrite_data.get("content", original_content)
                status_parts.append(f"✅ AI hoàn thành: **{new_title}**")

                # ── Step 3: Publish to WordPress ──
                status_parts.append(f"🔄 **Bước 3/3**: Đang đăng bài lên **{site_name}**...")

                # Convert [IMAGE: url] tags to HTML
                import re
                html_content = new_content.replace("\n", "<br>")
                html_content = re.sub(
                    r'\[IMAGE:\s*(https?://[^\]]+)\]',
                    r'<br><img src="\1" style="max-width:100%; height:auto; margin: 20px 0; border-radius: 8px;" /><br>',
                    html_content
                )

                publish_resp = await client.post(
                    f"{TUBECLI_BASE_URL}/api/v1/web_crawler/publish_wp",
                    json={
                        "wp_url": wp_site["url"],
                        "username": wp_site["user"],
                        "app_password": wp_site["pass"],
                        "title": new_title,
                        "content": html_content,
                        "status": "publish",
                    },
                    timeout=30
                )

                if publish_resp.status_code == 200:
                    pub_data = publish_resp.json()
                    if pub_data.get("success"):
                        post_url = pub_data.get("post_url", "")
                        status_parts.append(f"✅ **Đăng bài thành công!**")
                        if post_url:
                            status_parts.append(f"🔗 {post_url}")
                        return "\n".join(status_parts)
                    else:
                        return "\n".join(status_parts) + f"\n❌ Bước 3 thất bại — WordPress: {pub_data.get('detail', 'Lỗi')}"
                else:
                    return "\n".join(status_parts) + f"\n❌ Bước 3 thất bại — HTTP {publish_resp.status_code}"

        except Exception as e:
            return "\n".join(status_parts) + f"\n❌ Pipeline lỗi: {str(e)[:300]}"

    async def _action_add_wp_site(self, action_data: dict, context: dict) -> str:
        """Thêm và lưu cấu hình trang WordPress từ Chatbot."""
        import httpx
        url = action_data.get("url", "")
        user = action_data.get("user", "")
        password = action_data.get("pass", "")
        name = action_data.get("name", "")

        if not url or not user or not password:
            return "❌ Thiếu thông tin! Phải có URL, username và app password."

        import uuid
        site_id = str(uuid.uuid4())

        # Determine name if empty
        if not name:
            import urllib.parse
            parsed = urllib.parse.urlparse(url if url.startswith("http") else "https://" + url)
            name = parsed.netloc

        payload = {
            "id": site_id,
            "name": name,
            "url": url,
            "user": user,
            "pass": password
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(
                    f"{TUBECLI_BASE_URL}/api/v1/web_crawler/wp_sites",
                    json=payload
                )
                if res.status_code == 200:
                    return f"✅ Đã lưu cấu hình WordPress thành công cho website: **{name}**\nBây giờ bạn có thể yêu cầu tôi crawl & publish!"
                else:
                    return f"❌ Lỗi khi lưu: HTTP {res.status_code}"
        except Exception as e:
            return f"❌ Lỗi kết nối tới backend nội bộ: {e}"

    # ── Watcher Telegram Actions ─────────────────────────────

    async def _action_watch_page(self, action_data: dict, context: dict) -> str:
        """Bắt đầu theo dõi một trang web."""
        from watcher import page_watcher

        url = action_data.get("url", "")
        if not url:
            return "❌ Thiếu URL cần theo dõi."

        if not url.startswith("http"):
            url = "https://" + url

        interval = action_data.get("interval_hours", 6)
        target_site = action_data.get("target_site", "")
        instruction = action_data.get("instruction", "dịch sang tiếng anh")
        max_articles = action_data.get("max_articles_per_check", 5)
        category_name = action_data.get("category_name") or action_data.get("category", "")

        # Validate target_site exists
        wp_site = self._find_wp_site(target_site)
        if not wp_site:
            return (
                f"❌ Chưa có cấu hình WordPress cho `{target_site}`.\n\n"
                f"Hãy gửi thông tin đăng nhập WordPress (URL, username, app password) để tôi lưu trước nhé!"
            )

        # Get telegram context for notifications
        telegram_chat_id = context.get("chat_id")
        telegram_token = context.get("token")

        # Resolve token from global settings if not in context
        if not telegram_token:
            telegram_token = self._get_global_telegram_token()

        watch = page_watcher.add_watch(
            url=url,
            interval_hours=interval,
            target_site=target_site,
            instruction=instruction,
            telegram_chat_id=telegram_chat_id,
            telegram_token=telegram_token,
            max_articles_per_check=max_articles,
            wp_category_name=category_name if category_name else None,
        )

        # Ensure scheduler is running
        page_watcher.start_scheduler()

        next_time = watch.next_check_at[:16].replace("T", " ") if watch.next_check_at else "sớm"
        site_name = wp_site.get("name", target_site)

        return (
            f"✅ **Đã thiết lập theo dõi tự động!**\n\n"
            f"📡 **URL:** {url}\n"
            f"⏰ **Kiểm tra:** mỗi {interval} tiếng\n"
            f"🎯 **Đăng lên:** {site_name}\n"
            f"📝 **Xử lý:** {instruction}\n"
            f"📊 **Tối đa:** {max_articles} bài/lần\n"
            f"🔄 **Lần kiểm tra đầu:** {next_time}\n\n"
            f"💡 Lần đầu sẽ ghi nhận danh sách bài hiện tại. Từ lần sau, mỗi bài MỚI sẽ tự động được xử lý và đăng!"
        )

    async def _action_unwatch_page(self, action_data: dict, context: dict) -> str:
        """Dừng theo dõi một trang web."""
        from watcher import page_watcher

        url = action_data.get("url", "")
        watch_id = action_data.get("watch_id", "")

        if watch_id:
            if page_watcher.remove_watch(watch_id):
                return f"✅ Đã dừng theo dõi (ID: {watch_id[:8]}...)"
            return f"❌ Không tìm thấy watch ID: {watch_id}"

        if url:
            if not url.startswith("http"):
                url = "https://" + url
            if page_watcher.remove_watch_by_url(url):
                return f"✅ Đã dừng theo dõi: {url}"
            # Try partial match
            watches = page_watcher.list_watches()
            for w in watches:
                if url.lower() in w["url"].lower() or w["url"].lower() in url.lower():
                    page_watcher.remove_watch(w["id"])
                    return f"✅ Đã dừng theo dõi: {w['url']}"
            return f"❌ Không tìm thấy trang đang theo dõi: {url}"

        return "❌ Thiếu URL hoặc watch_id."

    async def _action_list_watches(self, action_data: dict, context: dict) -> str:
        """Liệt kê các trang đang theo dõi."""
        from watcher import page_watcher

        watches = page_watcher.list_watches()
        if not watches:
            return "📡 Chưa có trang nào đang được theo dõi.\n\n💡 Bạn có thể nói: \"Theo dõi https://vnexpress.net/the-gioi mỗi 6 tiếng, dịch sang tiếng anh và đăng lên [tên website]\""

        lines = [f"📡 **Đang theo dõi {len(watches)} trang:**\n"]
        for i, w in enumerate(watches, 1):
            status_icon = "🟢" if w["status"] == "active" else "⏸️" if w["status"] == "paused" else "🔴"
            next_time = w.get("next_check_at", "")[:16].replace("T", " ") if w.get("next_check_at") else "N/A"
            published = w.get("stats", {}).get("total_published", 0)

            lines.append(
                f"{status_icon} **{i}.** {w['url']}\n"
                f"   ⏰ Mỗi {w['interval_hours']}h → 🎯 {w['target_site']}\n"
                f"   📝 {w['instruction']}\n"
                f"   📊 Đã đăng: {published} bài | Tiếp: {next_time}"
            )

        return "\n".join(lines)

    async def _action_update_watch(self, action_data: dict, context: dict) -> str:
        """Cập nhật cấu hình một watch đang chạy."""
        from watcher import page_watcher

        url = action_data.get("url", "")
        watch_id = action_data.get("watch_id", "")

        # Find the watch
        watch = None
        if watch_id:
            watch = page_watcher.get_watch(watch_id)
        elif url:
            if not url.startswith("http"):
                url = "https://" + url
            # Find by URL
            for w in page_watcher.list_watches():
                if url.lower() in w["url"].lower() or w["url"].lower() in url.lower():
                    watch = page_watcher.get_watch(w["id"])
                    break

        if not watch:
            return f"❌ Không tìm thấy watch cho `{url or watch_id}`.\n\n💡 Gửi \"danh sách theo dõi\" để xem các watch hiện tại."

        # Apply updates
        changes = []
        if "instruction" in action_data:
            watch.instruction = action_data["instruction"]
            changes.append(f"📝 Instruction → {watch.instruction}")
        if "interval_hours" in action_data:
            watch.interval_hours = action_data["interval_hours"]
            changes.append(f"⏰ Interval → mỗi {watch.interval_hours}h")
        if "target_site" in action_data:
            watch.target_site = action_data["target_site"]
            changes.append(f"🎯 Target → {watch.target_site}")
        if "max_articles_per_check" in action_data:
            watch.max_articles_per_check = action_data["max_articles_per_check"]
            changes.append(f"📊 Max bài/check → {watch.max_articles_per_check}")
        if action_data.get("category_name") or action_data.get("category"):
            cat = action_data.get("category_name") or action_data.get("category")
            watch.wp_category_name = cat
            watch.wp_category_id = None  # Reset to resolve fresh
            changes.append(f"📂 Category → {cat}")

        if not changes:
            return "❌ Không có gì để cập nhật. Hãy chỉ rõ muốn thay đổi gì (instruction, interval, category...)." 

        page_watcher._save_watches()

        return (
            f"✅ Đã cập nhật watch:\n"
            f"🔗 {watch.url}\n\n" +
            "\n".join(changes)
        )

    # ── Helper Methods ────────────────────────────────────────

    def _find_wp_site(self, keyword: str) -> dict:
        """Tìm WP site config khớp với keyword (tên hoặc URL)."""
        if not keyword:
            # Return the first site if no keyword
            sites = self._load_wp_sites()
            return sites[0] if sites else None

        keyword_lower = keyword.lower().strip()
        sites = self._load_wp_sites()

        for s in sites:
            name = (s.get("name") or "").lower()
            url = (s.get("url") or "").lower()
            if keyword_lower in name or keyword_lower in url:
                return s
        return None

    def _load_wp_sites(self) -> list:
        """Đọc danh sách WP sites từ wp_sites.json."""
        if os.path.exists(WP_SITES_FILE):
            try:
                with open(WP_SITES_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _get_default_ai_model(self):
        """Đọc model AI mặc định từ global settings."""
        try:
            settings_file = os.path.join(str(DATA_DIR), "global_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                model_str = data.get("default_model", "")
                if model_str and "|" in model_str:
                    return model_str.split("|", 1)
                elif model_str:
                    # Auto-detect provider from model name
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
        # Fallback
        return ("gemini", "gemini-2.0-flash")

    def _get_global_telegram_token(self) -> str:
        """Get global Telegram bot token from settings."""
        try:
            settings_file = os.path.join(str(DATA_DIR), "global_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("telegram_bot_token", "")
        except Exception:
            pass
        return ""
