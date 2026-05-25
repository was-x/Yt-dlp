"""
YouTube Stream API - Fetches cookies from batbin.me URL for every request
Single endpoint that gets fresh cookies from the URL each time
"""

from flask import Flask, request, jsonify, redirect
import yt_dlp
import requests
import re
import os
import json
import time
import logging
import sys
import signal
from datetime import datetime, timedelta
from typing import Dict, Optional
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('youtube_api.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# The batbin URL containing cookies
COOKIE_URL = "https://batbin.me/raw/jennier"

def fetch_cookies_from_url() -> Optional[str]:
    """
    Fetch cookies from the batbin URL and save to a temporary file
    Returns path to cookie file or None if failed
    """
    try:
        logger.info("🌐 Fetching cookies from batbin.me...")
        
        # Fetch cookies from URL
        response = requests.get(COOKIE_URL, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch cookies. Status: {response.status_code}")
            return None
        
        cookies_content = response.text
        
        # Validate that it looks like a cookies file
        if not cookies_content.startswith('# Netscape HTTP Cookie File'):
            logger.warning("Fetched content doesn't look like a cookies file")
            return None
        
        # Create a unique cookie file for this request
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        cookie_file = f'cookies_{timestamp}_{unique_id}.txt'
        
        # Save cookies to file
        with open(cookie_file, 'w') as f:
            f.write(cookies_content)
        
        logger.info(f"✅ Cookies saved to {cookie_file}")
        
        # Count cookies (lines that are not comments and not empty)
        cookie_count = sum(1 for line in cookies_content.split('\n') 
                          if line.strip() and not line.startswith('#'))
        logger.info(f"📊 Loaded {cookie_count} cookies")
        
        return cookie_file
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch cookies: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching cookies: {e}")
        return None

def extract_video_id(url_or_id):
    """Extract YouTube video ID from URL or return the ID if already clean"""
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id
    
    patterns = [
        r'(?:youtube\.com\/watch\?v=)([^&]+)',
        r'(?:youtu\.be\/)([^?]+)',
        r'(?:youtube\.com\/embed\/)([^?]+)',
        r'(?:youtube\.com\/v\/)([^?]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    
    return None

def cleanup_old_cookie_files(max_age_minutes=5):
    """
    Clean up old cookie files
    """
    try:
        current_time = time.time()
        for filename in os.listdir('.'):
            if filename.startswith('cookies_') and filename.endswith('.txt'):
                file_path = os.path.join('.', filename)
                file_age = current_time - os.path.getmtime(file_path)
                
                # Delete files older than max_age_minutes
                if file_age > (max_age_minutes * 60):
                    try:
                        os.unlink(file_path)
                        logger.debug(f"Cleaned up old cookie file: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {filename}: {e}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

@app.route('/stream', methods=['GET'])
def get_stream():
    """
    Get streaming URL for a video - fetches fresh cookies from batbin for each request
    Usage: /stream?id=VIDEO_ID_OR_URL&format=FORMAT_ID
    """
    # Get parameters
    video_id = request.args.get('id')
    format_id = request.args.get('format', 'best')
    redirect_mode = request.args.get('redirect', 'false').lower() == 'true'
    
    if not video_id:
        return jsonify({
            'success': False,
            'error': 'Missing id parameter',
            'usage': {
                'endpoint': '/stream?id=VIDEO_ID&format=FORMAT_ID',
                'examples': {
                    '360p': '/stream?id=p7ZG_xWYLzI&format=18',
                    '720p': '/stream?id=p7ZG_xWYLzI&format=22',
                    '1080p': '/stream?id=p7ZG_xWYLzI&format=137',
                    'best': '/stream?id=p7ZG_xWYLzI&format=best',
                    'audio': '/stream?id=p7ZG_xWYLzI&format=bestaudio',
                    'redirect': '/stream?id=p7ZG_xWYLzI&format=18&redirect=true'
                }
            }
        }), 400
    
    # Extract clean video ID
    clean_id = extract_video_id(video_id)
    if not clean_id:
        return jsonify({
            'success': False,
            'error': 'Invalid video ID or URL'
        }), 400
    
    # Generate a request ID for tracking
    request_id = str(uuid.uuid4())[:8]
    logger.info(f"[{request_id}] Processing request for video: {clean_id}, format: {format_id}")
    
    cookie_file = None
    video_url = f'https://www.youtube.com/watch?v={clean_id}'
    start_time = time.time()
    
    try:
        # Fetch fresh cookies from batbin URL for this request
        cookie_file = fetch_cookies_from_url()
        
        if not cookie_file:
            return jsonify({
                'success': False,
                'request_id': request_id,
                'error': 'Failed to fetch cookies from batbin.me'
            }), 500
        
        # Configure yt-dlp with the fresh cookies
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'cookiefile': cookie_file,
            'format': format_id,
            'headers': {
                'Referer': 'https://www.youtube.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'translated_subs'],
                    'player_client': ['android', 'web']
                }
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"[{request_id}] Extracting video info with fresh cookies")
            info = ydl.extract_info(video_url, download=False)
            
            # Find the stream URL
            stream_url = None
            format_info = None
            
            # Try to get URL from formats list
            if 'formats' in info:
                for f in info['formats']:
                    if f.get('format_id') == format_id and f.get('url'):
                        stream_url = f['url']
                        format_info = {
                            'format_id': f.get('format_id'),
                            'ext': f.get('ext'),
                            'resolution': f.get('resolution') or f.get('format_note') or 'N/A',
                            'filesize': f.get('filesize'),
                            'filesize_mb': round(f.get('filesize', 0) / (1024 * 1024), 2) if f.get('filesize') else None,
                            'vcodec': f.get('vcodec', 'none'),
                            'acodec': f.get('acodec', 'none'),
                            'fps': f.get('fps')
                        }
                        break
            
            # If not found in formats, try the main URL
            if not stream_url and 'url' in info:
                stream_url = info['url']
                format_info = {
                    'format_id': format_id,
                    'ext': info.get('ext', 'unknown'),
                    'resolution': 'best'
                }
            
            if stream_url:
                elapsed_time = time.time() - start_time
                logger.info(f"[{request_id}] Success - Time: {elapsed_time:.2f}s")
                
                # If redirect mode is enabled, redirect directly to the stream URL
                if redirect_mode:
                    return redirect(stream_url)
                
                return jsonify({
                    'success': True,
                    'stream_url': stream_url,
                    'video_id': clean_id,
                    'title': info.get('title'),
                    'duration': info.get('duration'),
                    'uploader': info.get('uploader'),
                    'format': format_info,
                    'format_requested': format_id,
                    'view_count': info.get('view_count'),
                    'like_count': info.get('like_count'),
                    'request_id': request_id,
                    'processing_time': round(elapsed_time, 2),
                    'cookie_source': COOKIE_URL
                })
            else:
                # Get available formats
                available_formats = []
                if 'formats' in info:
                    for f in info['formats'][:20]:  # Show top 20 formats
                        available_formats.append({
                            'format_id': f.get('format_id'),
                            'ext': f.get('ext'),
                            'resolution': f.get('resolution') or f.get('format_note') or 'N/A',
                            'has_video': f.get('vcodec') != 'none',
                            'has_audio': f.get('acodec') != 'none',
                            'filesize_mb': round(f.get('filesize', 0) / (1024 * 1024), 2) if f.get('filesize') else None
                        })
                
                elapsed_time = time.time() - start_time
                return jsonify({
                    'success': False,
                    'error': f'Format {format_id} not available',
                    'available_formats': available_formats,
                    'title': info.get('title'),
                    'request_id': request_id,
                    'processing_time': round(elapsed_time, 2)
                }), 404
                
    except Exception as e:
        error_msg = str(e)
        # Remove ANSI escape codes
        error_msg = re.sub(r'\x1b\[[0-9;]*m', '', error_msg)
        logger.error(f"[{request_id}] Error: {error_msg}")
        
        elapsed_time = time.time() - start_time
        return jsonify({
            'success': False,
            'error': error_msg,
            'request_id': request_id,
            'processing_time': round(elapsed_time, 2)
        }), 500
    
    finally:
        # Clean up the temporary cookie file
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.unlink(cookie_file)
                logger.debug(f"[{request_id}] Cleaned up cookie file: {cookie_file}")
            except Exception as e:
                logger.warning(f"[{request_id}] Failed to delete {cookie_file}: {e}")
        
        # Periodically clean up old cookie files (in case any were left)
        if request_id and int(request_id[:2], 16) % 5 == 0:  # Random cleanup ~20% of requests
            cleanup_old_cookie_files()

@app.route('/info', methods=['GET'])
def get_info():
    """
    Get video information without stream URL
    Usage: /info?id=VIDEO_ID_OR_URL
    """
    video_id = request.args.get('id')
    
    if not video_id:
        return jsonify({
            'success': False,
            'error': 'Missing id parameter'
        }), 400
    
    clean_id = extract_video_id(video_id)
    if not clean_id:
        return jsonify({
            'success': False,
            'error': 'Invalid video ID or URL'
        }), 400
    
    request_id = str(uuid.uuid4())[:8]
    cookie_file = None
    video_url = f'https://www.youtube.com/watch?v={clean_id}'
    start_time = time.time()
    
    try:
        # Fetch fresh cookies from batbin URL
        cookie_file = fetch_cookies_from_url()
        
        if not cookie_file:
            return jsonify({
                'success': False,
                'request_id': request_id,
                'error': 'Failed to fetch cookies from batbin.me'
            }), 500
        
        # Configure yt-dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'cookiefile': cookie_file,
            'headers': {
                'Referer': 'https://www.youtube.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            # Extract format information
            formats = []
            if 'formats' in info:
                for f in info['formats']:
                    formats.append({
                        'format_id': f.get('format_id'),
                        'ext': f.get('ext'),
                        'resolution': f.get('resolution') or f.get('format_note') or 'N/A',
                        'filesize_mb': round(f.get('filesize', 0) / (1024 * 1024), 2) if f.get('filesize') else None,
                        'has_video': f.get('vcodec') != 'none',
                        'has_audio': f.get('acodec') != 'none',
                        'fps': f.get('fps'),
                        'vcodec': f.get('vcodec'),
                        'acodec': f.get('acodec')
                    })
            
            elapsed_time = time.time() - start_time
            return jsonify({
                'success': True,
                'request_id': request_id,
                'video_id': clean_id,
                'title': info.get('title'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'view_count': info.get('view_count'),
                'like_count': info.get('like_count'),
                'description': info.get('description')[:500] + '...' if info.get('description') and len(info.get('description')) > 500 else info.get('description'),
                'thumbnail': info.get('thumbnail'),
                'upload_date': info.get('upload_date'),
                'formats': formats,
                'format_count': len(formats),
                'processing_time': round(elapsed_time, 2),
                'cookie_source': COOKIE_URL
            })
    
    except Exception as e:
        error_msg = re.sub(r'\x1b\[[0-9;]*m', '', str(e))
        elapsed_time = time.time() - start_time
        return jsonify({
            'success': False,
            'request_id': request_id,
            'error': error_msg,
            'processing_time': round(elapsed_time, 2)
        }), 500
    
    finally:
        # Clean up the temporary cookie file
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.unlink(cookie_file)
                logger.debug(f"[{request_id}] Cleaned up cookie file: {cookie_file}")
            except:
                pass

@app.route('/cookies/refresh', methods=['POST'])
def refresh_cookies():
    """
    Test endpoint to manually fetch and verify cookies
    """
    request_id = str(uuid.uuid4())[:8]
    
    try:
        cookie_file = fetch_cookies_from_url()
        
        if cookie_file:
            # Read cookie content for preview
            with open(cookie_file, 'r') as f:
                content = f.read()
            
            # Clean up
            os.unlink(cookie_file)
            
            # Get first few lines for preview
            preview_lines = content.split('\n')[:10]
            
            return jsonify({
                'success': True,
                'request_id': request_id,
                'message': 'Successfully fetched cookies',
                'cookie_preview': preview_lines,
                'cookie_source': COOKIE_URL
            })
        else:
            return jsonify({
                'success': False,
                'request_id': request_id,
                'error': 'Failed to fetch cookies'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'request_id': request_id,
            'error': str(e)
        }), 500

@app.route('/', methods=['GET'])
def home():
    """Home endpoint with usage instructions"""
    return jsonify({
        'name': 'YouTube Stream API - Batbin Cookie Fetcher',
        'version': '1.0.0',
        'description': 'Fetches fresh cookies from batbin.me URL for EVERY request',
        'cookie_source': COOKIE_URL,
        'endpoints': {
            'get_stream': {
                'url': '/stream?id=VIDEO_ID&format=FORMAT_ID&redirect=true/false',
                'description': 'Get streaming URL with fresh cookies from batbin',
                'examples': {
                    '360p_json': '/stream?id=p7ZG_xWYLzI&format=18',
                    '720p_json': '/stream?id=p7ZG_xWYLzI&format=22',
                    '1080p_json': '/stream?id=p7ZG_xWYLzI&format=137',
                    'best_json': '/stream?id=p7ZG_xWYLzI&format=best',
                    '360p_redirect': '/stream?id=p7ZG_xWYLzI&format=18&redirect=true',
                    'audio': '/stream?id=p7ZG_xWYLzI&format=bestaudio'
                }
            },
            'get_info': {
                'url': '/info?id=VIDEO_ID',
                'description': 'Get video information and available formats',
                'example': '/info?id=p7ZG_xWYLzI'
            },
            'test_cookies': {
                'url': '/cookies/refresh',
                'description': 'Test cookie fetching (POST request)',
                'example': 'curl -X POST http://localhost:5000/cookies/refresh'
            }
        },
        'common_format_ids': {
            '18': '360p (MP4)',
            '22': '720p (MP4)',
            '137': '1080p (video only)',
            '140': 'Audio only (M4A)',
            '251': 'Audio only (OPUS)',
            'best': 'Best quality',
            'bestaudio': 'Best audio only',
            'worst': 'Worst quality'
        },
        'note': 'Cookies are fetched from batbin.me for EVERY request - no persistent storage'
    })

@app.errorhandler(404)
def not_found(e):
    return jsonify({
        'success': False,
        'error': 'Endpoint not found',
        'available_endpoints': ['/', '/stream', '/info', '/cookies/refresh']
    }), 404

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info("Shutdown signal received. Cleaning up...")
    
    # Clean up all cookie files on shutdown
    try:
        for filename in os.listdir('.'):
            if filename.startswith('cookies_') and filename.endswith('.txt'):
                try:
                    os.unlink(filename)
                    logger.info(f"Cleaned up: {filename}")
                except:
                    pass
    except Exception as e:
        logger.error(f"Cleanup error on shutdown: {e}")
    
    sys.exit(0)

if __name__ == '__main__':
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 70)
    print("🚀 YouTube Stream API - Batbin Cookie Fetcher")
    print("=" * 70)
    print("Features:")
    print("  • Fetches cookies from batbin.me URL for EVERY request")
    print(f"  • Cookie URL: {COOKIE_URL}")
    print("  • No persistent cookie storage")
    print("  • Automatic cleanup of temporary files")
    print("\nEndpoints:")
    print("  • GET /stream?id=VIDEO_ID&format=FORMAT")
    print("  • GET /info?id=VIDEO_ID")
    print("  • POST /cookies/refresh - Test cookie fetching")
    print("\nExamples:")
    print("  • http://localhost:5000/stream?id=p7ZG_xWYLzI&format=18")
    print("  • http://localhost:5000/stream?id=p7ZG_xWYLzI&format=18&redirect=true")
    print("  • http://localhost:5000/info?id=p7ZG_xWYLzI")
    print("  • curl -X POST http://localhost:5000/cookies/refresh")
    print("=" * 70)
    
    # Test cookie fetching on startup
    print("\n🔍 Testing cookie fetch on startup...")
    test_cookie = fetch_cookies_from_url()
    if test_cookie:
        print("✅ Successfully fetched cookies from batbin.me")
        os.unlink(test_cookie)
    else:
        print("⚠️  Warning: Could not fetch cookies on startup")
    print("=" * 70 + "\n")
    
    # Run the app
    app.run(host='0.0.0.0', port=8000, debug=True, threaded=True)
