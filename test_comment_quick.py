"""
Quick test: verify fixed post fetching + commenting
"""
import json
import sys
import logging
sys.stdout.reconfigure(encoding='utf-8')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from weibo_client import WeiboClient

# Load cookie
with open("e:/GitHub/weibo/.env", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line.startswith("WEIBO_COOKIE="):
            cookie_str = line.split("WEIBO_COOKIE=", 1)[1]
            break

print(f"Cookie length: {len(cookie_str)}")

client = WeiboClient(cookie_str)

# Test getting posts
containerid = "1008086f1b7983ba4fca7456e28317e78127ed"
print(f"\n{'='*60}")
print("Testing get_super_topic_posts...")
print(f"{'='*60}")
posts = client.get_super_topic_posts(containerid, count=3)
print(f"\nPosts returned: {len(posts)}")
for i, p in enumerate(posts):
    print(f"  [{i+1}] mid={p['mid']} @{p['user']}: {p['text'][:60]}")

if posts:
    print(f"\n{'='*60}")
    print("Testing comment on post 1...")
    print(f"{'='*60}")
    result = client.comment_post(
        post_mid=posts[0]['mid'],
        content="鞠婧祎",
        post_id=posts[0]['id'],
    )
    print(f"Comment result: {json.dumps(result, ensure_ascii=False)}")

client.cleanup()
print("\nDone!")
