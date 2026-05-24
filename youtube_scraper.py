"""
YouTube Scraper — Lấy transcript và metadata của video YouTube.
Hỗ trợ:
  1. youtube-transcript-api (không cần API key)
  2. Fallback: YouTube oEmbed API để lấy metadata

Ngôn ngữ transcript: Tự động phát hiện từ tiêu đề video.
"""
import re
import logging
import asyncio
from typing import Optional

import httpx

logger = logging.getLogger("YouTubeScraper")


def extract_video_id(url_or_id: str) -> Optional[str]:
    """Extract YouTube video ID from URL or return as-is if already an ID."""
    # Direct ID (11 chars, alphanumeric + dash/underscore)
    if re.match(r'^[A-Za-z0-9_-]{11}$', url_or_id):
        return url_or_id

    # Various YouTube URL formats
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([A-Za-z0-9_-]{11})',
        r'youtube\.com/shorts/([A-Za-z0-9_-]{11})',
    ]
    for pat in patterns:
        m = re.search(pat, url_or_id)
        if m:
            return m.group(1)
    return None


def extract_channel_id_from_url(channel_url: str) -> Optional[str]:
    """
    Try to extract a UCxxxx channel ID from URL.
    Returns None if channel_url uses @handle format (need to resolve via web).
    """
    m = re.search(r'youtube\.com/channel/(UC[A-Za-z0-9_-]+)', channel_url)
    if m:
        return m.group(1)
    return None


def detect_language_from_title(title: str) -> list:
    """
    Detect probable transcript language(s) from video title.
    Returns an ordered list of language codes to try.
    """
    title_lower = title.lower()

    # Vietnamese Unicode ranges and common words
    vn_chars = re.compile(r'[àáâãèéêìíòóôõùúýăđơư]', re.IGNORECASE)
    vn_words = {'của', 'và', 'là', 'không', 'có', 'được', 'người', 'với'}

    if vn_chars.search(title) or any(w in title_lower.split() for w in vn_words):
        return ['vi', 'en']

    # CJK characters → likely Chinese/Japanese/Korean
    if re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', title):
        if re.search(r'[\u3040-\u30ff]', title):
            return ['ja', 'en']
        return ['zh-Hans', 'zh-Hant', 'zh', 'en']

    # Korean
    if re.search(r'[\uac00-\ud7af]', title):
        return ['ko', 'en']

    # Thai
    if re.search(r'[\u0e00-\u0e7f]', title):
        return ['th', 'en']

    # Default: English first
    return ['en', 'vi']


def get_transcript(video_id: str, title: str = "") -> Optional[str]:
    """
    Get transcript text for a YouTube video.
    Returns plain text string or None if unavailable.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

        lang_priority = detect_language_from_title(title)

        # Try preferred languages first
        all_transcripts = None
        try:
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id)
            all_transcripts = transcript_list
        except TranscriptsDisabled:
            logger.info(f"[Transcript] Disabled for {video_id}")
            return None
        except Exception as e:
            logger.warning(f"[Transcript] list_transcripts error for {video_id}: {e}")
            return None

        # Try manual captions first (higher quality), then auto-generated
        transcript_obj = None

        # 1. Try manual transcript in preferred language
        for lang in lang_priority:
            try:
                transcript_obj = all_transcripts.find_manually_created_transcript([lang])
                logger.info(f"[Transcript] Found manual '{lang}' transcript for {video_id}")
                break
            except NoTranscriptFound:
                continue

        # 2. Try auto-generated transcript in preferred language
        if not transcript_obj:
            for lang in lang_priority:
                try:
                    transcript_obj = all_transcripts.find_generated_transcript([lang])
                    logger.info(f"[Transcript] Found auto-generated '{lang}' transcript for {video_id}")
                    break
                except NoTranscriptFound:
                    continue

        # 3. Fall back to any available transcript
        if not transcript_obj:
            try:
                # Get the first available transcript
                for t in all_transcripts:
                    transcript_obj = t
                    logger.info(f"[Transcript] Using fallback transcript lang={t.language_code} for {video_id}")
                    break
            except Exception:
                pass

        if not transcript_obj:
            logger.info(f"[Transcript] No transcript available for {video_id}")
            return None

        # Fetch and join transcript segments
        segments = transcript_obj.fetch()
        full_text = ""
        for seg in segments:
            text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
            if text:
                full_text += text + " "
                
        full_text = re.sub(r'\s+', ' ', full_text).strip()

        logger.info(f"[Transcript] Got {len(full_text)} chars for {video_id}")
        return full_text if len(full_text) > 50 else None

    except ImportError:
        logger.warning("[Transcript] youtube-transcript-api not installed. Run: pip install youtube-transcript-api")
        return None
    except Exception as e:
        logger.warning(f"[Transcript] Unexpected error for {video_id}: {e}")
        return None


async def get_video_metadata(video_id: str) -> dict:
    """
    Fetch video metadata using YouTube oEmbed + thumbnail (no API key needed).
    Returns dict with: title, thumbnail_url, author_name, video_url
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    meta = {
        "video_id": video_id,
        "video_url": video_url,
        "title": "",
        "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        "author_name": "",
        "description": "",
    }

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # oEmbed API - no key needed
            resp = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": video_url, "format": "json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                meta["title"] = data.get("title", "")
                meta["author_name"] = data.get("author_name", "")
                # Use hq thumbnail, fallback to maxresdefault
                meta["thumbnail_url"] = data.get("thumbnail_url",
                    f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg")
    except Exception as e:
        logger.warning(f"[Metadata] oEmbed error for {video_id}: {e}")

    return meta


def build_youtube_embed_html(video_id: str) -> str:
    """Generate responsive YouTube embed iframe HTML."""
    return (
        f'<div class="yt-embed-wrapper" style="position:relative;padding-bottom:56.25%;'
        f'height:0;overflow:hidden;max-width:100%;margin:24px 0;">'
        f'<iframe src="https://www.youtube.com/embed/{video_id}" '
        f'style="position:absolute;top:0;left:0;width:100%;height:100%;" '
        f'frameborder="0" allow="accelerometer; autoplay; clipboard-write; '
        f'encrypted-media; gyroscope; picture-in-picture" allowfullscreen>'
        f'</iframe></div>'
    )
