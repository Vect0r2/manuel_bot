import discord
import asyncio
import random
import aiohttp
import re
import json
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify, box

class VidChoose(commands.Cog):
    """YouTube Video Selector with Weighted Random Selection"""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8675309)
        
        default_global = {
            "youtube_api_key": None,
            "update_interval": 60
        }
        
        default_guild = {
            "post_channel": None,
            "post_interval": 30,
            "channel_history": 5,
            "video_history": 10,
            "last_channels": [],
            "last_videos": [],
            "channels": {},
            "videos": {},
            "last_post_time": 0,
            "enabled": True,
            "shorts_enabled": False
        }
        
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        
        self.task = self.bot.loop.create_task(self._post_loop())
    
    def cog_unload(self):
        self.task.cancel()
    
    # ====================
    # YOUTUBE API METHODS
    # ====================
    
    async def _extract_channel_id(self, url_or_id: str) -> Optional[str]:
        """Extract channel ID from various URL formats"""
        # Check if already a valid channel ID (starts with UC and is 24 chars)
        if re.match(r"^UC[a-zA-Z0-9_-]{22}$", url_or_id):
            return url_or_id
        
        # Check for channel URL
        channel_match = re.search(r"youtube\.com/channel/([a-zA-Z0-9_-]+)", url_or_id, re.IGNORECASE)
        if channel_match:
            return channel_match.group(1)
        
        # Check for handle (@username)
        handle_match = re.search(r"youtube\.com/@([a-zA-Z0-9_-]+)", url_or_id, re.IGNORECASE)
        if handle_match:
            handle = handle_match.group(1)
            # Resolve handle to channel ID using API
            return await self._resolve_handle_to_channel_id(handle)
        
        # Check for custom URL (/c/ or /user/)
        custom_match = re.search(r"youtube\.com/(?:c|user)/([a-zA-Z0-9_-]+)", url_or_id, re.IGNORECASE)
        if custom_match:
            custom_name = custom_match.group(1)
            # Try to resolve custom URL to channel ID
            return await self._resolve_custom_url_to_channel_id(custom_name)
        
        # If it's just a plain string, try to resolve it
        if re.match(r"^[a-zA-Z0-9_-]+$", url_or_id):
            # Could be a handle, username, or custom URL - try to resolve
            return await self._resolve_custom_url_to_channel_id(url_or_id)
        
        return None
    
    async def _resolve_handle_to_channel_id(self, handle: str) -> Optional[str]:
        """Resolve a YouTube handle (@username) to channel ID"""
        api_key = await self.config.youtube_api_key()
        if not api_key:
            return None
        
        # YouTube API search by handle
        url = (
            f"https://www.googleapis.com/youtube/v3/channels"
            f"?part=id"
            f"&forHandle={handle}"
            f"&key={api_key}"
        )
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    if data.get("items"):
                        return data["items"][0]["id"]
        except Exception:
            pass
        
        return None
    
    async def _resolve_custom_url_to_channel_id(self, custom_url: str) -> Optional[str]:
        """Resolve a custom URL or username to channel ID via search"""
        api_key = await self.config.youtube_api_key()
        if not api_key:
            return None
        
        # Try search API
        url = (
            f"https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet"
            f"&type=channel"
            f"&q={custom_url}"
            f"&maxResults=1"
            f"&key={api_key}"
        )
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    if data.get("items"):
                        return data["items"][0]["snippet"]["channelId"]
        except Exception:
            pass
        
        return None
    
    async def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL"""
        patterns = [
            r"youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
            r"youtu\.be/([a-zA-Z0-9_-]{11})",
            r"youtube\.com/embed/([a-zA-Z0-9_-]{11})",
            r"youtube\.com/v/([a-zA-Z0-9_-]{11})"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    async def _fetch_channel_info(self, channel_id: str) -> Optional[Dict]:
        """Fetch channel name and uploads playlist ID"""
        api_key = await self.config.youtube_api_key()
        if not api_key:
            return None
        
        url = (
            f"https://www.googleapis.com/youtube/v3/channels"
            f"?part=snippet,contentDetails"
            f"&id={channel_id}"
            f"&key={api_key}"
        )
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    
                    if not data.get("items"):
                        return None
                    
                    item = data["items"][0]
                    return {
                        "name": item["snippet"]["title"],
                        "uploads_playlist": item["contentDetails"]["relatedPlaylists"]["uploads"]
                    }
        except Exception:
            return None
    
    async def _fetch_channel_videos(self, playlist_id: str, max_results: int = 50, guild_id: int = None) -> List[str]:
        """Fetch videos from a playlist (channel uploads), optionally filtering shorts"""
        api_key = await self.config.youtube_api_key()
        if not api_key:
            return []
        
        # Check if shorts are enabled for this guild
        shorts_enabled = True
        if guild_id:
            shorts_enabled = await self.config.guild_from_id(guild_id).shorts_enabled()
        
        video_ids = []
        page_token = None
        
        try:
            async with aiohttp.ClientSession() as session:
                while len(video_ids) < max_results:
                    url = (
                        f"https://www.googleapis.com/youtube/v3/playlistItems"
                        f"?part=contentDetails"
                        f"&playlistId={playlist_id}"
                        f"&maxResults=50"
                        f"&key={api_key}"
                    )
                    
                    if page_token:
                        url += f"&pageToken={page_token}"
                    
                    async with session.get(url) as response:
                        if response.status != 200:
                            break
                        
                        data = await response.json()
                        
                        for item in data.get("items", []):
                            video_id = item["contentDetails"]["videoId"]
                            
                            # If shorts not enabled, check if this is a short
                            if not shorts_enabled:
                                video_info = await self._fetch_video_info(video_id)
                                if video_info and self._is_short(video_info["duration"]):
                                    continue  # Skip this short
                            
                            video_ids.append(video_id)
                        
                        page_token = data.get("nextPageToken")
                        if not page_token:
                            break
        except Exception:
            return []
        
        return video_ids[:max_results]
    
    async def _fetch_video_info(self, video_id: str) -> Optional[Dict]:
        """Fetch video title, channel ID, and duration"""
        api_key = await self.config.youtube_api_key()
        if not api_key:
            return None
        
        url = (
            f"https://www.googleapis.com/youtube/v3/videos"
            f"?part=snippet,contentDetails"
            f"&id={video_id}"
            f"&key={api_key}"
        )
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    
                    if not data.get("items"):
                        return None
                    
                    item = data["items"][0]
                    snippet = item["snippet"]
                    duration = item["contentDetails"]["duration"]
                    
                    return {
                        "title": snippet["title"],
                        "channel_id": snippet["channelId"],
                        "channel_title": snippet.get("channelTitle", "Unknown"),
                        "duration": duration
                    }
        except Exception:
            return None
    
    def _is_short(self, duration_iso: str) -> bool:
        """Check if video is a YouTube Short (duration <= 60 seconds)"""
        try:
            # Parse ISO 8601 duration format (e.g., PT1M30S = 1 minute 30 seconds)
            import re
            match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_iso)
            if not match:
                return False
            
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2) or 0)
            seconds = int(match.group(3) or 0)
            
            total_seconds = hours * 3600 + minutes * 60 + seconds
            
            # YouTube Shorts are 60 seconds or less
            return total_seconds <= 60
        except Exception:
            return False
    
    # ====================
    # WEIGHTED SELECTION
    # ====================
    
    async def _weighted_choice(self, guild_id: int) -> Optional[str]:
        """Choose a channel based on weights"""
        channels = await self.config.guild_from_id(guild_id).channels()
        
        if not channels:
            return None
        
        # Filter out channels with valid videos
        valid_channels = {}
        for cid, data in channels.items():
            if data.get("video_ids") and len(data["video_ids"]) > 0:
                weight = data.get("weight", 1.0)
                if weight > 0:
                    valid_channels[cid] = weight
        
        if not valid_channels:
            return None
        
        total = sum(valid_channels.values())
        if total <= 0:
            return random.choice(list(valid_channels.keys()))
        
        # Weighted random selection
        rand = random.uniform(0, total)
        cumulative = 0
        
        for cid, weight in valid_channels.items():
            cumulative += weight
            if rand <= cumulative:
                return cid
        
        return list(valid_channels.keys())[-1]
    
    async def _select_video_from_channel(self, guild_id: int, channel_id: str) -> Optional[str]:
        """Select a video from channel, avoiding recent history"""
        channels = await self.config.guild_from_id(guild_id).channels()
        
        if channel_id not in channels:
            return None
        
        video_ids = channels[channel_id].get("video_ids", [])
        if not video_ids:
            return None
        
        video_history = await self.config.guild_from_id(guild_id).last_videos()
        
        # Filter out recent videos
        available_videos = [vid for vid in video_ids if vid not in video_history]
        
        if not available_videos:
            available_videos = video_ids
        
        return random.choice(available_videos)
    
    # ====================
    # HISTORY MANAGEMENT
    # ====================
    
    async def _update_history(self, guild_id: int, channel_id: str, video_id: str):
        """Update history buffers"""
        guild_config = self.config.guild_from_id(guild_id)
        
        channel_history = await guild_config.last_channels()
        video_history = await guild_config.last_videos()
        
        max_channels = await guild_config.channel_history()
        max_videos = await guild_config.video_history()
        
        # Add to beginning of list
        channel_history.insert(0, channel_id)
        video_history.insert(0, video_id)
        
        # Trim to max size
        if len(channel_history) > max_channels:
            channel_history = channel_history[:max_channels]
        if len(video_history) > max_videos:
            video_history = video_history[:max_videos]
        
        await guild_config.last_channels.set(channel_history)
        await guild_config.last_videos.set(video_history)
    
    # ====================
    # POSTING LOOP
    # ====================
    
    async def _post_loop(self):
        """Main posting loop"""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                await self._process_all_guilds()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in post loop: {e}")
                await asyncio.sleep(60)
    
    async def _process_all_guilds(self):
        """Process all guilds"""
        for guild in self.bot.guilds:
            try:
                await self._maybe_post_video(guild)
            except Exception as e:
                print(f"Error processing guild {guild.id}: {e}")
    
    async def _maybe_post_video(self, guild: discord.Guild):
        """Check if it's time to post and post a video"""
        guild_config = self.config.guild(guild)
        
        # Check if enabled
        enabled = await guild_config.enabled()
        if not enabled:
            return
        
        post_channel_id = await guild_config.post_channel()
        if not post_channel_id:
            return
        
        post_interval = await guild_config.post_interval()
        last_post = await guild_config.last_post_time()
        
        current_time = datetime.utcnow().timestamp()
        
        if current_time - last_post < (post_interval * 60):
            return
        
        # Select channel
        channel_id = await self._weighted_choice(guild.id)
        if not channel_id:
            return
        
        # Select video
        video_id = await self._select_video_from_channel(guild.id, channel_id)
        if not video_id:
            return
        
        # Get video info for nice display
        video_info = await self._fetch_video_info(video_id)
        channels = await guild_config.channels()
        channel_name = channels.get(channel_id, {}).get("name", "Unknown Channel")
        
        # Post the video
        post_channel = guild.get_channel(post_channel_id)
        if post_channel:
            # Send just the URL so Discord shows the video preview
            await post_channel.send(f"https://www.youtube.com/watch?v={video_id}")
            
            # Update history and timestamp
            await self._update_history(guild.id, channel_id, video_id)
            await guild_config.last_post_time.set(current_time)
    
    # ====================
    # USER COMMANDS
    # ====================
    
    @commands.group()
    @commands.guild_only()
    async def vidchoose(self, ctx):
        """YouTube Video Selection System"""
        pass
    
    @vidchoose.command(name="setapi")
    @commands.is_owner()
    async def vidchoose_setapi(self, ctx, api_key: str):
        """Set YouTube API key (bot owner only)"""
        await self.config.youtube_api_key.set(api_key)
        await ctx.send("YouTube API key set!")
    
    @vidchoose.command(name="addchannel")
    async def vidchoose_addchannel(self, ctx, channel_url: str, weight: float = 1.0):
        """Add a YouTube channel and all its videos"""
        channel_id = await self._extract_channel_id(channel_url)
        if not channel_id:
            await ctx.send("Could not extract channel ID from URL")
            return
        
        async with ctx.typing():
            try:
                # Get channel info
                channel_info = await self._fetch_channel_info(channel_id)
                if not channel_info:
                    await ctx.send("Channel not found or API key not set")
                    return
                
                # Get videos (will filter shorts based on guild settings)
                videos = await self._fetch_channel_videos(channel_info["uploads_playlist"], 100, ctx.guild.id)
                if not videos:
                    await ctx.send("No videos found in channel (or all videos are shorts and shorts are disabled)")
                    return
                
                # Save channel
                async with self.config.guild(ctx.guild).channels() as channels:
                    channels[channel_id] = {
                        "name": channel_info["name"],
                        "weight": weight,
                        "video_ids": videos,
                        "last_updated": datetime.utcnow().isoformat()
                    }
                
                # Save videos
                async with self.config.guild(ctx.guild).videos() as video_data:
                    for video_id in videos:
                        if video_id not in video_data:
                            video_data[video_id] = {
                                "channel_id": channel_id,
                                "added_by": ctx.author.id
                            }
                
                embed = discord.Embed(
                    title=" Channel Added",
                    description=f"**{channel_info['name']}**",
                    color=discord.Color.green()
                )
                embed.add_field(name="Videos Added", value=str(len(videos)), inline=True)
                embed.add_field(name="Weight", value=str(weight), inline=True)
                embed.add_field(name="Channel ID", value=channel_id, inline=False)
                
                await ctx.send(embed=embed)
                
            except Exception as e:
                await ctx.send(f" Error: {str(e)[:1000]}")
    
    @vidchoose.command(name="addvideo")
    async def vidchoose_addvideo(self, ctx, video_url: str, weight: float = None):
        """Add a single video"""
        video_id = await self._extract_video_id(video_url)
        if not video_id:
            await ctx.send("Could not extract video ID from URL")
            return
        
        async with ctx.typing():
            try:
                video_info = await self._fetch_video_info(video_id)
                if not video_info:
                    await ctx.send("Video not found or API key not set")
                    return
                
                # Generate unique ID for single video "channel"
                single_channel_id = f"single_{video_id}"
                
                # Save video
                async with self.config.guild(ctx.guild).videos() as videos:
                    videos[video_id] = {
                        "title": video_info["title"],
                        "channel_id": video_info["channel_id"],
                        "added_by": ctx.author.id,
                        "is_single": True
                    }
                
                # Save as single-video channel
                async with self.config.guild(ctx.guild).channels() as channels:
                    channels[single_channel_id] = {
                        "name": f"Single: {video_info['title'][:50]}...",
                        "weight": weight if weight is not None else 1.0,
                        "video_ids": [video_id],
                        "last_updated": datetime.utcnow().isoformat(),
                        "is_single": True
                    }
                
                embed = discord.Embed(
                    title=" Video Added",
                    description=f"**{video_info['title']}**",
                    color=discord.Color.green()
                )
                embed.add_field(name="Channel", value=video_info["channel_title"], inline=True)
                if weight is not None:
                    embed.add_field(name="Weight", value=str(weight), inline=True)
                
                await ctx.send(embed=embed)
                
            except Exception as e:
                await ctx.send(f" Error: {str(e)[:1000]}")
    
    @vidchoose.command(name="weight")
    async def vidchoose_weight(self, ctx, identifier: str, weight: float):
        """Set weight for a channel/video"""
        guild_config = self.config.guild(ctx.guild)
        
        async with ctx.typing():
            channels = await guild_config.channels()
            
            # Try to find by channel ID first
            if identifier in channels:
                async with guild_config.channels() as ch_data:
                    ch_data[identifier]["weight"] = weight
                channel_name = channels[identifier].get("name", identifier)
                await ctx.send(f" Set weight for **{channel_name}** to **{weight}**")
                return
            
            # Try to find by video ID (single videos)
            videos = await guild_config.videos()
            if identifier in videos:
                # Find the single-video channel
                single_channel_id = f"single_{identifier}"
                if single_channel_id in channels:
                    async with guild_config.channels() as ch_data:
                        ch_data[single_channel_id]["weight"] = weight
                    video_title = videos[identifier].get("title", identifier)
                    await ctx.send(f" Set weight for video **{video_title[:50]}...** to **{weight}**")
                    return
            
            # Try to find by name
            for cid, data in channels.items():
                if data.get("name", "").lower() == identifier.lower():
                    async with guild_config.channels() as ch_data:
                        ch_data[cid]["weight"] = weight
                    await ctx.send(f"Set weight for **{data['name']}** to **{weight}**")
                    return
            
            await ctx.send("Channel/video not found")
    
    @vidchoose.command(name="setchannel")
    async def vidchoose_setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where videos will be posted"""
        await self.config.guild(ctx.guild).post_channel.set(channel.id)
        await ctx.send(f"Videos will be posted in {channel.mention}")
    
    @vidchoose.command(name="setinterval")
    async def vidchoose_setinterval(self, ctx, minutes: int):
        """Set minutes between automatic posts"""
        if minutes < 1:
            await ctx.send("Interval must be at least 1 minute")
            return
        
        await self.config.guild(ctx.guild).post_interval.set(minutes)
        await ctx.send(f"Post interval set to {minutes} minutes")
    
    @vidchoose.command(name="sethistory")
    async def vidchoose_sethistory(self, ctx, channels: int, videos: int):
        """Set how many channels/videos to remember"""
        if channels < 1 or videos < 1:
            await ctx.send("History sizes must be at least 1")
            return
        
        await self.config.guild(ctx.guild).channel_history.set(channels)
        await self.config.guild(ctx.guild).video_history.set(videos)
        await ctx.send(f"History: {channels} channels, {videos} videos")
    
    @vidchoose.command(name="list")
    async def vidchoose_list(self, ctx):
        """List all channels and their weights"""
        channels = await self.config.guild(ctx.guild).channels()
        
        if not channels:
            await ctx.send("No channels added yet")
            return
        
        embed = discord.Embed(
            title="Video Channels",
            color=discord.Color.blue()
        )
        
        for cid, data in channels.items():
            name = data.get("name", "Unknown")
            weight = data.get("weight", 1.0)
            video_count = len(data.get("video_ids", []))
            is_single = data.get("is_single", False)
            
            prefix = "Video:" if is_single else "Channel:"
            embed.add_field(
                name=f"{prefix} {name}",
                value=f"Weight: {weight} | Videos: {video_count}",
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    @vidchoose.command(name="remove")
    async def vidchoose_remove(self, ctx, identifier: str):
        """Remove a channel or video"""
        guild_config = self.config.guild(ctx.guild)
        
        async with ctx.typing():
            channels = await guild_config.channels()
            videos = await guild_config.videos()
            
            removed = False
            
            # Try to resolve identifier as a channel URL/handle/ID
            resolved_channel_id = await self._extract_channel_id(identifier)
            if resolved_channel_id and resolved_channel_id in channels:
                # delete channel entry
                async with guild_config.channels() as ch_data:
                    if resolved_channel_id in ch_data:
                        del ch_data[resolved_channel_id]
                # delete videos belonging to that channel
                async with guild_config.videos() as vid_data:
                    to_delete = [vid for vid, dat in vid_data.items() if dat.get("channel_id") == resolved_channel_id]
                    for vid in to_delete:
                        if vid in vid_data:
                            del vid_data[vid]
                removed = True

            # Direct key match for channel id (if user passed raw key)
            if not removed and identifier in channels:
                async with guild_config.channels() as ch_data:
                    del ch_data[identifier]
                removed = True

            # Direct video id removal
            if identifier in videos:
                async with guild_config.videos() as vid_data:
                    if identifier in vid_data:
                        del vid_data[identifier]
                removed = True

            # single_video channel id (user may pass raw video id)
            single_id = f"single_{identifier}"
            if not removed and single_id in channels:
                async with guild_config.channels() as ch_data:
                    del ch_data[single_id]
                removed = True

            # Try matching by stored channel name (case-insensitive)
            if not removed:
                for cid, data in channels.items():
                    name = data.get("name", "")
                    if name and name.lower() == identifier.lower():
                        async with guild_config.channels() as ch_data:
                            if cid in ch_data:
                                del ch_data[cid]
                        # also remove associated videos
                        async with guild_config.videos() as vid_data:
                            to_delete = [vid for vid, dat in vid_data.items() if dat.get("channel_id") == cid]
                            for vid in to_delete:
                                if vid in vid_data:
                                    del vid_data[vid]
                        removed = True
                        break

            if removed:
                await ctx.send("Removed successfully")
            else:
                await ctx.send("Channel/video not found")
    
    @vidchoose.command(name="force")
    async def vidchoose_force(self, ctx):
        """Force post a video now"""
        guild = ctx.guild
        guild_config = self.config.guild(guild)
        
        post_channel_id = await guild_config.post_channel()
        if not post_channel_id:
            await ctx.send("No posting channel set. Use `[p]vidchoose setchannel`")
            return
        
        async with ctx.typing():
            channel_id = await self._weighted_choice(guild.id)
            if not channel_id:
                await ctx.send("No channels available")
                return
            
            video_id = await self._select_video_from_channel(guild.id, channel_id)
            if not video_id:
                await ctx.send("No videos available")
                return
            
            post_channel = guild.get_channel(post_channel_id)
            if post_channel:
                # Send just the URL so Discord shows the video preview
                await post_channel.send(f"https://www.youtube.com/watch?v={video_id}")
                
                await self._update_history(guild.id, channel_id, video_id)
                await ctx.send("Video posted!")
    
    @vidchoose.command(name="enable")
    async def vidchoose_enable(self, ctx):
        """Enable automatic posting"""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Automatic posting enabled")
    
    @vidchoose.command(name="disable")
    async def vidchoose_disable(self, ctx):
        """Disable automatic posting"""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Automatic posting disabled")
    
    @vidchoose.command(name="shorts")
    async def vidchoose_shorts(self, ctx, enabled: bool):
        """Enable or disable YouTube Shorts
        
        Usage: 
        - !vidchoose shorts true  (include shorts)
        - !vidchoose shorts false (exclude shorts)
        """
        await self.config.guild(ctx.guild).shorts_enabled.set(enabled)
        if enabled:
            await ctx.send("YouTube Shorts are now **enabled** and will be included in video selection")
        else:
            await ctx.send("YouTube Shorts are now **disabled** and will be filtered out")
    
    @vidchoose.command(name="status")
    async def vidchoose_status(self, ctx):
        """Show current configuration"""
        guild_config = self.config.guild(ctx.guild)
        
        post_channel_id = await guild_config.post_channel()
        post_interval = await guild_config.post_interval()
        channel_history = await guild_config.channel_history()
        video_history = await guild_config.video_history()
        enabled = await guild_config.enabled()
        shorts_enabled = await guild_config.shorts_enabled()
        channels = await guild_config.channels()
        videos = await guild_config.videos()
        
        embed = discord.Embed(
            title="VidChoose Status",
            color=discord.Color.blue()
        )
        
        if post_channel_id:
            channel = ctx.guild.get_channel(post_channel_id)
            channel_name = channel.mention if channel else "Unknown Channel"
        else:
            channel_name = "Not set"
        
        embed.add_field(name="Posting Channel", value=channel_name, inline=True)
        embed.add_field(name="Post Interval", value=f"{post_interval} min", inline=True)
        embed.add_field(name="Auto Posting", value="Enabled" if enabled else "Disabled", inline=True)
        embed.add_field(name="Channel History", value=str(channel_history), inline=True)
        embed.add_field(name="Video History", value=str(video_history), inline=True)
        embed.add_field(name="YouTube Shorts", value="Enabled" if shorts_enabled else "Disabled", inline=True)
        embed.add_field(name="Total Channels", value=str(len(channels)), inline=True)
        embed.add_field(name="Total Videos", value=str(len(videos)), inline=True)
        
        # Next post time
        last_post = await guild_config.last_post_time()
        if last_post > 0:
            next_post = datetime.fromtimestamp(last_post + (post_interval * 60))
            embed.add_field(name="Next Post", value=next_post.strftime("%Y-%m-%d %H:%M"), inline=False)
        
        await ctx.send(embed=embed)
    
    @vidchoose.command(name="testweights")
    async def vidchoose_testweights(self, ctx, trials: int = 100):
        """Test weighted selection distribution"""
        if trials < 10 or trials > 1000:
            await ctx.send("Trials must be between 10 and 1000")
            return
        
        channels = await self.config.guild(ctx.guild).channels()
        
        if not channels:
            await ctx.send(" No channels configured")
            return
        
        counts = {cid: 0 for cid in channels.keys()}
        
        async with ctx.typing():
            for _ in range(trials):
                selected = await self._weighted_choice(ctx.guild.id)
                if selected:
                    counts[selected] += 1
            
            embed = discord.Embed(
                title=" Weight Test Results",
                description=f"{trials} trials",
                color=discord.Color.green()
            )
            
            for cid, count in counts.items():
                percentage = (count / trials) * 100
                weight = channels[cid].get("weight", 1.0)
                name = channels[cid].get("name", cid)[:50]
                
                embed.add_field(
                    name=name,
                    value=f"**{percentage:.1f}%** (Weight: {weight})",
                    inline=False
                )
            
            await ctx.send(embed=embed)
    
    @vidchoose.command(name="clearhistory")
    async def vidchoose_clearhistory(self, ctx):
        """Clear posting history"""
        await self.config.guild(ctx.guild).last_channels.set([])
        await self.config.guild(ctx.guild).last_videos.set([])
        await ctx.send(" History cleared")
    
    @vidchoose.command(name="update")
    async def vidchoose_update(self, ctx, channel_id: str = None):
        """Update videos for a channel (or all channels)"""
        guild_config = self.config.guild(ctx.guild)
        channels = await guild_config.channels()
        
        if not channels:
            await ctx.send(" No channels to update")
            return
        
        async with ctx.typing():
            updated = 0
            failed = 0
            
            if channel_id and channel_id in channels:
                # Update single channel
                channel_list = [channel_id]
            else:
                # Update all channels
                channel_list = list(channels.keys())
            
            for cid in channel_list:
                try:
                    # Skip single videos
                    if channels[cid].get("is_single", False):
                        continue
                    
                    channel_info = await self._fetch_channel_info(cid)
                    if not channel_info:
                        failed += 1
                        continue
                    
                    videos = await self._fetch_channel_videos(channel_info["uploads_playlist"], 100)
                    
                    async with guild_config.channels() as ch_data:
                        if cid in ch_data:
                            ch_data[cid]["video_ids"] = videos
                            ch_data[cid]["last_updated"] = datetime.utcnow().isoformat()
                            ch_data[cid]["name"] = channel_info["name"]
                    
                    updated += 1
                    
                except Exception:
                    failed += 1
            
            await ctx.send(f" Updated {updated} channel(s), failed: {failed}")

async def setup(bot):
    await bot.add_cog(VidChoose(bot))