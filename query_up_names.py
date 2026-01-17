import asyncio
import os
from dotenv import load_dotenv
from bilibili_api import user
from up_list import TARGET_UIDS, NO_FILTER_UIDS, YOUTUBE_CHANNELS, YOUTUBE_NO_FILTER_CHANNELS
from googleapiclient.discovery import build

# 加载 .env 文件中的环境变量
load_dotenv()

async def get_user_info(uid, semaphore):
    """获取用户信息"""
    async with semaphore:
        try:
            import sys
            print(f"正在查询 UID {uid}...", flush=True)
            sys.stdout.flush()
            u = user.User(uid=uid)
            info = await u.get_user_info()
            name = info.get('name', '未知')
            print(f"✓ UID {uid} -> {name}", flush=True)
            return {
                'uid': uid,
                'name': name,
                'success': True
            }
        except Exception as e:
            return {
                'uid': uid,
                'name': f'查询失败: {str(e)}',
                'success': False
            }

async def get_youtube_channel_info(channel_id, semaphore):
    """获取YouTube频道信息"""
    async with semaphore:
        try:
            import sys
            youtube_api_key = os.environ.get("YOUTUBE_API_KEY")
            if not youtube_api_key:
                return {
                    'uid': channel_id,
                    'name': '查询失败: YOUTUBE_API_KEY未设置',
                    'success': False
                }
            
            print(f"正在查询 YouTube 频道 {channel_id}...", flush=True)
            sys.stdout.flush()
            
            # 使用 asyncio.to_thread 包装同步的 YouTube API 调用
            def get_channel_sync():
                youtube = build('youtube', 'v3', developerKey=youtube_api_key)
                response = youtube.channels().list(
                    part='snippet',
                    id=channel_id
                ).execute()
                
                if not response.get('items'):
                    return None
                
                channel_info = response['items'][0]
                channel_name = channel_info['snippet']['title']
                return channel_name
            
            name = await asyncio.to_thread(get_channel_sync)
            
            if name:
                print(f"✓ YouTube 频道 {channel_id} -> {name}", flush=True)
                return {
                    'uid': channel_id,
                    'name': name,
                    'success': True
                }
            else:
                return {
                    'uid': channel_id,
                    'name': '查询失败: 频道不存在',
                    'success': False
                }
                
        except Exception as e:
            return {
                'uid': channel_id,
                'name': f'查询失败: {str(e)}',
                'success': False
            }

async def main():
    import sys
    sys.stdout.flush()
    
    # 合并 TARGET_UIDS 和 NO_FILTER_UIDS，去重
    all_uids = list(set(TARGET_UIDS + NO_FILTER_UIDS))
    all_youtube_channels = list(set(list(YOUTUBE_CHANNELS.keys()) + YOUTUBE_NO_FILTER_CHANNELS))
    
    print(f"开始查询 {len(all_uids)} 个B站UP主的信息（包含 {len(TARGET_UIDS)} 个监控UP主和 {len(NO_FILTER_UIDS)} 个特殊UP主）...", flush=True)
    if all_youtube_channels:
        print(f"开始查询 {len(all_youtube_channels)} 个YouTube频道的信息...", flush=True)
    print("", flush=True)
    
    # 使用信号量控制并发，避免触发风控
    semaphore = asyncio.Semaphore(2)
    
    # 查询B站UP主
    bilibili_tasks = [get_user_info(uid, semaphore) for uid in all_uids]
    bilibili_results = await asyncio.gather(*bilibili_tasks)
    
    # 查询YouTube频道
    youtube_results = []
    if all_youtube_channels:
        youtube_tasks = [get_youtube_channel_info(channel_id, semaphore) for channel_id in all_youtube_channels]
        youtube_results = await asyncio.gather(*youtube_tasks)
    
    # 合并结果
    results = bilibili_results + youtube_results
    
    print("=" * 60, flush=True)
    print(f"{'UID':<20} {'UP主名字':<30}", flush=True)
    print("=" * 60, flush=True)
    
    for result in results:
        status = "✅" if result['success'] else "❌"
        print(f"{result['uid']:<20} {result['name']:<30} {status}", flush=True)
    
    print("=" * 60, flush=True)
    success_count = sum(1 for r in results if r['success'])
    fail_count = sum(1 for r in results if not r['success'])
    print(f"\n查询完成！成功: {success_count}, 失败: {fail_count}", flush=True)
    
    # 自动更新 up_list.py
    update_up_list_file(results, success_count)
    
    # 输出格式化的列表（可用于更新代码）
    print("\n" + "=" * 60, flush=True)
    print("格式化输出（可用于代码注释）：", flush=True)
    print("=" * 60, flush=True)
    for result in results:
        if result['success']:
            print(f"    {result['uid']},  # {result['name']}", flush=True)
        else:
            print(f"    {result['uid']},  # 查询失败", flush=True)

