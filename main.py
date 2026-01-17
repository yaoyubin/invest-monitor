import asyncio
import aiohttp
import time
import os
import json
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from bilibili_api import user
from googleapiclient.discovery import build
from up_list import TARGET_UIDS, UP_LIST, KEYWORDS, NO_FILTER_UIDS, UP_NAME_MAP, YOUTUBE_CHANNELS, YOUTUBE_NO_FILTER_CHANNELS

# 加载 .env 文件中的环境变量
load_dotenv()

# ================= 配置区域 =================

HISTORY_DAYS = 14 # 记忆保留时间稍微拉长一点，防止周报重复
CONCURRENCY_LIMIT = 2  # 降低并发数，避免触发风控
# ===========================================

class HistoryManager:
    """记忆管理 (保持不变)"""
    def __init__(self, file_path="history.json"):
        self.file_path = file_path
        self.data = self._load()

    def _load(self):
        if not os.path.exists(self.file_path):
            return {}
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    def is_processed(self, video_id):
        """
        检查视频是否已处理
        支持两种格式：
        - B站: bvid (如: "BVxxxxx")
        - YouTube: "yt:video_id" (如: "yt:dQw4w9WgXcQ")
        """
        return video_id in self.data

    def add(self, video_id, platform='bilibili'):
        """
        添加已处理的视频
        platform: 'bilibili' 或 'youtube'
        """
        if platform == 'youtube':
            # YouTube 视频ID添加前缀
            video_id = f"yt:{video_id}"
        self.data[video_id] = int(time.time())

    def save_and_clean(self):
        now = time.time()
        expire_time = now - (HISTORY_DAYS * 24 * 3600)
        new_data = {k: v for k, v in self.data.items() if v > expire_time}
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=2)
        print(f"记忆库更新：清理后剩余 {len(new_data)} 条记录")

memory = HistoryManager()

def get_time_config():
    """【新功能】根据今天是星期几，决定抓取策略"""
    # 获取当前美国西部时间 (PST, UTC-8)
    utc_now = datetime.datetime.utcnow()
    pst_now = utc_now - datetime.timedelta(hours=8)
    weekday = pst_now.weekday() # 0是周一, ..., 6是周日
    
    current_timestamp = time.time()
    
    if weekday == 5: # 如果是周六
        print("今天是周六（美国西部时间），执行【周报】模式，抓取过去 7 天...")
        return {
            "title": "UGC监控周报 (Past 7 Days)",
            "window": 7 * 24 * 3600,
            "now": current_timestamp
        }
    else: # 其他6天（周日到周五）都是日报模式
        print("今天执行【日报】模式，抓取过去 1 天...")
        return {
            "title": "UGC监控日报",
            "window": 26 * 3600, # 设置26小时，稍微多一点防止漏掉边界
            "now": current_timestamp
        }

async def fetch_videos_from_up(uid, semaphore, retry_count=3):
    """获取UP主视频，带重试机制"""
    async with semaphore:
        for attempt in range(retry_count):
            try:
                u = user.User(uid=uid)
                # 周报模式下，5条可能不够，改为获取最近 10 条
                videos = await u.get_videos(ps=10) 
                
                # 检查是否有错误
                if isinstance(videos, dict) and videos.get('code') == -352:
                    # 风控错误，等待更长时间后重试
                    wait_time = (attempt + 1) * 3  # 3秒、6秒、9秒
                    print(f"⚠️  UID {uid} 触发风控，等待 {wait_time} 秒后重试... (尝试 {attempt + 1}/{retry_count})")
                    await asyncio.sleep(wait_time)
                    continue
                
                # 成功获取数据
                await asyncio.sleep(1)  # 增加延迟，避免触发风控
                return videos.get('list', {}).get('vlist', [])
                
            except Exception as e:
                error_msg = str(e)
                # 检查是否是风控错误
                if '-352' in error_msg or '风控' in error_msg:
                    wait_time = (attempt + 1) * 3
                    if attempt < retry_count - 1:
                        print(f"⚠️  UID {uid} 触发风控，等待 {wait_time} 秒后重试... (尝试 {attempt + 1}/{retry_count})")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        print(f"❌ UID {uid} 获取失败（风控限制）: {error_msg}")
                        return []
                else:
                    # 其他错误，直接返回
                    print(f"❌ UID {uid} 获取失败: {error_msg}")
                    return []
        
        # 所有重试都失败
        print(f"❌ UID {uid} 获取失败，已重试 {retry_count} 次")
        return []

