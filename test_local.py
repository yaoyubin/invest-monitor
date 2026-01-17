"""
本地测试脚本
用于测试B站UP主和YouTube频道视频监控系统，可以选择日报/周报模式，并且不发送真实邮件
"""

import asyncio
import time
import os
import sys

# 导入main.py中的函数和配置
from main import (
    fetch_videos_from_up,
    fetch_youtube_videos,
    filter_content,
    CONCURRENCY_LIMIT,
    HistoryManager
)
from up_list import TARGET_UIDS, UP_NAME_MAP, YOUTUBE_CHANNELS, YOUTUBE_NO_FILTER_CHANNELS

# ================= 测试配置 =================

# 是否保存到真实的 history.json（False 则使用临时文件）
USE_REAL_HISTORY = False

# 是否发送真实邮件（False 则只打印预览）
SEND_REAL_EMAIL = False

# ===========================================

# 使用测试用的历史记录文件
test_memory = HistoryManager("history_test.json") if not USE_REAL_HISTORY else None

# 导入真实的 memory 如果使用真实历史
if USE_REAL_HISTORY:
    from main import memory
    test_memory = memory

async def test_send_notification(content, title_prefix):
    """测试用的通知函数：只打印不发送邮件"""
    print("\n" + "="*70)
    print(f"📧 邮件通知预览（测试模式）")
    print("="*70)
    print(f"主题: {title_prefix}")
    print("-"*70)
    
    # 将HTML转换为可读的文本格式
    text_content = content \
        .replace("<ul>", "\n") \
        .replace("</ul>", "\n") \
        .replace("<li style='margin-bottom:8px'>", "  • ") \
        .replace("<li>", "  • ") \
        .replace("</li>", "\n") \
        .replace("<b>", "") \
        .replace("</b>", "") \
        .replace("<a href='", "(") \
        .replace("'>", ") ") \
        .replace("</a>", "")
    
    print(text_content)
    print("="*70)
    print("⚠️  这是测试模式，邮件未实际发送")
    print("="*70 + "\n")
    return True

