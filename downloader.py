import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import yt_dlp
import requests

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": MOBILE_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def clean_filename(name: str, limit: int = 80) -> str:
    name = re.sub(r'[\\/*?:\"<>|\n\r\t]', "", name or "video")
    name = re.sub(r"\s+", " ", name).strip()
    return (name[:limit] or "video").strip(". ")

def fmt_size(n):
    n = float(n or 0)
    for u in ("B","KB","MB","GB"):
        if n < 1024 or u=="GB":
            return f"{n:.1f} {u}"
        n/=1024.0

def get_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

def build_ydl_opts(extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": BASE_HEADERS,
        # no cookies by default - public only
        "extractor_args": {
            "youtube": {"player_client": ["android", "web"]},
            "tiktok": {"api_hostname": ["api16-normal-c-useast1a.tiktokv.com"]},
            "instagram": {"api_version": ["v1"]},
        },
    }
    if extra:
        opts.update(extra)
    return opts

def detect_site(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube Shorts" if "/shorts/" in u else "YouTube"
    if "tiktok.com" in u:
        return "TikTok"
    if "instagram.com" in u:
        return "Instagram"
    if "facebook.com" in u or "fb.watch" in u or "fb.gg" in u:
        return "Facebook"
    if "pinterest." in u or "pin.it" in u:
        return "Pinterest"
    if "pornhub.com" in u:
        return "Ph"
    return "Video"

def extract_info(url: str):
    """Returns dict: title, duration, thumbnail, site, formats_available"""
    with yt_dlp.YoutubeDL(build_ydl_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("مش قادر أقرأ الرابط ده، يمكن خاص أو محذوف")
    # playlist -> take first
    if "entries" in info and info.get("entries"):
        info = info["entries"][0]
    title = info.get("title") or "video"
    duration = info.get("duration") or 0
    thumb = info.get("thumbnail") or ""
    formats = info.get("formats") or []
    # هل فيه فيديو حقيقي؟
    has_video = any(f.get("vcodec") not in (None, "none") for f in formats) or info.get("vcodec") not in (None, "none")
    # Instagram login wall detection - yt-dlp بيرجع صفحة login فاضية
    if not has_video and not info.get("url"):
        raise RuntimeError("الرابط ده شكله خاص أو محتاج تسجيل دخول، والبوت بيشتغل على العام بس")
    return {
        "title": clean_filename(title),
        "raw_title": title,
        "duration": duration,
        "thumbnail": thumb,
        "site": detect_site(url),
        "ext": info.get("ext") or "mp4",
        "formats": formats,
        "webpage_url": info.get("webpage_url") or url,
    }

# format strings for yt-dlp
QUALITY_MAP = {
    "best": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best",
    "720": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best",
    "480": "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[ext=mp4][height<=480]/best",
    "360": "bestvideo[ext=mp4][height<=360]+bestaudio[ext=m4a]/best[ext=mp4][height<=360]/best",
}

def download_video(url: str, quality: str, out_dir: Path, progress_hook=None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / "%(title)s.%(ext)s")
    fmt = QUALITY_MAP.get(quality, QUALITY_MAP["best"])
    opts = build_ydl_opts({
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": tmpl,
        "progress_hooks": [progress_hook] if progress_hook else [],
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info and info.get("entries"):
            info = info["entries"][0]
        fname = ydl.prepare_filename(info)
        # لو merged بقى mp4
        p = Path(fname)
        if not p.exists():
            # دور على أي ملف جديد في out_dir
            files = sorted(out_dir.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
            if files:
                p = files[0]
        # لو ext مش mp4 وحصل merge
        if p.suffix != ".mp4":
            mp4 = p.with_suffix(".mp4")
            if mp4.exists():
                p = mp4
        return p

def download_audio_mp3(url: str, out_dir: Path, progress_hook=None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / "%(title)s.%(ext)s")
    ffmpeg = get_ffmpeg()
    # نستخدم postprocessor بتاع yt-dlp لو ffmpeg موجود، غير كده ننزل وبعدين نحول
    postprocessors = []
    if ffmpeg:
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    opts = build_ydl_opts({
        "format": "bestaudio/best",
        "outtmpl": tmpl,
        "postprocessors": postprocessors,
        "progress_hooks": [progress_hook] if progress_hook else [],
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info and info.get("entries"):
            info = info["entries"][0]
        fname = ydl.prepare_filename(info)
        p = Path(fname)
        # لو postprocessor اشتغل، الملف بقى mp3
        mp3 = p.with_suffix(".mp3")
        if mp3.exists():
            return mp3
        if p.exists() and p.suffix == ".mp3":
            return p
        # fallback: حول يدويا بـ ffmpeg
        if p.exists() and ffmpeg:
            out = p.with_suffix(".mp3")
            subprocess.run([ffmpeg, "-y", "-i", str(p), "-vn", "-ab", "192k", str(out)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
            if out.exists():
                try: p.unlink()
                except: pass
                return out
        if p.exists():
            return p
        # دور على أحدث ملف
        files = sorted(out_dir.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
        if files:
            return files[0]
        raise RuntimeError("فشل تحويل الصوت لـ MP3")

def make_tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="metabot_"))

def cleanup_dir(d: Path):
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


# ================= Facebook / Pinterest stronger fallback =================
# yt-dlp alone is 90%+ on YouTube/TikTok/Instagram/Ph, but Facebook blocks
# datacenter IPs (Render) and Pinterest sometimes needs its JSON.
# So if yt-dlp fails, we try a direct scrape before giving up.

def _scrape_page(url: str, referer=None):
    try:
        h = dict(BASE_HEADERS)
        if referer:
            h["Referer"] = referer
        r = requests.get(url, headers=h, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            return None, None
        return r.text, r.url
    except Exception:
        return None, None

def _find_mp4_in_html(html: str):
    if not html:
        return None
    # Facebook puts playable_url / browser_native_hd_url / sd_src in JSON
    # Pinterest puts video_url / "url": "https://v1.pinimg.com/..." in JSON
    import json as _json
    # 1) unescape json strings
    candidates = []
    # direct mp4 links
    for m in re.finditer(r'https?:\\?/\\?/[^"\'\\s]*\.mp4[^"\'\\s]*', html):
        u = m.group(0).replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&")
        # filter out tiny chrome / preview
        if len(u) > 30:
            candidates.append(u)
    # 2) Facebook specific keys
    for key in ["playable_url", "browser_native_hd_url", "browser_native_sd_url", "playable_url_quality_hd", "video_url", "contentUrl"]:
        for m in re.finditer(r'"' + key + r'"\s*:\s*"([^"]+)"', html):
            u = m.group(1).replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&")
            if "mp4" in u or "video" in u or "fbcdn" in u or "pinimg" in u:
                candidates.append(u)
    # 3) Pinterest video in JSON: "videos": {"video_list": {"V_720P": {"url": "..."}}}
    for m in re.finditer(r'"url"\s*:\s*"(https://[^"]*pinimg[^"]*\.mp4[^"]*)"', html):
        candidates.append(m.group(1).replace("\\/", "/"))
    # dedup, longest first (usually best quality)
    seen = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    seen.sort(key=len, reverse=True)
    return seen[0] if seen else None

def _scrape_pinterest_direct(url: str):
    # Try pinterest pin JSON via ? + scraping
    # 1) normal page
    html, final = _scrape_page(url)
    mp4 = _find_mp4_in_html(html or "")
    if mp4:
        return mp4
    # 2) try pin id extraction: /pin/123456/
    m = re.search(r'/pin/(\d+)', url)
    if m:
        pid = m.group(1)
        for cand in [f"https://www.pinterest.com/pin/{pid}/", f"https://www.pinterest.com/pin/{pid}/?amp=1"]:
            html2, _ = _scrape_page(cand)
            mp4 = _find_mp4_in_html(html2 or "")
            if mp4:
                return mp4
    return None

def _scrape_facebook_direct(url: str):
    # Try mbasic (lighter, less JS) + normal
    # mbasic often still has playable_url in HTML
    candidates = []
    # normalize to mbasic
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        mbasic = urlunparse(("https", "mbasic.facebook.com", parsed.path, parsed.params, parsed.query, parsed.fragment))
        candidates.append(mbasic)
    except Exception:
        pass
    candidates.append(url)
    # also try www with ?_fb_noscript=1
    if "?" in url:
        candidates.append(url + "&_fb_noscript=1")
    else:
        candidates.append(url + "?_fb_noscript=1")
    for cand in candidates:
        html, _ = _scrape_page(cand, referer="https://www.facebook.com/")
        mp4 = _find_mp4_in_html(html or "")
        if mp4:
            return mp4
        # check for login wall - if page is login, stop trying same host
        if html and ("login" in html[:2000].lower() and "facebook" in html[:2000].lower() and "playable_url" not in html):
            # mbasic login wall means this video needs login - don't spam
            continue
    return None

def extract_info_with_fallback(url: str):
    """Try yt-dlp first, then FB/Pinterest scrape fallback. Returns same dict + direct_url if fallback."""
    try:
        return extract_info(url)
    except Exception as e:
        site = detect_site(url)
        direct = None
        if site == "Pinterest":
            direct = _scrape_pinterest_direct(url)
        elif site == "Facebook":
            direct = _scrape_facebook_direct(url)
        if direct:
            return {
                "title": clean_filename(f"{site} video"),
                "raw_title": f"{site} video",
                "duration": 0,
                "thumbnail": "",
                "site": site,
                "ext": "mp4",
                "formats": [],
                "webpage_url": url,
                "direct_url": direct,
            }
        raise e

def download_direct_mp4(direct_url: str, referer: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "video.mp4"
    h = dict(BASE_HEADERS)
    h["Referer"] = referer or "https://www.facebook.com/"
    h["Accept"] = "*/*"
    with requests.get(direct_url, headers=h, stream=True, timeout=(15, 60), allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(256*1024):
                if chunk:
                    f.write(chunk)
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError("direct download was empty")
    return dest
