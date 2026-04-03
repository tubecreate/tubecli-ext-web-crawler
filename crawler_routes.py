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
    status: str = "draft" # draft or publish

@router.post("/publish_wp")
async def publish_to_wordpress(req: WPPublishRequest):
    """Đăng tự động nội dung lên cấu trúc Website chạy nền tảng WordPress"""
    import base64
    import httpx
    
    # Chuẩn hoá URL
    base_url = req.wp_url.strip().rstrip('/')
    if not base_url.startswith('http'):
        base_url = 'https://' + base_url
        
    endpoint = f"{base_url}/wp-json/wp/v2/posts"
    
    # Tạo chuỗi Auth Authentication chuẩn
    credentials = f"{req.username}:{req.app_password}"
    token = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    headers = {
        'Authorization': f'Basic {token}',
        'Content-Type': 'application/json'
    }
    
    # Format Content với xuống dòng HTML
    html_content = req.content.replace('\n', '<br>')
    
    payload = {
        'title': req.title,
        'content': html_content,
        'status': req.status
    }
    
    try:
        current_app_timeout = 30.0
        async with httpx.AsyncClient() as client:
            res = await client.post(endpoint, headers=headers, json=payload, timeout=current_app_timeout)
            
            if res.status_code in [200, 201]:
                data = res.json()
                return {
                    "success": True, 
                    "message": "Đăng tải bài thành công!", 
                    "post_url": data.get("link", ""), 
                    "post_id": data.get("id")
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