async def main():
    print("\n" + "="*70)
    print("🧪 B站UP主视频监控系统 - 本地测试脚本")
    print("="*70 + "\n")
    
    # 1. 选择测试模式
    print("请选择测试模式：")
    print("  1. 日报模式（过去26小时内的视频）")
    print("  2. 周报模式（过去7天内的视频）")
    print()
    
    while True:
        choice = input("请输入选项 (1 或 2，直接回车默认选择1): ").strip()
        if not choice:
            choice = "1"
        if choice in ["1", "2"]:
            break
        print("❌ 无效选项，请输入 1 或 2")
    
    current_timestamp = time.time()
    
    if choice == "2":
        print("\n✅ 使用【周报模式】测试...")
        config = {
            "title": "UGC监控周报 (Past 7 Days) [测试]",
            "window": 7 * 24 * 3600,  # 7天
            "now": current_timestamp
        }
    else:
        print("\n✅ 使用【日报模式】测试...")
        config = {
            "title": "UGC监控日报 [测试]",
            "window": 26 * 3600,  # 26小时
            "now": current_timestamp
        }
    
    # 导入配置信息
    from up_list import UP_LIST, NO_FILTER_UIDS, KEYWORDS
    
    print(f"⏰ 时间窗口: {config['window'] / 3600:.1f} 小时")
    print(f"📅 监控 {len(TARGET_UIDS)} 个B站UP主")
    if YOUTUBE_CHANNELS:
        print(f"📺 监控 {len(YOUTUBE_CHANNELS)} 个YouTube频道")
    print(f"⚙️  并发限制: {CONCURRENCY_LIMIT}")
    
    # 显示配置信息 - B站UP主
    print(f"\n📋 B站UP主列表:")
    for uid in TARGET_UIDS:
        # 使用UP_NAME_MAP获取UP主名字，支持NO_FILTER_UIDS中的UP主
        up_name = UP_NAME_MAP.get(uid, f"UID_{uid}")
        is_special = uid in NO_FILTER_UIDS
        status = "⭐ 特殊（不过滤关键词）" if is_special else f"🔍 关键词: {', '.join(KEYWORDS)}"
        print(f"   - {up_name} (UID: {uid}) - {status}")
    
    # 显示配置信息 - YouTube频道
    if YOUTUBE_CHANNELS:
        print(f"\n📺 YouTube频道列表:")
        for channel_id in YOUTUBE_CHANNELS.keys():
            channel_name = YOUTUBE_CHANNELS.get(channel_id, f"Channel_{channel_id}")
            is_special = channel_id in YOUTUBE_NO_FILTER_CHANNELS
            status = "⭐ 特殊（不过滤关键词）" if is_special else f"🔍 关键词: {', '.join(KEYWORDS)}"
            print(f"   - {channel_name} (ID: {channel_id}) - {status}")
    print()
    
    # 2. 并发获取视频
    print("开始抓取视频...\n")
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    
    valid_videos = []
    success_count = 0
    fail_count = 0
    total_videos = 0
    skipped_by_time = 0
    skipped_by_keyword = 0
    skipped_by_history = 0
    
    # 2.1 获取B站视频
    if TARGET_UIDS:
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
            current_uid = TARGET_UIDS[i]
            # 使用UP_NAME_MAP获取UP主名字，支持NO_FILTER_UIDS中的UP主
            up_name = UP_NAME_MAP.get(current_uid, f"UID_{current_uid}")
            is_special = current_uid in NO_FILTER_UIDS
            
            print(f"\n📋 B站UP主: {up_name} (UID: {current_uid})")
            if is_special:
                print(f"   ⭐ 特殊UP主：跳过关键词过滤")
            else:
                print(f"   🔍 关键词过滤：{', '.join(KEYWORDS)}")
            
            print(f"   获取到 {len(result)} 个视频")
            
            for v in result:
                bvid = v['bvid']
                total_videos += 1
                
                # 记忆去重
                if test_memory.is_processed(bvid):
                    skipped_by_history += 1
                    continue
                
                # 检查时间过滤
                video_time = v['created']
                time_diff = config['now'] - video_time
                if time_diff > config['window']:
                    hours_ago = time_diff / 3600
                    skipped_by_time += 1
                    continue
                
                # 过滤判断
                if await filter_content(v, config, up_uid=current_uid, platform='bilibili'):
                    time_str = time.strftime("%m-%d %H:%M", time.localtime(video_time))
                    print(f"   ✅ 发现新视频 [{time_str}]: {v['title']}")
                    valid_videos.append(v)
                    test_memory.add(bvid)
                else:
                    # 如果不匹配，说明关键词过滤失败（特殊UP主不会走到这里）
                    skipped_by_keyword += 1
    
    # 2.2 获取YouTube视频
    youtube_channel_ids = list(YOUTUBE_CHANNELS.keys()) if YOUTUBE_CHANNELS else []
    if youtube_channel_ids:
        youtube_tasks = [fetch_youtube_videos(channel_id, semaphore) for channel_id in youtube_channel_ids]
        youtube_results = await asyncio.gather(*youtube_tasks, return_exceptions=True)
        
        for i, result in enumerate(youtube_results):
            channel_id = youtube_channel_ids[i]
            channel_name = YOUTUBE_CHANNELS.get(channel_id, f"Channel_{channel_id}")
            
            if isinstance(result, Exception):
                fail_count += 1
                print(f"❌ YouTube 频道 {channel_id} 获取异常: {result}")
                continue
            
            if not result:
                fail_count += 1
                continue
            
            success_count += 1
            is_special = channel_id in YOUTUBE_NO_FILTER_CHANNELS
            
            print(f"\n📺 YouTube频道: {channel_name} (ID: {channel_id})")
            if is_special:
                print(f"   ⭐ 特殊频道：跳过关键词过滤")
            else:
                print(f"   🔍 关键词过滤：{', '.join(KEYWORDS)}")
            
            print(f"   获取到 {len(result)} 个视频")
            
            for v in result:
                video_id = v['video_id']
                total_videos += 1
                
                # 记忆去重（注意：is_processed 检查的是存储的格式 "yt:video_id"）
                youtube_key = f"yt:{video_id}"
                if test_memory.is_processed(youtube_key):
                    skipped_by_history += 1
                    continue
                
                # 检查时间过滤
                video_time = v['created']
                time_diff = config['now'] - video_time
                if time_diff > config['window']:
                    hours_ago = time_diff / 3600
                    skipped_by_time += 1
                    continue
                
                # 过滤判断
                if await filter_content(v, config, up_uid=channel_id, platform='youtube'):
                    time_str = time.strftime("%m-%d %H:%M", time.localtime(video_time))
                    print(f"   ✅ 发现新视频 [{time_str}]: {v['title']}")
                    valid_videos.append(v)
                    test_memory.add(video_id, platform='youtube')
                else:
                    skipped_by_keyword += 1
    
    print(f"\n📊 监控统计：")
    print(f"   ✅ 成功抓取: {success_count} 个频道/UP主")
    print(f"   ❌ 失败: {fail_count} 个频道/UP主")
    print(f"   📹 总视频数: {total_videos} 个")
    print(f"   ⏰ 时间窗口外: {skipped_by_time} 个")
    print(f"   🔍 关键词不匹配: {skipped_by_keyword} 个")
    print(f"   💾 已在历史记录: {skipped_by_history} 个")
    print(f"   🎯 符合条件的视频: {len(valid_videos)} 条\n")
    
    # 4. 生成并显示报告
    if valid_videos:
        # 按发布时间倒序排列（新的在前）
        valid_videos.sort(key=lambda x: x['created'], reverse=True)
        
        msg = "<ul>"
        for v in valid_videos:
            time_str = time.strftime("%m-%d %H:%M", time.localtime(v['created']))
            platform = v.get('platform', 'bilibili')
            author = v.get('author', 'Unknown')
            
            if platform == 'youtube':
                video_id = v.get('video_id', '')
                url = f"https://www.youtube.com/watch?v={video_id}"
            else:
                bvid = v.get('bvid', '')
                url = f"https://www.bilibili.com/video/{bvid}"
            
            platform_tag = "📺" if platform == 'youtube' else "📱"
            msg += f"<li style='margin-bottom:8px'>[{time_str}] {platform_tag} <b>{author}</b>: <a href='{url}'>{v['title']}</a></li>"
        msg += "</ul>"
        
        # 发送通知（测试模式或真实模式）
        if SEND_REAL_EMAIL:
            from main import send_notification
            success = await send_notification(msg, config['title'])
            if success:
                print(f"✅ 邮件发送成功！共 {len(valid_videos)} 条\n")
            else:
                print(f"❌ 邮件发送失败！请查看上方错误信息\n")
        else:
            await test_send_notification(msg, config['title'])
        
        # 保存记忆（如果使用真实历史记录）
        if USE_REAL_HISTORY:
            test_memory.save_and_clean()
        else:
            # 测试模式：询问是否保存
            print(f"💾 测试模式使用临时文件: history_test.json")
            print(f"   （不会影响真实的 history.json）\n")
    else:
        print("ℹ️  没有符合条件的新视频。")
        print("   可能原因：")
        print("   - 时间窗口内没有新视频")
        print("   - 视频不符合关键词过滤条件")
        print("   - 视频已经在历史记录中\n")
    
    print("="*70)
    print("✅ 测试完成！")
    print("="*70 + "\n")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断测试")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