async def fetch_youtube_videos(channel_id, semaphore, retry_count=3):
    """获取YouTube频道视频，带重试机制"""
    async with semaphore:
        youtube_api_key = os.environ.get("YOUTUBE_API_KEY")
        if not youtube_api_key:
            print(f"⚠️  YOUTUBE_API_KEY 未设置，跳过 YouTube 频道 {channel_id}")
            return []
        
        for attempt in range(retry_count):
            try:
                # 使用 asyncio.to_thread 包装同步的 YouTube API 调用
                def get_videos_sync():
                    youtube = build('youtube', 'v3', developerKey=youtube_api_key)
                    
                    # 第一步：获取 channel 信息和 uploads playlist ID
                    channel_response = youtube.channels().list(
                        part='contentDetails,snippet',
                        id=channel_id
                    ).execute()
                    
                    if not channel_response.get('items'):
                        return None, None
                    
                    channel_info = channel_response['items'][0]
                    uploads_playlist_id = channel_info['contentDetails']['relatedPlaylists']['uploads']
                    channel_name = channel_info['snippet']['title']
                    
                    # 第二步：获取 uploads playlist 中的最新视频
                    playlist_response = youtube.playlistItems().list(
                        part='snippet,contentDetails',
                        playlistId=uploads_playlist_id,
                        maxResults=10
                    ).execute()
                    
                    videos = []
                    for item in playlist_response.get('items', []):
                        snippet = item['snippet']
                        video_id = snippet['resourceId']['videoId']
                        title = snippet['title']
                        description = snippet.get('description', '')
                        # YouTube API 返回 ISO 8601 格式时间，转换为时间戳
                        published_at = snippet['publishedAt']
                        published_dt = datetime.datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                        created_timestamp = int(published_dt.timestamp())
                        
                        videos.append({
                            'video_id': video_id,
                            'title': title,
                            'description': description,
                            'created': created_timestamp,
                            'author': channel_name,
                            'platform': 'youtube',
                            'channel_id': channel_id
                        })
                    
                    return videos, channel_name
                
                videos, channel_name = await asyncio.to_thread(get_videos_sync)
                
                if videos is None:
                    print(f"❌ YouTube 频道 {channel_id} 不存在或无法访问")
                    return []
                
                if videos:
                    print(f"✓ YouTube 频道 {channel_id} ({channel_name}): 获取到 {len(videos)} 个视频")
                
                await asyncio.sleep(1)  # 延迟，避免触发 API 限制
                return videos
                
            except Exception as e:
                error_msg = str(e)
                # 检查是否是配额错误
                if 'quota' in error_msg.lower() or 'quotaExceeded' in error_msg:
                    print(f"❌ YouTube API 配额耗尽，无法获取频道 {channel_id} 的视频")
                    return []
                
                wait_time = (attempt + 1) * 2
                if attempt < retry_count - 1:
                    print(f"⚠️  YouTube 频道 {channel_id} 获取失败，等待 {wait_time} 秒后重试... (尝试 {attempt + 1}/{retry_count})")
                    print(f"   错误: {error_msg}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    print(f"❌ YouTube 频道 {channel_id} 获取失败: {error_msg}")
                    return []
        
        # 所有重试都失败
        print(f"❌ YouTube 频道 {channel_id} 获取失败，已重试 {retry_count} 次")
        return []

async def filter_content(video_data, time_config, up_uid=None, platform='bilibili'):
    """【过滤层】增加了严格的时间判断和特殊UP主支持，支持B站和YouTube"""
    # 1. 【新增】严格的时间过滤
    # created 是视频发布时间戳
    video_time = video_data['created']
    # 如果 (当前时间 - 视频时间) > 允许的时间窗口，则说明是旧视频
    if (time_config['now'] - video_time) > time_config['window']:
        return False

    # 2. 特殊UP主/频道检查：如果在NO_FILTER列表中，跳过关键词过滤
    if platform == 'bilibili' and up_uid and up_uid in NO_FILTER_UIDS:
        return True
    elif platform == 'youtube' and up_uid and up_uid in YOUTUBE_NO_FILTER_CHANNELS:
        return True

    # 3. 关键词硬过滤（仅对普通UP主/频道）
    title = video_data['title']
    # 修复简介可能为空的bug
    desc = video_data.get('description', '')
    full_text = (title + desc).lower()
    
    hit_keyword = False
    for kw in KEYWORDS:
        if kw.lower() in full_text:
            hit_keyword = True
            break
    
    if not hit_keyword:
        return False

    return True

async def send_notification(content, title_prefix):
    """使用Gmail SMTP发送邮件通知"""
    sender_email = os.environ.get("GMAIL_SENDER")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient_email = os.environ.get("GMAIL_RECIPIENT")
    
    if not sender_email or not app_password or not recipient_email:
        print("❌ Gmail配置未设置（需要：GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT）")
        return False
    
    # 构建HTML格式的邮件内容
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body>
        <h3>{title_prefix}</h3>
        {content}
    </body>
    </html>
    """
    
    # 创建邮件消息
    msg = MIMEMultipart('alternative')
    msg['Subject'] = title_prefix
    msg['From'] = sender_email
    msg['To'] = recipient_email
    
    # 添加HTML内容
    html_part = MIMEText(html_content, 'html', 'utf-8')
    msg.attach(html_part)
    
    # 使用asyncio.to_thread包装同步SMTP调用
    def send_email_sync():
        """同步发送邮件函数"""
        try:
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(sender_email, app_password)
                server.send_message(msg)
            return True, None
        except Exception as e:
            return False, str(e)
    
    try:
        success, error_msg = await asyncio.to_thread(send_email_sync)
        if success:
            print(f"✅ 邮件发送成功")
            return True
        else:
            print(f"❌ 邮件发送失败: {error_msg}")
            import traceback
            traceback.print_exc()
            return False
    except Exception as e:
        print(f"❌ 邮件发送异常: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    # 1. 获取今日策略 (周报 vs 日报)
    config = get_time_config()
    
    print(f"开始监控 {len(TARGET_UIDS)} 个B站UP主...")
    if YOUTUBE_CHANNELS:
        print(f"开始监控 {len(YOUTUBE_CHANNELS)} 个YouTube频道...")
    print(f"并发限制: {CONCURRENCY_LIMIT}")
    print("")
    
    valid_videos = []
    success_count = 0
    fail_count = 0
    
    # 2. 获取B站视频
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    bilibili_tasks = [fetch_videos_from_up(uid, semaphore) for uid in TARGET_UIDS]
    bilibili_results = await asyncio.gather(*bilibili_tasks, return_exceptions=True)
    
    for i, result in enumerate(bilibili_results):
        if isinstance(result, Exception):
            fail_count += 1
            print(f"❌ UID {TARGET_UIDS[i]} 获取异常: {result}")
            continue
        
        if not result:
            fail_count += 1
            continue
        
        success_count += 1
        current_uid = TARGET_UIDS[i]  # 当前UP主的UID
        for v in result:
            bvid = v['bvid']
            
            # 记忆去重
            if memory.is_processed(bvid):
                continue
            
            # 传入 config 和 UID 进行过滤判断
            if await filter_content(v, config, up_uid=current_uid, platform='bilibili'):
                print(f"发现新视频（B站）：{v['title']}")
                valid_videos.append(v)
                memory.add(bvid, platform='bilibili')
    
    # 3. 获取YouTube视频
    youtube_channel_ids = list(YOUTUBE_CHANNELS.keys())
    if youtube_channel_ids:
        youtube_tasks = [fetch_youtube_videos(channel_id, semaphore) for channel_id in youtube_channel_ids]
        youtube_results = await asyncio.gather(*youtube_tasks, return_exceptions=True)
        
        for i, result in enumerate(youtube_results):
            channel_id = youtube_channel_ids[i]
            
            if isinstance(result, Exception):
                fail_count += 1
                print(f"❌ YouTube 频道 {channel_id} 获取异常: {result}")
                continue
            
            if not result:
                fail_count += 1
                continue
            
            success_count += 1
            for v in result:
                video_id = v['video_id']
                
                # 记忆去重（使用 "yt:video_id" 格式）
                if memory.is_processed(video_id):
                    continue
                
                # 传入 config 和 Channel ID 进行过滤判断
                if await filter_content(v, config, up_uid=channel_id, platform='youtube'):
                    print(f"发现新视频（YouTube）：{v['title']}")
                    valid_videos.append(v)
                    memory.add(video_id, platform='youtube')
    
    print(f"\n监控完成：成功 {success_count} 个，失败 {fail_count} 个")

    if valid_videos:
        # 按发布时间倒序排列 (新的在前)
        valid_videos.sort(key=lambda x: x['created'], reverse=True)
        
        msg = "<ul>"
        for v in valid_videos:
            # 格式化一下时间，比如 [01-05]
            time_str = time.strftime("%m-%d", time.localtime(v['created']))
            platform = v.get('platform', 'bilibili')
            
            if platform == 'youtube':
                video_id = v['video_id']
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                platform_tag = "[YouTube]"
            else:
                video_id = v['bvid']
                video_url = f"https://www.bilibili.com/video/{video_id}"
                platform_tag = "[B站]"
            
            msg += f"<li style='margin-bottom:8px'>[{time_str}] {platform_tag} <b>{v['author']}</b>: <a href='{video_url}'>{v['title']}</a></li>"
        msg += "</ul>"
        
        success = await send_notification(msg, config['title'])
        if success:
            print(f"推送成功！共 {len(valid_videos)} 条")
        else:
            print(f"推送失败！共 {len(valid_videos)} 条（请查看上方错误信息）")
    else:
        print("没有符合条件的新视频。")

    memory.save_and_clean()

if __name__ == '__main__':
    asyncio.run(main())
