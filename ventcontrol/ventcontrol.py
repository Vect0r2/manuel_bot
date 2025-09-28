import discord
import asyncio
import logging
from datetime import datetime, timedelta
from redbot.core import commands, Config, checks, app_commands
from redbot.core.utils.chat_formatting import box, humanize_timedelta
from typing import Optional, Dict, Any
import time

__version__ = "1.0.0"
__author__ = "Vect0r"

log = logging.getLogger("red.ventilationcontrol")

BaseCog = getattr(commands, "Cog", object)

class ventcontrol(BaseCog):
    """
    Automated channel message purging for #ventilation
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=6782154929, force_registration=True)

        default_guild = {
            "purge_channels": {},  # {channel_id: {"interval": minutes, "enabled": bool, "last_purge": timestamp}}
        }
        self.config.register_guild(**default_guild)

        self.purge_tasks: Dict[int, asyncio.Task] = {}

        self.bot.loop.create_task(self.initialize_purge_cycles())
    
    def cog_unload(self):
        """Clean up tasks when cog is unloaded"""
        for task in self.purge_tasks.values():
            if not task.done():
                task.cancel()

    async def initialize_purge_cycles(self):
        """Initialize purge cycles for all configured channels on bot startup"""
        await self.bot.wait_until_ready()
        
        for guild in self.bot.guilds:
            try:
                guild_config = await self.config.guild(guild).purge_channels()
                
                for channel_id_str, settings in guild_config.items():
                    if settings.get("enabled", False):
                        channel_id = int(channel_id_str)
                        channel = guild.get_channel(channel_id)
                        
                        if channel:
                            await self.start_purge_cycle(channel, settings["interval"])
                            log.info(f"Resumed purge cycle for #{channel.name} in {guild.name}")
                        else:
                            # Channel doesn't exist anymore, remove from config
                            async with self.config.guild(guild).purge_channels() as channels:
                                if channel_id_str in channels:
                                    del channels[channel_id_str]
                            log.warning(f"Removed non-existent channel {channel_id} from config in {guild.name}")
            except Exception as e:
                log.error(f"Error initializing purge cycles for guild {guild.name}: {e}")