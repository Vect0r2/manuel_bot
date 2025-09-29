import discord
import asyncio
import time
from datetime import datetime
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
            "purge_channels": {},  # {channel_id: interval_minutes}
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
        if time_minutes < 1:
            await ctx.send("Time must be at least 1 minute.")
            return
            
        guild_config = self.config.guild(ctx.guild)
        purge_channels = await guild_config.purge_channels()
        
        # Stop existing task if any
        if channel.id in self.purge_tasks:
            self.purge_tasks[channel.id].cancel()
            del self.purge_tasks[channel.id]
        
        # Remove old countdown message if exists
        await self._remove_countdown_message(channel)
        
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
        """Stop automatic purging for a channel"""
        guild_config = self.config.guild(ctx.guild)
        purge_channels = await guild_config.purge_channels()
        
        # Remove from config
        if str(channel.id) in purge_channels:
            del purge_channels[str(channel.id)]
            await guild_config.purge_channels.set(purge_channels)
        
        # Stop task
        if channel.id in self.purge_tasks:
            self.purge_tasks[channel.id].cancel()
            del self.purge_tasks[channel.id]
        
        # Remove countdown message
        await self._remove_countdown_message(channel)
            
        await ctx.send(f"Auto-purge stopped for {channel.mention}.")

    @commands.command(name="ventrepo")
    async def vent_repo(self, ctx):
        """Get the repository link for this cog"""
        repo_url = "https://github.com/Vect0r2/manuel_bot"
        embed = discord.Embed(
            title="VentControl Repository",
            description=repo_url,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Version {__version__} by {__author__}")
        await ctx.send(embed=embed)

    async def _create_countdown_message(self, channel: discord.TextChannel, next_purge_time: float):
        """Create a pinned message showing when next purge will occur"""
        next_purge = datetime.fromtimestamp(next_purge_time)
        
        embed = discord.Embed(
            title="Auto-Purge Active",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Next Purge",
            value=f"<t:{int(next_purge_time)}:F>",
            inline=False
        )
        embed.add_field(
            name="Countdown",
            value=f"<t:{int(next_purge_time)}:R>",
            inline=False
        )
        
        try:
            message = await channel.send(embed=embed)
            await message.pin()
            
            # Store message ID
            guild_config = self.config.guild(channel.guild)
            countdown_messages = await guild_config.countdown_messages()
            countdown_messages[str(channel.id)] = message.id
            await guild_config.countdown_messages.set(countdown_messages)
            
        except discord.Forbidden:
            # No permission to pin
            pass

    async def _remove_countdown_message(self, channel: discord.TextChannel):
        """Remove the pinned countdown message"""
        guild_config = self.config.guild(channel.guild)
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

    async def _purge_loop(self, channel: discord.TextChannel, interval_minutes: int):
        """Background task that purges messages at intervals"""
        while True:
            try:
                # Calculate next purge time
                next_purge_time = time.time() + (interval_minutes * 60)
                
                # Create pinned countdown message
                await self._create_countdown_message(channel, next_purge_time)
                
                # Wait for the interval
                await asyncio.sleep(interval_minutes * 60)
                
                # Check if we still have permissions
                if not channel.permissions_for(channel.guild.me).manage_messages:
                    break
                
                # Purge all messages (including the countdown message)
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