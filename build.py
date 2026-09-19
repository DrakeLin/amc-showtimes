"""Copy the existing PWA assets to Vercel's CDN directory."""
from pathlib import Path
import shutil

if __name__ == '__main__':
    target = Path('public/static')
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree('static', target, dirs_exist_ok=True)
    for name in ['favicon.ico', 'apple-touch-icon.png']:
        shutil.copyfile('static/icon-192.png', Path('public') / name)
