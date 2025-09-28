from .ventcontrol import ventcontrol

async def setup(bot):
    await bot.add_cog(ventcontrol(bot))