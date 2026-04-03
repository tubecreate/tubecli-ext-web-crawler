import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
import os
from datetime import datetime
import logging

logger = logging.getLogger('WebCrawler')

class SimpleScraper:
    @staticmethod
    async def _scrape_single_page(client, url: str) -> dict:
        try:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return None
            
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            
        # Extract meta tags
        meta_data = {}
        for meta_tag in soup.find_all('meta'):
            name = meta_tag.get('name')
            prop = meta_tag.get('property')
            content = meta_tag.get('content')
            
            if name and content:
                meta_data[name] = content
            elif prop and content:
                meta_data[prop] = content
                
        if title:
            meta_data["title"] = title
            
        # --- NEW: Extract Content ---
        # 1. Clean up noisy tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
            tag.decompose()
            
        # 2. Find main content container
        main_container = soup.find('article') or soup.find('main') or soup.find('div', class_='content') or soup.body
        content_text = ""
        if main_container:
            paragraphs = []
            for elem in main_container.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li', 'img', 'figcaption']):
                if elem.name == 'img':
                    src = elem.get('data-src') or elem.get('data-original') or elem.get('src')
                    if src:
                        src = src.strip()
                        if src and not src.startswith('data:image'):
                            absolute_url = urljoin(url, src)
                            # ignore small icons
                            w = elem.get('width')
                            h = elem.get('height')
                            try:
                                if w and h and (int(w) < 20 or int(h) < 20):
                                    continue
                            except ValueError:
                                pass
                            paragraphs.append(f"\n[IMAGE: {absolute_url}]\n")
                else:
                    text = elem.get_text(separator=' ', strip=True)
                    if text and len(text) > 10:
                        paragraphs.append(text)
            content_text = "\n\n".join(paragraphs)
            
        # Extract basic text for description if missing
        if "description" not in meta_data and content_text:
            meta_data["description"] = content_text[:197] + "..." if len(content_text) > 200 else content_text

        # --- NEW: Extract Images ---
        images = []
        base_url = url
        for img_tag in soup.find_all('img'):
            # Fetch lazy-loaded fields first
            src = img_tag.get('data-src') or img_tag.get('data-original') or img_tag.get('src')
            if not src:
                continue
            src = src.strip()
            if not src or src.startswith('data:image'):
                continue
            
            absolute_url = urljoin(base_url, src)
            
            # Simple check to ignore tiny tracking icons (only if width/height explicitly small)
            w = img_tag.get('width')
            h = img_tag.get('height')
            try:
                if w and h and (int(w) < 20 or int(h) < 20):
                    continue
            except ValueError:
                pass
                
            if absolute_url not in images:
                images.append(absolute_url)
                
        # Fallback to OG Image if no article images found
        if not images and meta_data.get('og:image'):
            img_url = meta_data.get('og:image')
            if img_url.startswith('http'):
                images.append(img_url)

        # Extract links
        links = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag.get('href')
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue
            absolute_url = urljoin(base_url, href)
            if absolute_url.startswith(('http://', 'https://')):
                links.add(absolute_url)
            
        return {
            "url": url,
            "title": title,
            "content": content_text,
            "images": images,
            "links": sorted(list(links)),
        }

    @staticmethod
    async def _download_images(client, urls: list, dest_folder: str) -> list:
        import asyncio
        os.makedirs(dest_folder, exist_ok=True)
        results = []
        
        async def fetch_and_save(url, index):
            try:
                ext = ".jpg"
                if "." in url.split("/")[-1]:
                    ext_cand = "." + url.split("/")[-1].split("?")[0].split(".")[-1]
                    if ext_cand.lower() in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
                        ext = ext_cand
                        
                filename = f"img_{index:03d}{ext}"
                filepath = os.path.join(dest_folder, filename)
                
                resp = await client.get(url, timeout=10.0)
                if resp.status_code == 200:
                    with open(filepath, "wb") as f:
                        f.write(resp.content)
                    results.append({"url": url, "local_path": filepath})
            except Exception as e:
                logger.warning(f"Failed to download image {url}: {e}")
                results.append({"url": url, "local_path": None})

        tasks = [fetch_and_save(u, i) for i, u in enumerate(urls)]
        if tasks:
            await asyncio.gather(*tasks)
            
        return results

    @staticmethod
    async def scrape(base_url: str, proxy: str = None, max_depth: int = 0, max_pages: int = 20, download_images: bool = False, data_dir: str = None) -> list:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        visited = set()
        results = []
        queue = [(base_url, 0)]  # (url, depth)
        
        async with httpx.AsyncClient(proxy=proxy, follow_redirects=True, verify=False, headers=headers) as client:
            while queue and len(visited) < max_pages:
                current_url, current_depth = queue.pop(0)
                
                if current_url in visited:
                    continue
                    
                visited.add(current_url)
                logger.info(f"Crawling: {current_url} (depth={current_depth})")
                
                page_data = await SimpleScraper._scrape_single_page(client, current_url)
                if page_data:
                    results.append(page_data)
                    
                    if current_depth < max_depth:
                        for next_url in page_data["links"]:
                            if next_url not in visited:
                                queue.append((next_url, current_depth + 1))
            
            # Download images if requested
            if download_images and data_dir:
                domain = urlparse(base_url).netloc.replace('www.', '').replace(':', '_')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                for i, page_data in enumerate(results):
                    urls = page_data.get("images", [])
                    if urls:
                        img_folder = os.path.join(data_dir, "web_crawler_exports", "images", f"{domain}_{timestamp}", f"page_{i}")
                        downloaded = await SimpleScraper._download_images(client, urls, img_folder)
                        page_data["images"] = downloaded
            else:
                # Convert list of strings to list of dicts consistent format
                for page_data in results:
                    page_data["images"] = [{"url": u, "local_path": None} for u in page_data.get("images", [])]
                                
        return results

    @staticmethod
    def save_output(data: list, data_dir: str, url: str) -> str:
        domain = urlparse(url).netloc.replace('www.', '').replace(':', '_')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"crawl_{domain}_{timestamp}.json"
        
        export_dir = os.path.join(data_dir, "web_crawler_exports")
        os.makedirs(export_dir, exist_ok=True)
        
        file_path = os.path.join(export_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        return file_path