def update_up_list_file(results, success_count):
    """更新 up_list.py 文件"""
    try:
        # 分离 B站和 YouTube 的结果
        # 假设 UID 是数字，Channel ID 是以 'UC' 开头的字符串
        bilibili_results = []
        youtube_results = []
        
        for result in results:
            uid_or_channel = result['uid']
            # 简单判断：如果是字符串且以 'UC' 开头，或者是长数字字符串，视为 YouTube Channel ID
            # 否则视为 B站 UID
            if isinstance(uid_or_channel, str) and (uid_or_channel.startswith('UC') or len(str(uid_or_channel)) > 15):
                youtube_results.append(result)
            else:
                bilibili_results.append(result)
        
        # 构建新的 UP_LIST 字典（B站UP主）
        up_list_dict = {}
        for result in bilibili_results:
            uid = result['uid']
            if result['success']:
                up_list_dict[uid] = result['name']
            else:
                try:
                    from up_list import UP_LIST
                    if uid in UP_LIST:
                        up_list_dict[uid] = UP_LIST[uid]
                    else:
                        up_list_dict[uid] = "查询失败"
                except:
                    up_list_dict[uid] = "查询失败"
        
        # 构建新的 YOUTUBE_CHANNELS 字典
        youtube_channels_dict = {}
        for result in youtube_results:
            channel_id = result['uid']
            if result['success']:
                youtube_channels_dict[channel_id] = result['name']
            else:
                try:
                    from up_list import YOUTUBE_CHANNELS as existing_channels
                    if channel_id in existing_channels:
                        youtube_channels_dict[channel_id] = existing_channels[channel_id]
                    else:
                        youtube_channels_dict[channel_id] = "查询失败"
                except:
                    youtube_channels_dict[channel_id] = "查询失败"
        
        # 尝试从现有文件中读取配置，保留用户的配置
        try:
            from up_list import KEYWORDS, NO_FILTER_UIDS, YOUTUBE_NO_FILTER_CHANNELS
            keywords_list = KEYWORDS
            no_filter_list = NO_FILTER_UIDS
            youtube_no_filter_list = YOUTUBE_NO_FILTER_CHANNELS
        except:
            # 如果读取失败，使用默认值
            keywords_list = ["AIGC", "LoRA", "工作流", "模型"]
            no_filter_list = []
            youtube_no_filter_list = []
        
        # 使用当前 TARGET_UIDS（已在文件顶部导入）来确定哪些UID应该写入UP_LIST
        target_uids_set = set(TARGET_UIDS)
        
        # 生成新的文件内容
        file_content = '''"""
B站UP主和YouTube频道关注列表配置
包含需要监控的UP主/频道的ID和名字
"""

# UP主列表：{UID: UP主名字}
UP_LIST = {
'''
        # 只写入 TARGET_UIDS 中的 UID（按 UID 排序）
        target_uids_to_write = [uid for uid in sorted(up_list_dict.keys()) if uid in target_uids_set]
        for uid in target_uids_to_write:
            name = up_list_dict[uid]
            # 转义引号，防止名字中包含引号
            name_escaped = name.replace('"', '\\"').replace("'", "\\'")
            file_content += f'    {uid}: "{name_escaped}",\n'
        
        file_content += '''}

# 特殊UP主列表（这些UP主的视频不进行关键词过滤，直接推送）
# 如果某个UP主的所有视频都值得关注，可以将其UID添加到此列表中
NO_FILTER_UIDS = [
    # 示例：17280004,  # 蓝波球的球
    # 你可以在这里添加不需要关键词过滤的UP主UID
'''
        # 写入 NO_FILTER_UIDS（添加UP主名字注释）
        if no_filter_list:
            for uid in no_filter_list:
                # 优先从 up_list_dict 中查找对应的名字（最新查询结果）
                name = None
                if uid in up_list_dict:
                    name = up_list_dict[uid]
                else:
                    # 如果不在查询结果中，尝试从现有 UP_LIST 读取（可能不在 TARGET_UIDS 中）
                    try:
                        from up_list import UP_LIST as existing_up_list
                        if uid in existing_up_list:
                            name = existing_up_list[uid]
                    except:
                        pass
                
                # 如果有名字，添加注释；否则只写 UID
                if name and name != "查询失败":
                    # 转义引号，防止名字中包含引号
                    name_escaped = name.replace('"', '\\"').replace("'", "\\'")
                    file_content += f'    {uid},  # {name_escaped}\n'
                else:
                    # 如果没有找到名字，只写 UID（可能不在监控列表中或查询失败）
                    file_content += f'    {uid},\n'
        
        file_content += '''] 

# 提取所有UID列表（自动包含UP_LIST和NO_FILTER_UIDS中的所有UP主）
# 使用set去重，确保不重复监控
TARGET_UIDS = list(set(list(UP_LIST.keys()) + NO_FILTER_UIDS))

# YouTube频道列表：{Channel ID: 频道名字}
# Channel ID 格式：UCxxxxx（24个字符）
YOUTUBE_CHANNELS = {
'''
        # 写入 YOUTUBE_CHANNELS
        for channel_id in sorted(youtube_channels_dict.keys()):
            name = youtube_channels_dict[channel_id]
            name_escaped = name.replace('"', '\\"').replace("'", "\\'")
            file_content += f'    "{channel_id}": "{name_escaped}",\n'
        
        file_content += '''}

# YouTube特殊频道列表（这些频道的视频不进行关键词过滤，直接推送）
# 如果某个频道的所有视频都值得关注，可以将其Channel ID添加到此列表中
YOUTUBE_NO_FILTER_CHANNELS = [
    # 示例：'UCxxxxx',  # 频道名字
    # 你可以在这里添加不需要关键词过滤的YouTube频道ID
'''
        # 写入 YOUTUBE_NO_FILTER_CHANNELS（添加频道名字注释）
        if youtube_no_filter_list:
            for channel_id in youtube_no_filter_list:
                # 优先从 youtube_channels_dict 中查找对应的名字
                name = None
                if channel_id in youtube_channels_dict:
                    name = youtube_channels_dict[channel_id]
                else:
                    try:
                        from up_list import YOUTUBE_CHANNELS as existing_channels
                        if channel_id in existing_channels:
                            name = existing_channels[channel_id]
                    except:
                        pass
                
                # 如果有名字，添加注释；否则只写 Channel ID
                if name and name != "查询失败":
                    name_escaped = name.replace('"', '\\"').replace("'", "\\'")
                    file_content += f'    "{channel_id}",  # {name_escaped}\n'
                else:
                    file_content += f'    "{channel_id}",\n'
        
        file_content += ''']

# UP主名字映射（自动包含UP_LIST和NO_FILTER_UIDS中的所有UP主）
# 用于显示UP主名字，即使UP主不在UP_LIST中
# 此映射会自动从UP_LIST和查询结果中生成
# 现在也包含YouTube频道名字
UP_NAME_MAP = {
    **UP_LIST,  # 从UP_LIST中获取名字
    **YOUTUBE_CHANNELS,  # 从YOUTUBE_CHANNELS中获取名字
}

# 关键词过滤列表（用于视频内容过滤）
KEYWORDS = [
'''
        # 写入 KEYWORDS
        for kw in keywords_list:
            kw_escaped = kw.replace('"', '\\"').replace("'", "\\'")
            file_content += f'    "{kw_escaped}",\n'
        
        file_content += ']'
        
        # 写入文件
        with open('up_list.py', 'w', encoding='utf-8') as f:
            f.write(file_content)
        
        print(f"\n✅ 已自动更新 up_list.py 文件", flush=True)
        print(f"   共更新 {success_count} 个UP主名字", flush=True)
        print(f"   已保留 KEYWORDS ({len(keywords_list)} 个) 和 NO_FILTER_UIDS ({len(no_filter_list)} 个) 配置", flush=True)
        
    except Exception as e:
        print(f"\n❌ 更新 up_list.py 失败: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())