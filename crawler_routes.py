import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger("web_crawler.routes")

router = APIRouter(prefix="/api/v1/web_crawler", tags=["WebCrawler"])

class ScrapeRequest(BaseModel):
    url: str
    proxy: Optional[str] = None
    max_depth: int = 0
    save_to_file: bool = False
    download_images: bool = False

@router.post("/scrape")
async def scrape_url(req: ScrapeRequest):
    """Scrape title, content, images and links from a website URL."""
    try:
        from crawler import SimpleScraper
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Thiếu thư viện (Missing Library): {e}. Vui lòng chạy / Please run: pip install beautifulsoup4 lxml httpx")

    if not req.url.startswith("http"):
        req.url = "https://" + req.url

    try:
        from tubecli.config import DATA_DIR
        data = await SimpleScraper.scrape(req.url, req.proxy, req.max_depth, download_images=req.download_images, data_dir=DATA_DIR)
        
        save_path = None
        if req.save_to_file and data:
            save_path = SimpleScraper.save_output(data, DATA_DIR, req.url)
            
        return {
            "success": True,
            "data": data,
            "save_path": save_path
        }
    except Exception as e:
        logger.error(f"Scrape error: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi lấy dữ liệu: {str(e)}")

import os
from fastapi.responses import FileResponse, JSONResponse

