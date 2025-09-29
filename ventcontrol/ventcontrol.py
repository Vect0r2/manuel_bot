import discord
import asyncio
from redbot.core import commands, Config
from typing import Dict

__version__ = "1.0.0"
__author__ = "Vect0r"

class ventcontrol(commands.Cog):
    """Simple channel message purging cog"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=6782154929, force_registration=True)
        
        default_guild = {
    "purge_channels": {},
    "next_purge_times": {},  # {channel_id: timestamp}
    "countdown_messages": {}  # {channel_id: message_id}
        }
        self.config.register_guild(**default_guild)
        
        self.purge_tasks: Dict[int, asyncio.Task] = {}

    def cog_unload(self):
        """Clean up tasks when cog is unloaded"""
        for task in self.purge_tasks.values():
            if not task.done():
                task.cancel()

    @commands.command(name="purgeconfig")
    @commands.guild_only()
    @commands.is_owner()
    async def purge_config(self, ctx, channel: discord.TextChannel, time_minutes: int):
        """Set up automatic message purging for a channel
        
        Usage: !purgeconfig #channel 30
        This will delete all messages from the channel every 30 minutes
        """
            
        guild_config = self.config.guild(ctx.guild)
        purge_channels = await guild_config.purge_channels()
        
        # Stop existing task if any
        if channel.id in self.purge_tasks:
            self.purge_tasks[channel.id].cancel()
            del self.purge_tasks[channel.id]
        
        # Save config
        purge_channels[str(channel.id)] = time_minutes
        await guild_config.purge_channels.set(purge_channels)
        
        # Start new purge task
        task = asyncio.create_task(self._purge_loop(channel, time_minutes))
        self.purge_tasks[channel.id] = task
        
        await ctx.send(f"Auto-purge set for {channel.mention} every {time_minutes} minutes.")

    @commands.command(name="stoppurge")
    @commands.guild_only()
    @commands.is_owner()
    async def stop_purge(self, ctx, channel: discord.TextChannel):
        # ... existing code ...
        
        # Remove countdown message
        guild_config = self.config.guild(ctx.guild)
        countdown_messages = await guild_config.countdown_messages()
        
        if str(channel.id) in countdown_messages:
            try:
                message_id = countdown_messages[str(channel.id)]
                message = await channel.fetch_message(message_id)
                await message.delete()
            except discord.NotFound:
                pass
            
            del countdown_messages[str(channel.id)]
            await guild_config.countdown_messages.set(countdown_messages)

    @commands.command(name="ventrepo")
    async def vent_repo(self, ctx):
        """Get the repository link for this cog"""
        repo_url = "https://github.com/Vect0r2/manuel_bot"
        embed = discord.Embed(
            title="VentControl Repository",
            description="check out the repo here: %s" % repo_url,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Version {__version__} by {__author__}")
        await ctx.send(embed=embed)

    async def _purge_loop(self, channel: discord.TextChannel, interval_minutes: int):
        """Background task that purges messages at intervals"""
        import time
        
        while True:
            try:
                # Calculate next purge time
                next_purge_time = time.time() + (interval_minutes * 60)
                
                # Store next purge time
                guild_config = self.config.guild(channel.guild)
                next_purge_times = await guild_config.next_purge_times()
                next_purge_times[str(channel.id)] = next_purge_time
                await guild_config.next_purge_times.set(next_purge_times)
                
                # Create initial countdown message
                await self._update_countdown_message(channel, next_purge_time)
                
                # Update countdown every minute
                for i in range(interval_minutes):
                    await asyncio.sleep(60)  # Wait 1 minute
                    current_time = time.time()
                    if current_time < next_purge_time:  # Still time left
                        await self._update_countdown_message(channel, next_purge_time)
                
                # Check permissions before purging
                if not channel.permissions_for(channel.guild.me).manage_messages:
                    break
                
                # Purge all messages (including the countdown)
                await channel.purge(limit=None)
                
            except discord.HTTPException:
                await asyncio.sleep(60)
                continue
            except asyncio.CancelledError:
                break

        @commands.Cog.listener()
        async def on_ready(self):
            """Restore purge tasks when bot starts"""
            await self.bot.wait_until_ready()
            
            for guild in self.bot.guilds:
                guild_config = self.config.guild(guild)
                purge_channels = await guild_config.purge_channels()
                
                for channel_id_str, interval in purge_channels.items():
                    channel_id = int(channel_id_str)
                    channel = guild.get_channel(channel_id)
                    
                    if channel and channel.permissions_for(guild.me).manage_messages:
                        # Start purge task
                        task = asyncio.create_task(self._purge_loop(channel, interval))
                        self.purge_tasks[channel_id] = task

    async def _update_countdown_message(self, channel: discord.TextChannel, next_purge_time: float):
        """Create or update the pinned countdown message"""
        guild_config = self.config.guild(channel.guild)
        countdown_messages = await guild_config.countdown_messages()
        
        # Calculate time remaining
        import datetime
        next_purge = datetime.datetime.fromtimestamp(next_purge_time)
        now = datetime.datetime.now()
        time_left = next_purge - now
        
        # Format the message
        if time_left.total_seconds() > 0:
            minutes_left = int(time_left.total_seconds() / 60)
            hours_left = minutes_left // 60
            mins_left = minutes_left % 60
            
            if hours_left > 0:
                time_str = f"{hours_left}h {mins_left}m"
            else:
                time_str = f"{mins_left}m"
                
            embed = discord.Embed(
                title="Auto-Purge Active",
                description=f"Next message wipe in: **{time_str}**",
                color=discord.Color.orange()
            )
        try:
            message = await channel.send(embed=embed)
            await message.pin()
            
            # Store message ID
            countdown_messages[str(channel.id)] = message.id
            await guild_config.countdown_messages.set(countdown_messages)
        except discord.Forbidden:
            # No permission to pin
            pass