@router.get("/ui")
async def get_ui():
    """Serve the Web Crawler UI"""
    index = os.path.join(os.path.dirname(__file__), "static", "web_crawler.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"error": "UI file not found"}, status_code=404)

@router.get("/locales/{lang}")
async def get_locale(lang: str):
    """Serve localizations for the UI"""
    file_path = os.path.join(os.path.dirname(__file__), "locales", f"{lang}.json")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return JSONResponse({"error": "Locale not found"}, status_code=404)

@router.get("/ai_models")
async def get_ai_models():
    """Lấy danh sách các Provider & Model hiện có mặt trên máy."""
    models_list = []
    
    # 1. Ollama
    try:
        from tubecli.extensions.ollama_manager.extension import ollama_model_manager
        ollama_res = ollama_model_manager.list_models()
        if not ollama_res.get("error"):
            ollama_models = [m.get("name") for m in ollama_res.get("models", [])]
            if ollama_models:
                models_list.append({
                    "provider": "ollama",
                    "name": "Ollama (Offline AI)",
                    "models": ollama_models
                })
            else:
                models_list.append({
                    "provider": "ollama",
                    "name": "Ollama (Chưa tải model nào)",
                    "models": ["llama3", "mistral", "qwen", "gemma"]
                })
        else:
            models_list.append({
                "provider": "ollama",
                "name": "Ollama (Chưa bật Server)",
                "models": ["llama3", "mistral", "qwen", "phi3", "gemma"]
            })
    except Exception as e:
        logger.warning(f"Failed to fetch Ollama models: {e}")

    # 2. Cloud API
    try:
        from tubecli.extensions.cloud_api.extension import key_manager
        cloud_providers = key_manager.list_providers()
        for p in cloud_providers:
            if p.get("has_key") and p.get("models"):
                models_list.append({
                    "provider": p.get("id"),
                    "name": p.get("name"),
                    "models": p.get("models")
                })
    except Exception as e:
        logger.warning(f"Failed to fetch Cloud API providers: {e}")

    return {"success": True, "providers": models_list}

# --- WP SITES MANAGEMENT ---

def _get_wp_sites_file():
    try:
        from tubecli.config import DATA_DIR
        return os.path.join(DATA_DIR, "wp_sites.json")
    except ImportError:
        from zhiying.config import DATA_DIR
        return os.path.join(DATA_DIR, "wp_sites.json")

@router.get("/wp_sites")
async def get_wp_sites():
    """Lấy danh sách các trang WordPress đã lưu trên Backend"""
    file_path = _get_wp_sites_file()
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                import json
                data = json.load(f)
                return {"success": True, "sites": data}
        except Exception as e:
            logger.error(f"Error reading wp_sites.json: {e}")
    return {"success": True, "sites": []}

@router.post("/wp_sites")
async def save_wp_site(request: dict):
    """Thêm hoặc cập nhật một trang WordPress"""
    file_path = _get_wp_sites_file()
    sites = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                import json
                sites = json.load(f)
        except Exception:
            pass
            
    site_id = request.get("id")
    if not site_id:
        import uuid
        site_id = str(uuid.uuid4())
        request["id"] = site_id
        sites.append(request)
    else:
        # Cập nhật
        found = False
        for i, s in enumerate(sites):
            if s.get("id") == site_id:
                sites[i] = request
                found = True
                break
        if not found:
            sites.append(request)
            
    try:
        import json
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(sites, f, ensure_ascii=False, indent=2)
        return {"success": True, "site": request}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@router.delete("/wp_sites/{site_id}")
async def delete_wp_site(site_id: str):
    """Xóa một trang WordPress"""
    file_path = _get_wp_sites_file()
    if not os.path.exists(file_path):
        return {"success": True}
        
    try:
        import json
        with open(file_path, "r", encoding="utf-8") as f:
            sites = json.load(f)
            
        new_sites = [s for s in sites if s.get("id") != site_id]
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(new_sites, f, ensure_ascii=False, indent=2)
            
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

class RewriteRequest(BaseModel):
    title: str
    content: str
    instruction: str
    provider: str
    model: str

@router.post("/rewrite")
async def rewrite_content(req: RewriteRequest):
    """Sử dụng AI Core để thao tác Biên Tập Nội Dung"""
    try:
        from tubecli.extensions.cloud_api.extension import key_manager
        from tubecli.core.ai_generator import call_gemini, call_openai_compatible, call_claude, call_ollama
        
        prompt = f"""Bạn là một chuyên gia biên tập nội dung.
Yêu cầu: {req.instruction}

LƯU Ý QUAN TRỌNG TỪ NGƯỜI DÙNG: Hãy trả lời bằng văn bản cực kỳ SẠCH SẼ. Tuyệt đối KHÔNG sử dụng ký tự đánh dấu (markdown) như dấu ** (in đậm, in nghiêng) trong câu trả lời. 
TUYỆT ĐỐI BẢO TỒN VÀ GIỮ NGUYÊN CÁC THẺ [IMAGE: url] ở đúng vị trí mạch văn trong bài viết để làm hình ảnh minh họa. Không được xóa hay thay đổi url hình ảnh.

BẠN BẮT BUỘC PHẢI CHIA CÂU TRẢ LỜI LÀM 2 PHẦN THEO ĐÚNG ĐỊNH DẠNG SAU:
[TITLE]
(Viết tiêu đề mới ở đây)
[CONTENT]
(Viết nội dung mới ở đây)

---
TIÊU ĐỀ GỐC: {req.title}
NỘI DUNG GỐC:
{req.content}"""
        
        provider = req.provider.lower()
        if provider == "ollama":
            res = call_ollama(req.model, prompt)
        else:
            # Cloud API
            key = key_manager.get_active_key(provider)
            if not key:
                raise HTTPException(400, f"Chưa cấu hình API Key cho nhóm '{provider}'")
                
            if provider == "gemini":
                res = call_gemini(req.model, key, prompt)
            elif provider in ["openai", "chatgpt"]:
                res = call_openai_compatible(req.model, key, prompt)
            elif provider == "grok":
                res = call_openai_compatible(req.model, key, prompt, base_url="https://api.x.ai/v1")
            elif provider == "deepseek":
                res = call_openai_compatible(req.model, key, prompt, base_url="https://api.deepseek.com/v1")
            elif provider == "claude":
                res = call_claude(req.model, key, prompt)
            else:
                raise HTTPException(400, f"Provider '{provider}' không được hỗ trợ để rewrite.")
                
        if str(res).startswith("[ERROR]") or str(res).startswith("[QUOTA_ERROR]"):
            raise HTTPException(500, res)
            
        # Tách TITLE và CONTENT
        res_str = str(res)
        new_title = req.title # mặc định nếu AI không tuân thủ mẫu
        new_content = res_str
        
        if "[TITLE]" in res_str and "[CONTENT]" in res_str:
            parts = res_str.split("[CONTENT]")
            title_part = parts[0].replace("[TITLE]", "").strip()
            content_part = parts[1].strip()
            new_title = title_part if title_part else new_title
            new_content = content_part if content_part else new_content
            
        return {"success": True, "title": new_title, "content": new_content}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

class WPPublishRequest(BaseModel):
    wp_url: str
    username: str
    app_password: str
    title: str
    content: str
    status: str = "draft"  # draft or publish
    thumbnail_url: Optional[str] = None  # URL ảnh để set làm Featured Image
    category_name: Optional[str] = None  # Tên category (tự tạo nếu chưa có)
    category_id: Optional[int] = None    # ID category (ưu tiên nếu có)

@router.post("/publish_wp")
async def publish_to_wordpress(req: WPPublishRequest):
    """Đăng tự động nội dung lên WordPress — hỗ trợ thumbnail & category."""
    import base64
    import httpx
    
    # Chuẩn hoá URL
    base_url = req.wp_url.strip().rstrip('/')
    if not base_url.startswith('http'):
        base_url = 'https://' + base_url
    
    # Tạo chuỗi Auth
    credentials = f"{req.username}:{req.app_password}"
    token = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    headers = {
        'Authorization': f'Basic {token}',
        'Content-Type': 'application/json'
    }
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # ── Step 1: Resolve category ──
            category_id = req.category_id
            if not category_id and req.category_name:
                category_id = await _resolve_wp_category(
                    client, base_url, headers, req.category_name
                )

            # ── Step 2: Upload thumbnail ──
            featured_media_id = None
            if req.thumbnail_url:
                featured_media_id = await _upload_wp_thumbnail(
                    client, base_url, token, req.thumbnail_url, req.title
                )

            # ── Step 3: Create post ──
            html_content = req.content.replace('\n', '<br>')
            
            payload = {
                'title': req.title,
                'content': html_content,
                'status': req.status
            }
            if category_id:
                payload['categories'] = [category_id]
            if featured_media_id:
                payload['featured_media'] = featured_media_id
            
            endpoint = f"{base_url}/wp-json/wp/v2/posts"
            res = await client.post(endpoint, headers=headers, json=payload, timeout=30)
            
            if res.status_code in [200, 201]:
                data = res.json()
                return {
                    "success": True, 
                    "message": "Đăng tải bài thành công!", 
                    "post_url": data.get("link", ""), 
                    "post_id": data.get("id"),
                    "category_id": category_id,
                    "featured_media_id": featured_media_id,
                }
            else:
                error_msg = res.text
                try:
                    error_msg = res.json().get('message', res.text)
                except Exception:
                    pass
                raise HTTPException(status_code=400, detail=f"WordPress phản hồi lỗi (Code {res.status_code}): {error_msg}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Lỗi kết nối tới Website ({e}). Hãy đảm bảo URL đúng và web không cấm block REST API.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _resolve_wp_category(client, base_url: str, headers: dict, category_name: str) -> Optional[int]:
    """Tìm category theo tên, tự tạo mới nếu chưa có. Trả về category ID."""
    try:
        # Search existing categories
        search_url = f"{base_url}/wp-json/wp/v2/categories?search={category_name}&per_page=100"
        res = await client.get(search_url, headers=headers, timeout=10)
        if res.status_code == 200:
            cats = res.json()
            # Exact match (case-insensitive)
            for cat in cats:
                if cat.get("name", "").lower().strip() == category_name.lower().strip():
                    return cat["id"]
            # Partial match
            for cat in cats:
                if category_name.lower().strip() in cat.get("name", "").lower():
                    return cat["id"]

        # Not found — create new category
        create_res = await client.post(
            f"{base_url}/wp-json/wp/v2/categories",
            headers=headers,
            json={"name": category_name},
            timeout=10
        )
        if create_res.status_code in [200, 201]:
            return create_res.json().get("id")
        
        logger.warning(f"Failed to create WP category '{category_name}': {create_res.status_code}")
    except Exception as e:
        logger.warning(f"Category resolve error: {e}")
    return None


async def _upload_wp_thumbnail(client, base_url: str, auth_token: str, 
                                image_url: str, post_title: str) -> Optional[int]:
    """Download ảnh từ URL và upload lên WordPress Media Library. Trả về media ID."""
    try:
        # Download image
        img_resp = await client.get(image_url, timeout=30, follow_redirects=True)
        if img_resp.status_code != 200:
            logger.warning(f"Failed to download thumbnail: {image_url} → HTTP {img_resp.status_code}")
            return None

        img_data = img_resp.content
        if len(img_data) < 1000:  # Skip tiny/broken images
            return None

        # Detect filename & content type
        from urllib.parse import urlparse as _urlparse
        parsed = _urlparse(image_url)
        path_parts = parsed.path.split("/")
        filename = path_parts[-1] if path_parts else "thumbnail.jpg"
        # Clean filename
        filename = filename.split("?")[0]
        if "." not in filename:
            filename += ".jpg"

        content_type = img_resp.headers.get("content-type", "image/jpeg")
        if "png" in content_type:
            content_type = "image/png"
        elif "webp" in content_type:
            content_type = "image/webp"
        elif "gif" in content_type:
            content_type = "image/gif"
        else:
            content_type = "image/jpeg"

        # Upload to WP Media
        upload_headers = {
            'Authorization': f'Basic {auth_token}',
            'Content-Type': content_type,
            'Content-Disposition': f'attachment; filename="{filename}"',
        }
        
        upload_res = await client.post(
            f"{base_url}/wp-json/wp/v2/media",
            headers=upload_headers,
            content=img_data,
            timeout=30
        )

        if upload_res.status_code in [200, 201]:
            media_id = upload_res.json().get("id")
            logger.info(f"✅ Uploaded thumbnail: {filename} → media_id={media_id}")
            return media_id
        else:
            logger.warning(f"WP media upload failed: {upload_res.status_code} — {upload_res.text[:200]}")
    except Exception as e:
        logger.warning(f"Thumbnail upload error: {e}")
    return None


@router.get("/wp_categories")
async def list_wp_categories(wp_url: str, username: str, app_password: str):
    """Lấy danh sách categories từ WordPress site."""
    import base64
    import httpx
    
    base_url = wp_url.strip().rstrip('/')
    if not base_url.startswith('http'):
        base_url = 'https://' + base_url
    
    credentials = f"{username}:{app_password}"
    token = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    headers = {'Authorization': f'Basic {token}'}
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                f"{base_url}/wp-json/wp/v2/categories?per_page=100",
                headers=headers
            )
            if res.status_code == 200:
                cats = res.json()
                return {
                    "success": True,
                    "categories": [{"id": c["id"], "name": c["name"], "count": c.get("count", 0)} for c in cats]
                }
            raise HTTPException(400, detail=f"WP API error: {res.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# PAGE WATCHER ROUTES
# ══════════════════════════════════════════════════════════════

class WatchRequest(BaseModel):
    url: str
    interval_hours: float = 6
    target_site: str = ""
    instruction: str = "dịch sang tiếng anh"
    max_articles_per_check: int = 5
    url_pattern: Optional[str] = None
    wp_category_name: Optional[str] = None


@router.get("/watches")
async def list_watches():
    """Lấy danh sách các trang đang theo dõi."""
    try:
        from watcher import page_watcher
        watches = page_watcher.list_watches()
        return {"success": True, "watches": watches, "count": len(watches)}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.post("/watches")
async def create_watch(req: WatchRequest):
    """Tạo một watch mới."""
    try:
        from watcher import page_watcher

        url = req.url
        if not url.startswith("http"):
            url = "https://" + url

        watch = page_watcher.add_watch(
            url=url,
            interval_hours=req.interval_hours,
            target_site=req.target_site,
            instruction=req.instruction,
            max_articles_per_check=req.max_articles_per_check,
            url_pattern=req.url_pattern,
            wp_category_name=req.wp_category_name,
        )

        # Ensure scheduler is running
        page_watcher.start_scheduler()

        return {"success": True, "watch": watch.to_dict()}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.delete("/watches/{watch_id}")
async def delete_watch(watch_id: str):
    """Xoá một watch."""
    try:
        from watcher import page_watcher
        if page_watcher.remove_watch(watch_id):
            return {"success": True}
        raise HTTPException(404, detail="Watch not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.patch("/watches/{watch_id}/pause")
async def pause_watch(watch_id: str):
    """Tạm dừng theo dõi."""
    try:
        from watcher import page_watcher
        if page_watcher.pause_watch(watch_id):
            return {"success": True, "status": "paused"}
        raise HTTPException(404, detail="Watch not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.patch("/watches/{watch_id}/resume")
async def resume_watch(watch_id: str):
    """Tiếp tục theo dõi."""
    try:
        from watcher import page_watcher
        if page_watcher.resume_watch(watch_id):
            return {"success": True, "status": "active"}
        raise HTTPException(404, detail="Watch not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.post("/watches/{watch_id}/check_now")
async def check_now(watch_id: str):
    """Force check ngay lập tức."""
    try:
        from watcher import page_watcher
        import asyncio

        watch = page_watcher.get_watch(watch_id)
        if not watch:
            raise HTTPException(404, detail="Watch not found")

        # Run check in background to avoid HTTP timeout
        result = await asyncio.wait_for(
            page_watcher.check_watch(watch_id),
            timeout=120
        )
        return {"success": True, "result": result}
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        return {"success": False, "detail": "Check timed out (>120s), but may still be running in background."}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.get("/watches/{watch_id}/logs")
async def get_watch_logs(watch_id: str, limit: int = 50):
    """Lấy lịch sử hoạt động của một watch."""
    try:
        from watcher import page_watcher
        logs = page_watcher.get_logs(watch_id, limit=limit)
        return {"success": True, "logs": logs}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.post("/watches/{watch_id}/test_pipeline")
async def test_pipeline(watch_id: str):
    """Test pipeline trên 1 bài — chạy scrape → AI rewrite → publish để kiểm tra."""
    try:
        from watcher import page_watcher
        import httpx

        watch = page_watcher.get_watch(watch_id)
        if not watch:
            raise HTTPException(404, detail="Watch not found")

        # Get a test article URL from processed_urls or fresh scrape
        test_url = None
        if watch.processed_urls:
            test_url = watch.processed_urls[0]
        else:
            # Scrape the watch page to get a link
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"http://localhost:5295/api/v1/web_crawler/scrape",
                    json={"url": watch.url, "max_depth": 0, "download_images": False}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pages = data.get("data", data) if isinstance(data, dict) else data
                    if isinstance(pages, list) and pages:
                        links = pages[0].get("links", [])
                        filtered = watch._filter_article_links(links, watch) if hasattr(watch, '_filter_article_links') else links[:1]
                        if filtered:
                            test_url = filtered[0]

        if not test_url:
            return {"success": False, "detail": "Không tìm được bài viết nào để test."}

        # Run the pipeline on this article
        result = await page_watcher._process_article(test_url, watch)

        return {
            "success": True,
            "test_url": test_url,
            "pipeline_result": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, detail=str(e))

